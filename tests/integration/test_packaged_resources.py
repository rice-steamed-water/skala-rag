"""Real distribution/resource checks, not whole-pipeline wheel portability."""

import hashlib
import json
import os
import shlex
import subprocess
import sys
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
        requirements = directory / "resource-dependencies.txt"
        # Only the loaders' dependency closure is installed. Other package
        # subsystems are deliberately outside this portability claim.
        export = [
            "uv",
            "export",
            "--frozen",
            "--no-dev",
            "--no-emit-project",
            "--output-file",
            str(requirements),
        ]
        for dependency in (
            "httpx",
            "langchain-core",
            "langchain-text-splitters",
            "langgraph",
            "playwright",
            "pypdf",
            "reportlab",
            "sentence-transformers",
            "torch",
        ):
            export.extend(["--prune", dependency])
        run_command(export, cwd=ROOT)
        run_command(
            [
                "uv",
                "pip",
                "install",
                "--require-hashes",
                "--python",
                str(python),
                "-r",
                str(requirements),
            ],
            cwd=ROOT,
        )
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
    }
    for resource in sorted(PROMPT_DIRECTORY.glob("*.json")):
        resources[f"prompt/text/{resource.name}"] = resource.read_bytes()
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
            if name == "_config/runtime.json":
                source_name = "configs/runtime.json"
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
from skala_rag.prompt._resources import read_prompt

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
        literal = "".join(json.loads(content)).encode("utf-8")
        assert read_prompt(Path(name).name).encode("utf-8") == literal
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
from skala_rag.settings import load_runtime_settings
from skala_rag.prompt._resources import read_prompt

name, mutation = sys.argv[1:]
resource = files("skala_rag").joinpath(name)
original = resource.read_bytes()
try:
    if mutation == "missing":
        resource.unlink()
        expected_error = FileNotFoundError
    else:
        resource.write_bytes(b"{")
        expected_error = json.JSONDecodeError
    try:
        if name == "_config/runtime.json":
            load_runtime_settings("m2_shared")
        else:
            read_prompt(name.rsplit("/", 1)[1])
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
    "resource", ["_config/runtime.json", "prompt/text/evidence_extraction.json"]
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
