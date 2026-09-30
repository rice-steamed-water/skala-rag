"""configs/rubrics/finance.yaml 구조·구간 일관성 테스트 (#11, D14 제안).

rubric 파일이 scoring.md catalog와 맞고, 숫자 구간이 빈틈·겹침 없이
전 구간을 덮으며, 문서의 경계 예시와 같은 rating을 내는지 확인한다.
평가 로직 구현이 아니라 정책 파일 검증이다.
"""

import re
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
RUBRIC_PATH = ROOT / "configs" / "rubrics" / "finance.yaml"
SCORING_DOC = ROOT / "docs" / "implementation" / "scoring.md"


@pytest.fixture(scope="module")
def rubric() -> dict:
    return yaml.safe_load(RUBRIC_PATH.read_text(encoding="utf-8"))


def _criteria(rubric: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for dim in rubric["dimensions"].values():
        out.update(dim["criteria"])
    return out


def _catalog_weights() -> dict[str, int]:
    """scoring.md §2 표에서 traction/deal_terms criterion 비중을 읽는다."""
    pattern = re.compile(
        r"^\|\s*(?:traction|deal_terms) / \d+\s*\|"
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


def test_version_and_status(rubric):
    assert re.fullmatch(r"finance-\d+\.\d+\.\d+", rubric["rubric_version"])
    assert rubric["status"] in {"proposed", "approved"}
    assert rubric["decision_id"] == "D14"


def test_ids_and_weights_match_scoring_catalog(rubric):
    catalog = _catalog_weights()
    assert len(catalog) == 9
    assert {cid: c["weight"] for cid, c in _criteria(rubric).items()} == catalog


def test_dimension_weights_sum(rubric):
    for dim in rubric["dimensions"].values():
        assert sum(c["weight"] for c in dim["criteria"].values()) == dim["weight"] == 10


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


PROBES = ["-1000", "-0.01", "0", "4.99", "5", "15", "39.99", "40", "1000"]


@pytest.mark.parametrize("probe", PROBES)
def test_bands_cover_line_without_overlap(rubric, probe):
    for c in _criteria(rubric).values():
        if "bands" in c:
            _rate(c["bands"], probe)


@pytest.mark.parametrize(
    ("criterion", "value", "expected"),
    [
        ("traction.revenue_growth", "50", 4),
        ("traction.revenue_growth", "49.98", 3),
        ("traction.revenue_growth", "-0.01", 1),
        ("traction.gross_margin", "30", 4),
        ("traction.runway", "12", 3),
        ("traction.runway", "11.99", 2),
        ("traction.runway", "24", 5),
        ("traction.concentration", "50", 2),
        ("traction.concentration", "14.99", 5),
        ("traction.rule_of_40", "40", 4),
        ("traction.rule_of_40", "39.99", 3),
        ("deal_terms.ownership", "20", 4),
        ("deal_terms.ownership", "19.996", 5),
        ("deal_terms.ownership", "4.99", 2),
        ("deal_terms.ownership", "40", 1),
    ],
)
def test_document_boundary_examples(rubric, criterion, value, expected):
    assert _rate(_criteria(rubric)[criterion]["bands"], value) == expected
