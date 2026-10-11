"""Real distribution/resource checks, not whole-pipeline wheel portability."""

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import sysconfig
import tarfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

import pytest

from skala_rag.settings import load_runtime_settings

ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIRECTORY = ROOT / "src/skala_rag/prompt/text"


def run_command(arguments: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("VIRTUAL_ENV", None)
    result = subprocess.run(
        arguments,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
    )
    print(
        json.dumps(
            {
                "command": shlex.join(arguments),
                "argv": arguments,
                "cwd": str(cwd),
                "PYTHONPATH": None,
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


@dataclass(frozen=True, slots=True)
class InstalledPackage:
    directory: Path
    python: Path
    sdist: Path
    wheel: Path
    rebuilt_wheel: Path


@contextmanager
def installed_package() -> Iterator[InstalledPackage]:
    with TemporaryDirectory(prefix="task-11-packaged-resources-") as temporary:
        directory = Path(temporary).resolve()
        assert not directory.is_relative_to(ROOT)
        distribution_directory = directory / "dist"
        run_command(["uv", "build", "--out-dir", str(distribution_directory)], cwd=ROOT)
        (sdist,) = distribution_directory.glob("*.tar.gz")
        (wheel,) = distribution_directory.glob("*.whl")
        rebuilt_directory = directory / "rebuilt"
        run_command(
            ["uv", "build", "--wheel", str(sdist), "--out-dir", str(rebuilt_directory)],
            cwd=ROOT,
        )
        (rebuilt_wheel,) = rebuilt_directory.glob("*.whl")
        environment_directory = directory / "venv"
        run_command(
            ["uv", "venv", "--python", sys.executable, str(environment_directory)],
            cwd=ROOT,
        )
        python = environment_directory / "bin/python"
        # Copy dependency bytes, never editable project paths. Offline resolver
        # cache completeness is not this installed-resource test's claim.
        source_site = Path(sysconfig.get_path("purelib"))
        target_site = environment_directory / (
            f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
        )
        for entry in source_site.iterdir():
            if entry.suffix == ".pth" or entry.name.startswith("skala_rag"):
                continue
            target = target_site / entry.name
            if entry.is_dir():
                shutil.copytree(entry, target, dirs_exist_ok=True)
            else:
                shutil.copy2(entry, target)
        run_command(
            [
                "uv",
                "pip",
                "install",
                "--no-deps",
                "--python",
                str(python),
                str(rebuilt_wheel),
            ],
            cwd=ROOT,
        )
        yield InstalledPackage(directory, python, sdist, wheel, rebuilt_wheel)
    assert not directory.exists()
    print(json.dumps({"cleanup": str(directory), "exists": False}))


@pytest.fixture(scope="module")
def packaged_resources() -> Iterator[InstalledPackage]:
    with installed_package() as package:
        yield package


def canonical_resources() -> dict[str, bytes]:
    resources = {
        "_config/runtime.json": (ROOT / "configs/runtime.json").read_bytes(),
        "_config/pdf.layout.v1.json": (
            ROOT / "configs/pdf.layout.v1.json"
        ).read_bytes(),
    }
    for resource in sorted(PROMPT_DIRECTORY.glob("*.json")):
        resources[f"prompt/text/{resource.name}"] = resource.read_bytes()
    for resource in sorted((ROOT / "src/skala_rag/reporting/fonts").iterdir()):
        if resource.is_file():
            resources[f"reporting/fonts/{resource.name}"] = resource.read_bytes()
    assert len(resources) > 1
    return resources


def test_distributions_preserve_canonical_resources(packaged_resources):
    # Given: the canonical assets and actual built distributions.
    resources = canonical_resources()
    # When: open the sdist and both wheels (including the sdist rebuild).
    with tarfile.open(packaged_resources.sdist) as archive:
        prefix = packaged_resources.sdist.name.removesuffix(".tar.gz")
        for name, content in resources.items():
            source_name = "src/skala_rag/" + name
            if name.startswith("_config/"):
                source_name = "configs/" + name.removeprefix("_config/")
            member = archive.extractfile(f"{prefix}/{source_name}")
            assert member is not None
            with member:
                # Then: every shipped asset retains its canonical bytes.
                assert member.read() == content, source_name
    for wheel in (packaged_resources.wheel, packaged_resources.rebuilt_wheel):
        with ZipFile(wheel) as archive:
            for name, content in resources.items():
                assert archive.read(f"skala_rag/{name}") == content, name


INSTALLED_PROBE = """
import hashlib
import json
import sys
from importlib.resources import files
from pathlib import Path
from skala_rag import settings
from skala_rag.prompt._resources import read_prompt, read_questions
from skala_rag.reporting.pdf import load_pdf_profile

expected = json.loads(sys.argv[1])
module = Path(settings.__file__).resolve()
assert module.is_relative_to(Path(sys.prefix).resolve())
assert not module.is_relative_to(Path(expected["checkout"]))
assert all(not Path(p).resolve().is_relative_to(Path(expected["checkout"]))
           for p in sys.path)
for name, digest in expected["resources"].items():
    content = files("skala_rag").joinpath(name).read_bytes()
    assert hashlib.sha256(content).hexdigest() == digest, name
    if name.startswith("prompt/text/"):
        if name.endswith("company_analysis_questions.v1.json"):
            assert read_questions().encode("utf-8") == content
        else:
            literal = "".join(json.loads(content)).encode("utf-8")
            assert read_prompt(Path(name).name).encode("utf-8") == literal
profile = load_pdf_profile()
font_root = files("skala_rag").joinpath("reporting/fonts")
for name, expected_hash in (
    ("NanumGothic-Regular.ttf", profile.regular_sha256),
    ("NanumGothic-Bold.ttf", profile.bold_sha256),
):
    content = font_root.joinpath(name).read_bytes()
    assert hashlib.sha256(content).hexdigest() == expected_hash
for name, snapshot in expected["profiles"].items():
    assert settings.load_runtime_settings(name).model_dump(mode="json") == snapshot
print(json.dumps({"status": "PASS", "module": str(module),
                  "resources": len(expected["resources"]) - 1,
                  "profiles": list(expected["profiles"])}))
"""


def test_installed_defaults_and_prompts_match_source(packaged_resources):
    # Given: independent canonical bytes and explicit source profile snapshots.
    expected = {"checkout": str(ROOT), "resources": {}, "profiles": {}}
    for name, content in canonical_resources().items():
        expected["resources"][name] = hashlib.sha256(content).hexdigest()
    runtime_file = ROOT / "configs/runtime.json"
    for name in json.loads(runtime_file.read_bytes())["profiles"]:
        expected["profiles"][name] = load_runtime_settings(
            name, path=runtime_file
        ).model_dump(mode="json")
    assert len(expected["profiles"]) == 8
    # When: use isolated Python outside checkout, with no source import path.
    result = run_command(
        [
            str(packaged_resources.python),
            "-I",
            "-c",
            INSTALLED_PROBE,
            json.dumps(expected),
        ],
        cwd=packaged_resources.directory,
    )
    # Then: installed paths, every asset and every profile are verified.
    assert json.loads(result.stdout)["status"] == "PASS"


FAILURE_PROBE = """
import json
import sys
from importlib.resources import files
from pydantic import ValidationError
from skala_rag.settings import load_runtime_settings
from skala_rag.prompt._resources import read_prompt
from skala_rag.reporting.pdf import load_pdf_profile

name, mutation = sys.argv[1:]
resource = files("skala_rag").joinpath(name)
original = resource.read_bytes()

def load_resource():
    if name == "_config/runtime.json":
        return load_runtime_settings("m2_shared")
    if name == "_config/pdf.layout.v1.json":
        return load_pdf_profile()
    return read_prompt(name.rsplit("/", 1)[1])

# Each mutation starts from a verified valid installed baseline.
load_resource()
try:
    if mutation == "missing":
        resource.unlink()
        expected_error = FileNotFoundError
    else:
        resource.write_bytes(b"{")
        expected_error = (
            ValidationError
            if name == "_config/pdf.layout.v1.json"
            else json.JSONDecodeError
        )
    try:
        load_resource()
    except expected_error as error:
        print(json.dumps({"status": "PASS", "resource": name,
                          "mutation": mutation, "error": type(error).__name__}))
    else:
        raise AssertionError("Damaged installed resource did not fail closed")
finally:
    resource.write_bytes(original)
assert resource.read_bytes() == original
"""


@pytest.mark.parametrize("mutation", ["missing", "malformed"])
@pytest.mark.parametrize(
    "resource",
    [
        "_config/runtime.json",
        "_config/pdf.layout.v1.json",
        "prompt/text/evidence_extraction.json",
    ],
)
def test_damaged_installed_resource_fails_closed(
    packaged_resources, resource, mutation
):
    # Given: a real installed resource, independently damaged in a fresh process.
    # When: use the production loader, while the canonical checkout still exists.
    result = run_command(
        [str(packaged_resources.python), "-I", "-c", FAILURE_PROBE, resource, mutation],
        cwd=packaged_resources.directory,
    )
    # Then: the exact expected exception proves no source fallback; bytes restore.
    observation = json.loads(result.stdout)
    assert observation["status"] == "PASS"
    assert observation["resource"] == resource
    assert observation["mutation"] == mutation
