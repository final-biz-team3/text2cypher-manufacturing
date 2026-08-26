"""YAML 온톨로지를 Neo4j에 중복 없이 적재한다."""

import os
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase

from ontology.loader import build_term_index, load_term_dictionary
from ontology.models import TermDictionary

_CONSTRAINTS = (
    "CREATE CONSTRAINT ontology_term_normalized IF NOT EXISTS "
    "FOR (term:Term) REQUIRE term.normalizedText IS UNIQUE",
    "CREATE CONSTRAINT ontology_business_concept_id IF NOT EXISTS "
    "FOR (concept:BusinessConcept) REQUIRE concept.conceptId IS UNIQUE",
    "CREATE CONSTRAINT ontology_action_concept_id IF NOT EXISTS "
    "FOR (concept:ActionConcept) REQUIRE concept.conceptId IS UNIQUE",
)


def build_seed_records(
    dictionary: TermDictionary,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """개념과 normalized term-MEANS 레코드를 결정적으로 만든다."""
    concepts = [
        {
            "concept_id": concept.concept_id,
            "concept_type": concept.concept_type,
            "canonical": concept.canonical,
            "target_type": concept.target_type,
            "action_type": concept.action_type,
            "default_policy": concept.default_policy,
            "ontology_version": dictionary.version,
            "description": f"{concept.canonical} 제조 온톨로지 개념",
        }
        for concept in dictionary.concepts
    ]
    terms: list[dict[str, Any]] = []
    for normalized, candidates in sorted(build_term_index(dictionary).items()):
        original = next(
            term
            for concept in dictionary.concepts
            for term in concept.terms
            if " ".join(term.casefold().split()) == normalized
        )
        for concept in candidates:
            terms.append(
                {
                    "normalized": normalized,
                    "text": original,
                    "concept_id": concept.concept_id,
                    "concept_type": concept.concept_type,
                    "ontology_version": dictionary.version,
                    "language": (
                        "ko" if any("가" <= char <= "힣" for char in original) else "en"
                    ),
                }
            )
    return concepts, terms


def seed_dictionary(
    driver: Any, dictionary: TermDictionary, *, database: str = "neo4j"
) -> None:
    """제약을 준비하고 MERGE만 사용해 온톨로지를 적재한다."""
    for constraint in _CONSTRAINTS:
        driver.execute_query(constraint, database_=database)

    concepts, terms = build_seed_records(dictionary)
    business_concepts = [row for row in concepts if row["concept_type"] == "BUSINESS"]
    action_concepts = [row for row in concepts if row["concept_type"] == "ACTION"]
    driver.execute_query(
        """
        UNWIND $concepts AS row
        MERGE (concept:BusinessConcept {conceptId: row.concept_id})
        SET concept.nameKo = row.canonical,
            concept.description = row.description,
            concept.targetType = row.target_type,
            concept.ontologyVersion = row.ontology_version
        RETURN count(*) AS seededConcepts
        """,
        concepts=business_concepts,
        database_=database,
    )
    driver.execute_query(
        """
        UNWIND $concepts AS row
        MERGE (concept:ActionConcept {conceptId: row.concept_id})
        SET concept.nameKo = row.canonical,
            concept.actionType = row.action_type,
            concept.defaultPolicy = row.default_policy,
            concept.ontologyVersion = row.ontology_version
        RETURN count(*) AS seededConcepts
        """,
        concepts=action_concepts,
        database_=database,
    )
    driver.execute_query(
        """
        UNWIND $terms AS row
        MERGE (term:Term {normalizedText: row.normalized})
        ON CREATE SET term.termId = row.normalized, term.text = row.text
        SET term.language = row.language,
            term.ontologyVersion = row.ontology_version
        WITH term, row
        OPTIONAL MATCH (business:BusinessConcept {conceptId: row.concept_id})
        OPTIONAL MATCH (action:ActionConcept {conceptId: row.concept_id})
        WITH term, coalesce(business, action) AS concept
        WHERE concept IS NOT NULL
        MERGE (term)-[:MEANS]->(concept)
        RETURN count(*) AS seededMeanings
        """,
        terms=terms,
        database_=database,
    )


def main() -> None:
    dictionary = load_term_dictionary(Path(os.environ["ONTOLOGY_PATH"]))
    driver = GraphDatabase.driver(
        os.environ["GRAPH_URI"],
        auth=(os.environ["ADMIN_NEO4J_USER"], os.environ["ADMIN_NEO4J_PASSWORD"]),
    )
    try:
        seed_dictionary(
            driver, dictionary, database=os.environ.get("GRAPH_DATABASE", "neo4j")
        )
    finally:
        driver.close()


if __name__ == "__main__":
    main()
