import os

import pytest
from neo4j import GraphDatabase

from ontology.models import TermDictionary
from ontology.seed import seed_dictionary


@pytest.mark.integration
def test_ontology_seed_is_idempotent_and_preserves_ambiguous_means() -> None:
    dictionary = TermDictionary.model_validate(
        {
            "version": "integration-test",
            "concepts": [
                {
                    "conceptId": "issue22_seed_business_a",
                    "conceptType": "BUSINESS",
                    "canonical": "테스트개념A",
                    "terms": ["issue22 ambiguous term"],
                },
                {
                    "conceptId": "issue22_seed_business_b",
                    "conceptType": "BUSINESS",
                    "canonical": "테스트개념B",
                    "terms": ["issue22 ambiguous term"],
                },
            ],
        }
    )
    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
    )
    count_query = """
        MATCH (term:Term {normalizedText: 'issue22 ambiguous term'})-[means:MEANS]->(concept)
        RETURN count(DISTINCT term) AS terms,
               count(means) AS meanings,
               count(DISTINCT concept) AS concepts
    """
    try:
        seed_dictionary(driver, dictionary)
        first = driver.execute_query(count_query, database_="neo4j").records[0]
        seed_dictionary(driver, dictionary)
        second = driver.execute_query(count_query, database_="neo4j").records[0]

        assert tuple(first.values()) == (1, 2, 2)
        assert tuple(second.values()) == (1, 2, 2)
    finally:
        driver.execute_query(
            "MATCH (term:Term {normalizedText: 'issue22 ambiguous term'}) "
            "DETACH DELETE term",
            database_="neo4j",
        )
        driver.execute_query(
            "MATCH (concept:BusinessConcept) "
            "WHERE concept.conceptId STARTS WITH 'issue22_seed_' "
            "DETACH DELETE concept",
            database_="neo4j",
        )
        driver.close()
