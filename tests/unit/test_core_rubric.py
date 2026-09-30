"""configs/rubrics/core.yaml 구조·구간 일관성 테스트 (#10, D14 제안).

rubric 파일이 scoring.md catalog(founder·market·technology·moat 14개)와 맞고,
anchor·구간이 1–5를 빠짐없이 정의하며, 문서 경계 예시와 같은 rating을
내는지 확인한다. 평가 로직 구현이 아니라 정책 파일 검증이다.
"""

import re
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
RUBRIC_PATH = ROOT / "configs" / "rubrics" / "core.yaml"
SCORING_DOC = ROOT / "docs" / "implementation" / "scoring.md"
DIMENSIONS = {"founder": 5, "market": 30, "technology": 25, "moat": 20}


@pytest.fixture(scope="module")
def rubric() -> dict:
    return yaml.safe_load(RUBRIC_PATH.read_text(encoding="utf-8"))


def _criteria(rubric: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for dim in rubric["dimensions"].values():
        out.update(dim["criteria"])
    return out


def _catalog_weights() -> dict[str, int]:
    pattern = re.compile(
        r"^\|\s*(?:founder|market|technology|moat) / \d+\s*\|"
        r"\s*([a-z_]+\.[a-z_0-9]+)\s*\|[^|]*\|\s*(\d+)\s*\|"
    )
    weights = {}
    for line in SCORING_DOC.read_text(encoding="utf-8").splitlines():
        m = pattern.match(line)
        if m:
            weights[m.group(1)] = int(m.group(2))
    return weights


def _rate(bands: list[dict], value: str) -> int:
    v = Decimal(value)
    hits = [
        b["rating"]
        for b in bands
        if ("min" not in b or v >= Decimal(str(b["min"])))
        and ("max" not in b or v < Decimal(str(b["max"])))
    ]
    assert len(hits) == 1, f"{value} matched {hits}"
    return hits[0]


def test_version_status_and_statuses(rubric):
    assert re.fullmatch(r"core-\d+\.\d+\.\d+", rubric["rubric_version"])
    assert rubric["status"] in {"proposed", "approved"}
    assert rubric["decision_id"] == "D14"
    assert rubric["statuses"] == ["observed", "missing", "not_applicable"]
    assert rubric["not_applicable_allowed"] is False


def test_ids_and_weights_match_scoring_catalog(rubric):
    catalog = _catalog_weights()
    assert len(catalog) == 14
    assert {cid: c["weight"] for cid, c in _criteria(rubric).items()} == catalog


def test_dimension_weights(rubric):
    assert {k: v["weight"] for k, v in rubric["dimensions"].items()} == DIMENSIONS
    for dim in rubric["dimensions"].values():
        assert sum(c["weight"] for c in dim["criteria"].values()) == dim["weight"]


def test_every_criterion_defines_ratings_1_to_5(rubric):
    for cid, c in _criteria(rubric).items():
        assert c["minimum_evidence"], cid
        if "bands" in c:
            assert {b["rating"] for b in c["bands"]} == {1, 2, 3, 4, 5}, cid
        else:
            assert set(c["anchors"]) == {1, 2, 3, 4, 5}, cid


def test_missing_reasons_are_declared(rubric):
    declared = set(rubric["missing_reasons"])
    for cid, c in _criteria(rubric).items():
        assert set(c["missing_when"]) <= declared, cid


def test_only_market_may_use_industry_evidence(rubric):
    assert rubric["common_rules"]["industry_evidence_dimensions"] == ["market"]


PROBES = ["-1", "0", "4.99", "5", "25", "99999999", "100000000", "1e11"]


@pytest.mark.parametrize("probe", PROBES)
def test_bands_cover_line_without_overlap(rubric, probe):
    for c in _criteria(rubric).values():
        if "bands" in c:
            _rate(c["bands"], probe)


@pytest.mark.parametrize(
    ("criterion", "value", "expected"),
    [
        ("market.size", "800000000", 3),
        ("market.size", "2000000000", 4),
        ("market.size", "99999999", 1),
        ("market.growth", "18.2", 4),
        ("market.growth", "26.0", 5),
        ("market.growth", "25", 5),
        ("market.growth", "-0.1", 1),
    ],
)
def test_document_boundary_examples(rubric, criterion, value, expected):
    assert _rate(_criteria(rubric)[criterion]["bands"], value) == expected


def test_tam_only_caps_market_size_at_3(rubric):
    caps = _criteria(rubric)["market.size"]["caps"]
    assert {"condition": "tam_only", "max_rating": 3} in caps
