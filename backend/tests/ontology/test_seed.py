from pathlib import Path

from ontology.models import TermDictionary
from ontology.seed import build_seed_records, seed_dictionary

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


class RecordingDriver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute_query(self, query: str, **kwargs: object) -> None:
        self.calls.append((query, kwargs))


def _ambiguous_dictionary() -> TermDictionary:
    return TermDictionary.model_validate(
        {
            "version": "test",
            "concepts": [
                {
                    "conceptId": "business_one",
                    "conceptType": "BUSINESS",
                    "canonical": "공급업체",
                    "terms": ["공통어", "업체"],
                },
                {
                    "conceptId": "business_two",
                    "conceptType": "BUSINESS",
                    "canonical": "파트너",
                    "terms": ["공통어"],
                },
                {
                    "conceptId": "action_read",
                    "conceptType": "ACTION",
                    "canonical": "조회",
                    "actionType": "READ",
                    "defaultPolicy": "ALLOW",
                    "terms": ["조회"],
                },
            ],
        }
    )


def test_seed_records_share_one_term_across_ambiguous_meanings() -> None:
    _, terms = build_seed_records(_ambiguous_dictionary())

    common = [row for row in terms if row["normalized"] == "공통어"]
    assert {row["concept_id"] for row in common} == {"business_one", "business_two"}
    assert {row["normalized"] for row in common} == {"공통어"}


def test_seed_uses_constraints_and_merge_only() -> None:
    driver = RecordingDriver()

    seed_dictionary(driver, _ambiguous_dictionary())

    queries = "\n".join(query for query, _ in driver.calls)
    assert queries.count("CREATE CONSTRAINT") == 3
    assert "IF NOT EXISTS" in queries
    assert "MERGE (term:Term {normalizedText: row.normalized})" in queries
    assert "MERGE (term)-[:MEANS]->(concept)" in queries
    assert "CREATE (" not in queries


def test_seed_constraints_match_physical_schema() -> None:
    driver = RecordingDriver()
    seed_dictionary(driver, _ambiguous_dictionary())
    seed_queries = "\n".join(query for query, _ in driver.calls)
    physical_schema = (
        _PROJECT_ROOT / "schema" / "structured_mvp_constraints.cypher"
    ).read_text(encoding="utf-8")

    for constraint_name in (
        "ontology_term_normalized",
        "ontology_business_concept_id",
        "ontology_action_concept_id",
    ):
        assert constraint_name in seed_queries
        assert constraint_name in physical_schema
    assert physical_schema.count("CREATE CONSTRAINT") == 9
