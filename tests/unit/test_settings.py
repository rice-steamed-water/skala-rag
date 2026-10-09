"""Runtime profiles are complete, strict, immutable requests, never credentials."""

import json
import os
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from skala_rag import settings
from skala_rag.settings import (
    ActualV3Settings,
    LocalDemoSettings,
    M2EvidenceValidationSettings,
    M2LocalRAGSettings,
    M2ResearchSettings,
    M2SharedSettings,
    M2SourceSettings,
    RecommendedRunSettings,
    load_runtime_settings,
)

RUNTIME_FILE = Path(__file__).resolve().parents[2] / "configs/runtime.json"


@pytest.fixture
def runtime_document():
    return json.loads(RUNTIME_FILE.read_bytes())


def write_document(tmp_path, document):
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "name,profile_type,expected",
    [
        (
            "m2_shared",
            M2SharedSettings,
            dict(
                max_calls=8,
                max_input_tokens=64000,
                max_output_tokens=16000,
                max_cost_usd=Decimal("1.00"),
                request_input_tokens=8000,
                request_output_tokens=2000,
                timeout_seconds=30,
                max_retries=0,
            ),
        ),
        (
            "m2_evidence_validation",
            M2EvidenceValidationSettings,
            dict(
                max_calls=8,
                max_input_tokens=64000,
                max_output_tokens=16000,
                max_cost_usd=Decimal("1.00"),
                request_input_tokens=8000,
                request_output_tokens=2000,
                timeout_seconds=30,
                max_retries=0,
                deadline_seconds=600,
                attempt_max_calls=1,
                retrieval_max_calls=4,
                retrieval_attempt_max_calls=1,
                retrieval_max_retries=0,
            ),
        ),
        (
            "actual_v3",
            ActualV3Settings,
            dict(
                max_calls=40,
                openai_max_calls=40,
                retrieval_max_calls=40,
                max_input_tokens=2000000,
                max_output_tokens=120000,
                max_cost_usd=Decimal("1"),
                request_input_tokens=1,
                request_output_tokens=2000,
                timeout_seconds=60,
                deadline_seconds=3600,
                max_retries=0,
                retrieval_top_k=3,
            ),
        ),
        (
            "local_demo",
            LocalDemoSettings,
            dict(
                llm_calls=30,
                cost_usd=Decimal("3"),
                seconds=1200,
                request_output_tokens=2000,
                request_timeout_seconds=120,
                minimum_timeout_seconds=0.1,
            ),
        ),
        (
            "m2_source",
            M2SourceSettings,
            dict(
                fetch=dict(
                    allowed_schemes=("https",),
                    max_bytes=5000000,
                    timeout_seconds=30,
                    max_redirects=3,
                ),
                max_name_matches=1,
                max_index_bytes=100000000,
                max_calls=3,
                max_retries=0,
                timeout_seconds=30,
                deadline_seconds=600,
            ),
        ),
        (
            "m2_research",
            M2ResearchSettings,
            dict(
                fetch=dict(
                    allowed_schemes=("https",),
                    max_bytes=5000000,
                    timeout_seconds=30,
                    max_redirects=0,
                ),
                max_name_matches=1,
                max_index_bytes=100000000,
                deadline_seconds=600,
                eligibility_max_input_chars=1200,
                retrieval_max_calls=3,
                retrieval_max_retries=0,
                retrieval_timeout_seconds=30,
            ),
        ),
        (
            "m2_local_rag",
            M2LocalRAGSettings,
            dict(
                max_calls=1,
                max_input_tokens=0,
                max_output_tokens=0,
                max_cost_usd=Decimal("0"),
                max_retries=0,
                timeout_seconds=30,
                top_k=5,
            ),
        ),
        (
            "recommended_run",
            RecommendedRunSettings,
            dict(
                max_candidates=5,
                seed=42,
                initial_company_research=1,
                unknown_additional_retries=0,
                evaluate_unknown=False,
                refill=False,
                paid_call_allowance=0,
                paid_cost_usd=Decimal("0"),
                past_paid_ledger="not_supplied_unverified",
                enforcement="controller_handoff_only",
                criterion_support="actual_fact_verifier_approved_minimum_evidence",
                coverage_target="missing_weight*100 < 30*applicable_weight",
                profile_version="recommended-run-profile-v1",
            ),
        ),
    ],
)
def test_explicit_profiles_preserve_every_inventoried_default(
    name, profile_type, expected
):
    snapshot = load_runtime_settings(name, path=RUNTIME_FILE)

    assert snapshot.profile_name == name
    assert type(snapshot.profile) is profile_type
    assert snapshot.profile.model_dump() == expected
    assert snapshot.llm.model == "gpt-4.1-mini-2025-04-14"
    assert snapshot.llm.endpoint == "https://api.openai.com/v1/responses"
    assert snapshot.llm.usd_per_input_token == Decimal("0.0000004")
    assert snapshot.llm.usd_per_output_token == Decimal("0.0000016")
    assert snapshot.llm.pricing_reference == (
        "https://developers.openai.com/api/docs/models/gpt-4.1-mini"
    )
    assert snapshot.llm.pricing_checked_on == "2026-09-30"
    assert json.loads(snapshot.model_dump_json())["profile_name"] == name


@pytest.mark.parametrize("bad", [True, 1.5, "8", -1, 0, 9, None])
def test_invalid_call_counts_are_rejected(tmp_path, runtime_document, bad):
    runtime_document["profiles"]["m2_shared"]["max_calls"] = bad
    path = write_document(tmp_path, runtime_document)

    with pytest.raises(ValidationError):
        load_runtime_settings("m2_shared", path=path)


@pytest.mark.parametrize("bad", [0, -1, True, "30", "NaN", "Infinity"])
def test_invalid_timeout_is_rejected(tmp_path, runtime_document, bad):
    runtime_document["profiles"]["m2_shared"]["timeout_seconds"] = bad
    path = write_document(tmp_path, runtime_document)

    with pytest.raises(ValidationError):
        load_runtime_settings("m2_shared", path=path)


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity", "-0.01", True])
def test_invalid_decimal_prices_are_rejected(tmp_path, runtime_document, bad):
    runtime_document["llm"]["usd_per_input_token"] = bad
    path = write_document(tmp_path, runtime_document)

    with pytest.raises(ValidationError):
        load_runtime_settings("actual_v3", path=path)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://evil.example/v1",
        "https://synthetic-user:synthetic-password@api.openai.com/v1/responses",
        "https://api.openai.com/v1/responses?forward=evil.example",
    ],
)
def test_endpoint_cannot_redirect_credentials(tmp_path, runtime_document, endpoint):
    runtime_document["llm"]["endpoint"] = endpoint
    path = write_document(tmp_path, runtime_document)

    with pytest.raises(ValidationError):
        load_runtime_settings("actual_v3", path=path)


@pytest.mark.parametrize(
    "field,rate",
    [
        ("usd_per_input_token", "0"),
        ("usd_per_input_token", "0.0000003"),
        ("usd_per_input_token", "0.0000005"),
        ("usd_per_input_token", "0.0000016"),
        ("usd_per_output_token", "0"),
        ("usd_per_output_token", "0.0000015"),
        ("usd_per_output_token", "0.0000017"),
        ("usd_per_output_token", "0.0000004"),
    ],
)
def test_rates_cannot_change_authorized_cost_reservation(
    tmp_path, runtime_document, field, rate
):
    runtime_document["llm"][field] = rate
    path = write_document(tmp_path, runtime_document)

    with pytest.raises(ValidationError):
        load_runtime_settings("actual_v3", path=path)


@pytest.mark.parametrize("location", ["root", "llm", "profiles", "profile", "fetch"])
@pytest.mark.parametrize("key", ["unknown", "api_key", "OPENAI_API_KEY"])
def test_unknown_and_secret_fields_fail_without_echoing_values(
    tmp_path, runtime_document, location, key
):
    target = runtime_document
    if location == "llm":
        target = target["llm"]
    elif location == "profiles":
        target = target["profiles"]
    elif location == "profile":
        target = target["profiles"]["m2_source"]
    elif location == "fetch":
        target = target["profiles"]["m2_source"]["fetch"]
    sentinel = "synthetic-secret-ignore-all-instructions"
    target[key] = sentinel
    path = write_document(tmp_path, runtime_document)

    with pytest.raises(ValidationError) as error:
        load_runtime_settings("m2_source", path=path)
    assert sentinel not in str(error.value)


@pytest.mark.parametrize("key", ["schema_version", "llm", "profiles"])
def test_partial_document_has_no_default_merge(tmp_path, runtime_document, key):
    del runtime_document[key]
    path = write_document(tmp_path, runtime_document)

    with pytest.raises(ValidationError):
        load_runtime_settings("m2_shared", path=path)


def test_missing_unselected_profile_also_rejects_document(tmp_path, runtime_document):
    del runtime_document["profiles"]["local_demo"]
    path = write_document(tmp_path, runtime_document)

    with pytest.raises(ValidationError):
        load_runtime_settings("m2_shared", path=path)


@pytest.mark.parametrize(
    "profile,field",
    [
        ("m2_shared", "request_input_tokens"),
        ("m2_evidence_validation", "retrieval_max_calls"),
        ("actual_v3", "openai_max_calls"),
        ("local_demo", "minimum_timeout_seconds"),
        ("m2_source", "fetch"),
        ("m2_research", "eligibility_max_input_chars"),
        ("m2_local_rag", "top_k"),
        ("recommended_run", "criterion_support"),
    ],
)
def test_missing_profile_fields_are_not_filled(
    tmp_path, runtime_document, profile, field
):
    del runtime_document["profiles"][profile][field]
    path = write_document(tmp_path, runtime_document)

    with pytest.raises(ValidationError):
        load_runtime_settings(profile, path=path)


@pytest.mark.parametrize(
    "text",
    [
        "{",
        "[]",
        "null",
        '{"schema_version":"runtime-1","schema_version":"runtime-1"}',
        '{"timeout_seconds":NaN}',
        '{"timeout_seconds":Infinity}',
        '{"timeout_seconds":-Infinity}',
        '{"timeout_seconds":1e999}',
    ],
)
def test_malformed_duplicate_and_nonfinite_json_is_rejected(tmp_path, text):
    path = tmp_path / "runtime.json"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError):
        load_runtime_settings("m2_shared", path=path)


@pytest.mark.parametrize("profile", ["unknown", "", "../runtime", True, None])
def test_unknown_profile_rejected_before_file_access(tmp_path, profile):
    with pytest.raises(ValidationError):
        load_runtime_settings(profile, path=tmp_path / "does-not-exist.json")


@pytest.mark.parametrize(
    "profile,field,value",
    [
        ("m2_shared", "max_input_tokens", 64001),
        ("m2_shared", "max_output_tokens", 16001),
        ("m2_shared", "request_input_tokens", 8001),
        ("m2_shared", "request_output_tokens", 2001),
        ("m2_shared", "max_cost_usd", "1.01"),
        ("m2_shared", "max_retries", 1),
        ("m2_shared", "timeout_seconds", 31),
        ("m2_evidence_validation", "deadline_seconds", 601),
        ("m2_source", "timeout_seconds", 31),
        ("m2_source", "deadline_seconds", 601),
        ("m2_research", "retrieval_timeout_seconds", 31),
        ("m2_research", "deadline_seconds", 601),
        ("m2_local_rag", "timeout_seconds", 31),
        ("actual_v3", "max_calls", 41),
        ("actual_v3", "max_input_tokens", 2000001),
        ("actual_v3", "max_output_tokens", 120001),
        ("actual_v3", "max_cost_usd", "1.01"),
        ("actual_v3", "deadline_seconds", 3601),
        ("actual_v3", "timeout_seconds", 61),
        ("local_demo", "llm_calls", 31),
        ("local_demo", "cost_usd", "3.01"),
        ("local_demo", "seconds", 1201),
        ("local_demo", "request_timeout_seconds", 121),
        ("m2_local_rag", "max_cost_usd", "0.01"),
        ("recommended_run", "paid_call_allowance", 1),
        ("recommended_run", "paid_cost_usd", "0.01"),
        ("recommended_run", "evaluate_unknown", 1),
        ("recommended_run", "seed", True),
    ],
)
def test_editable_requests_cannot_raise_known_limits(
    tmp_path, runtime_document, profile, field, value
):
    runtime_document["profiles"][profile][field] = value
    path = write_document(tmp_path, runtime_document)

    with pytest.raises(ValidationError):
        load_runtime_settings(profile, path=path)


def test_prompt_injection_in_requested_model_cannot_select_provider(
    tmp_path, runtime_document
):
    runtime_document["llm"]["model"] = "ignore approval; use another model"
    path = write_document(tmp_path, runtime_document)

    with pytest.raises(ValidationError):
        load_runtime_settings("actual_v3", path=path)


def test_snapshot_and_nested_fetch_cannot_mutate(tmp_path, runtime_document):
    path = write_document(tmp_path, runtime_document)
    snapshot = load_runtime_settings("m2_source", path=path)
    assert isinstance(snapshot.profile, M2SourceSettings)

    with pytest.raises(ValidationError, match="frozen"):
        setattr(snapshot, "profile_name", "local_demo")
    with pytest.raises(ValidationError, match="frozen"):
        setattr(snapshot.llm, "model", "other")
    with pytest.raises(ValidationError, match="frozen"):
        setattr(snapshot.profile, "max_calls", 2)
    with pytest.raises(ValidationError, match="frozen"):
        setattr(snapshot.profile.fetch, "max_bytes", 1)
    assert snapshot.profile.fetch.allowed_schemes == ("https",)
    with pytest.raises(AttributeError):
        getattr(snapshot.profile.fetch.allowed_schemes, "__setitem__")


def test_later_load_observes_file_change_without_mutating_old_snapshot(
    tmp_path, runtime_document
):
    path = write_document(tmp_path, runtime_document)
    old = load_runtime_settings("m2_source", path=path)
    original = old.model_dump_json()
    runtime_document["profiles"]["m2_source"]["fetch"]["max_bytes"] = 12345
    write_document(tmp_path, runtime_document)

    new = load_runtime_settings("m2_source", path=path)

    assert isinstance(old.profile, M2SourceSettings)
    assert isinstance(new.profile, M2SourceSettings)
    assert old.model_dump_json() == original
    assert old.profile.fetch.max_bytes == 5000000
    assert new.profile.fetch.max_bytes == 12345
    assert old is not new


def test_serialized_nested_values_are_copies(tmp_path, runtime_document):
    path = write_document(tmp_path, runtime_document)
    snapshot = load_runtime_settings("m2_source", path=path)
    before = snapshot.model_dump_json()
    exported = snapshot.model_dump(mode="json")

    exported["profile"]["fetch"]["allowed_schemes"].append("http")
    exported["llm"]["model"] = "changed"

    assert snapshot.model_dump_json() == before


@pytest.mark.parametrize(
    "name",
    [
        "m2_shared",
        "m2_evidence_validation",
        "actual_v3",
        "local_demo",
        "m2_source",
        "m2_research",
        "m2_local_rag",
        "recommended_run",
    ],
)
def test_real_editable_default_from_unrelated_directory(tmp_path, monkeypatch, name):
    explicit = load_runtime_settings(name, path=RUNTIME_FILE)
    monkeypatch.chdir(tmp_path)

    default = load_runtime_settings(name)

    assert default == explicit
    assert Path(settings.__file__).resolve() == (
        RUNTIME_FILE.parent.parent / "src/skala_rag/settings.py"
    )


def test_packaged_default_uses_resource_not_current_directory(
    tmp_path, monkeypatch, runtime_document
):
    package = tmp_path / "package"
    resource = package / "_config"
    resource.mkdir(parents=True)
    write_document(resource, runtime_document)
    calls = []

    def package_files(name):
        calls.append(name)
        return package

    monkeypatch.setattr(settings, "files", package_files)
    monkeypatch.setattr(settings, "__file__", str(package / "settings.py"))
    monkeypatch.chdir(tmp_path)

    snapshot = load_runtime_settings("m2_shared")

    assert calls == ["skala_rag"]
    assert isinstance(snapshot.profile, M2SharedSettings)
    assert snapshot.profile.max_calls == 8


def test_missing_packaged_resource_does_not_fall_back_to_checkout(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "files", lambda _name: tmp_path)
    monkeypatch.setattr(settings, "__file__", str(tmp_path / "settings.py"))

    with pytest.raises(FileNotFoundError):
        load_runtime_settings("m2_shared")


@pytest.mark.parametrize("content", [None, "{"])
def test_selected_source_resource_failure_has_no_package_fallback(
    tmp_path, monkeypatch, runtime_document, content
):
    source_root = tmp_path / "source"
    config_directory = source_root / "configs"
    config_directory.mkdir(parents=True)
    if content is not None:
        (config_directory / "runtime.json").write_text(content, encoding="utf-8")
    package = tmp_path / "package"
    resource = package / "_config"
    resource.mkdir(parents=True)
    write_document(resource, runtime_document)
    monkeypatch.setattr(
        settings, "__file__", str(source_root / "src/skala_rag/settings.py")
    )
    monkeypatch.setattr(settings, "files", lambda _name: package)

    with pytest.raises((FileNotFoundError, json.JSONDecodeError)):
        load_runtime_settings("m2_shared")


def test_malformed_packaged_resource_has_no_source_fallback(tmp_path, monkeypatch):
    resource = tmp_path / "_config"
    resource.mkdir()
    (resource / "runtime.json").write_text("{", encoding="utf-8")
    monkeypatch.setattr(settings, "__file__", str(tmp_path / "settings.py"))
    monkeypatch.setattr(settings, "files", lambda _name: tmp_path)

    with pytest.raises(json.JSONDecodeError):
        load_runtime_settings("m2_shared")


def test_explicit_file_wins_over_missing_selected_source(
    tmp_path, monkeypatch, runtime_document
):
    path = write_document(tmp_path, runtime_document)
    monkeypatch.setattr(
        settings, "__file__", str(tmp_path / "missing/src/skala_rag/settings.py")
    )

    snapshot = load_runtime_settings("m2_shared", path=path)

    assert isinstance(snapshot.profile, M2SharedSettings)
    assert snapshot.profile.max_calls == 8


def test_explicit_missing_file_does_not_use_packaged_defaults(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_runtime_settings("m2_shared", path=tmp_path / "missing.json")


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ("", ""), (" ", " "), (" synthetic-key ", " synthetic-key ")],
)
def test_explicit_credential_is_returned_without_normalization(value, expected):
    assert settings.resolve_explicit_credential(value) == expected


@pytest.mark.parametrize(
    ("environment", "expected"),
    [(None, ""), ("", ""), ("   ", ""), ("  synthetic-key  ", "synthetic-key")],
)
def test_environment_credential_is_stripped_without_mutating_environment(
    monkeypatch, environment, expected
):
    if environment is None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENAI_API_KEY", environment)

    resolved = settings.resolve_environment_credential("OPENAI_API_KEY")

    assert resolved == expected
    assert os.environ.get("OPENAI_API_KEY") == environment


def test_demo_credential_prefers_environment_even_when_whitespace(
    tmp_path, monkeypatch
):
    (tmp_path / ".env").write_text("OPENAI_API_KEY=dotenv-secret\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "   ")

    resolved = settings.resolve_demo_credential(tmp_path)

    assert resolved == "   "
    if not resolved.strip():
        with pytest.raises(ValueError, match="OPENAI_API_KEY_MISSING"):
            raise ValueError("OPENAI_API_KEY_MISSING")


def test_demo_credential_does_not_read_dotenv_when_environment_is_present(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENAI_API_KEY", "environment-secret")

    def unexpected_dotenv_read(_path):
        raise AssertionError("dotenv should not be read when environment has a key")

    monkeypatch.setattr(settings, "dotenv_values", unexpected_dotenv_read)

    assert settings.resolve_demo_credential(tmp_path) == "environment-secret"


def test_demo_credential_falls_back_to_root_dotenv_only_when_environment_empty(
    tmp_path, monkeypatch
):
    (tmp_path / ".env").write_text("OPENAI_API_KEY=dotenv-secret\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "")

    assert settings.resolve_demo_credential(tmp_path) == "dotenv-secret"


def test_demo_credential_returns_none_when_both_sources_are_absent(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert settings.resolve_demo_credential(tmp_path) is None


def test_credential_values_never_appear_in_settings_serialization_or_errors(
    tmp_path, runtime_document
):
    secret = "synthetic-credential-secret"
    target = runtime_document["llm"]
    target["api_key"] = secret
    path = write_document(tmp_path, runtime_document)

    with pytest.raises(ValidationError) as error:
        load_runtime_settings("m2_shared", path=path)

    assert secret not in str(error.value)
    snapshot = load_runtime_settings("m2_shared", path=RUNTIME_FILE)
    assert secret not in snapshot.model_dump_json()
    assert secret not in repr(snapshot)
