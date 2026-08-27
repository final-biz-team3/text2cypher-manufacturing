import json
from pathlib import Path

import pytest

from agents.cypher.generator import generate_cypher
from agents.cypher.schema.models import GraphQueryPolicy
from agents.sql.generator import generate_sql
from guard.natural_language import make_natural_language_guard_node
from ontology.loader import load_term_dictionary
from ontology.normalizer import normalize_query
from orchestrator.nodes.route_query import make_route_query_node
from orchestrator.nodes.validate_generated_queries import validate_generated_queries
from tests.mocks.openai import MockOpenAIClient, make_content_response

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_EVALUATION = json.loads(
    (_PROJECT_ROOT / "queries" / "ontology_synonym_evaluation.json").read_text(
        encoding="utf-8"
    )
)
_CONTRACTS = json.loads(
    (_PROJECT_ROOT / "queries" / "query_contracts.json").read_text(encoding="utf-8")
)
_DICTIONARY = load_term_dictionary(
    _PROJECT_ROOT / "ontology" / "manufacturing_terms.yaml"
)


def _plan(route: str) -> list[str]:
    return {
        "SQL": ["sql"],
        "GRAPH": ["graph"],
        "HYBRID": ["sql", "graph"],
    }[route]


async def test_twenty_questions_have_two_variants_with_same_route_and_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    contracts = {item["id"]: item for item in _CONTRACTS["questions"]}
    questions = _EVALUATION["questions"]
    all_queries = [
        (item, query)
        for item in questions
        for query in [contracts[item["id"]]["sampleQuestion"], *item["variants"]]
    ]
    read = make_content_response(
        '{"intent":"READ","confidence":0.99,"reason":"조회 질문"}'
    )
    natural_client = MockOpenAIClient(*(read for _ in all_queries))
    natural_guard = make_natural_language_guard_node(natural_client)
    route_client = MockOpenAIClient(
        *(
            make_content_response(json.dumps(_plan(item["route"])))
            for item, _ in all_queries
        )
    )
    route_node = make_route_query_node(route_client)

    assert len(questions) == 20
    assert all(len(item["variants"]) >= 2 for item in questions)
    for item, query in all_queries:
        assert item["route"] == contracts[item["id"]]["route"]
        normalized = normalize_query(query, _DICTIONARY)
        evidence = _EVALUATION["canonicalEvidence"][item["id"]]
        assert all(term in normalized["normalized_query"] for term in evidence), item[
            "id"
        ]
        natural = await natural_guard({"query": query, **normalized})
        assert natural["natural_guard"]["decision"] == "ALLOW_READ", item["id"]
        routed = await route_node(
            {"query": query, "normalized_query": normalized["normalized_query"]}
        )
        assert routed["tool_plan"] == _plan(item["route"]), item["id"]
        route_payload = route_client.calls[-1]["messages"][1]["content"]
        assert normalized["normalized_query"] in route_payload
        assert all(term in route_payload for term in evidence)

        plan = routed["tool_plan"]
        representative = _EVALUATION["representativeQueries"][item["id"]]
        generator_client = MockOpenAIClient(
            *(
                make_content_response(
                    representative["sql" if tool == "sql" else "cypher"]
                )
                for tool in plan
            )
        )
        generated: dict[str, str | None] = {
            "sql_query": None,
            "cypher_query": None,
        }
        if "sql" in plan:
            generated["sql_query"] = await generate_sql(
                generator_client,
                query=normalized["normalized_query"],
                entity=None,
                schema_text="production manufacturing schema",
            )
        if "graph" in plan:
            generated["cypher_query"] = await generate_cypher(
                generator_client,
                query=normalized["normalized_query"],
                entity=None,
                schema_text="Product Supplier WorkOrder graph schema",
                query_policy=GraphQueryPolicy(bomAsOfDate="2014-08-08", bomMaxDepth=4),
            )
        for call in generator_client.calls:
            generator_payload = "\n".join(
                message["content"] for message in call["messages"]
            )
            assert normalized["normalized_query"] in generator_payload
            assert all(term in generator_payload for term in evidence)
        guarded = validate_generated_queries(
            {
                "query": query,
                "tool_plan": plan,
                "sql_query": generated["sql_query"],
                "cypher_query": generated["cypher_query"],
            }
        )
        assert guarded["query_guard"]["decision"] == "PASSED", item["id"]

    assert len(all_queries) == 60
    assert len(natural_client.calls) == 60
    assert len(route_client.calls) == 60


def test_normalization_accuracy_and_entity_name_false_positives() -> None:
    cases = _EVALUATION["normalizationCases"]
    correct = sum(
        normalize_query(source, _DICTIONARY)["normalized_query"] == expected
        for source, expected in cases
    )
    assert correct / len(cases) >= 0.95

    for entity_name in _EVALUATION["entityNameCorpus"]:
        assert (
            normalize_query(entity_name, _DICTIONARY)["normalized_query"] == entity_name
        )
