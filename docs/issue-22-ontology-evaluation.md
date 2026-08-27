# Issue 22 Ontology Evaluation

## Reproduction

- Source of truth: `ontology/manufacturing_terms.yaml`
- Question fixture: `queries/ontology_synonym_evaluation.json`
- Deterministic test: `backend/tests/ontology/test_evaluation.py`
- Live seed test: `backend/tests/integration/test_ontology_seed.py` (`integration` marker)
- No live LLM call is required. Natural intent and route responses use ordered mocks.

## Metrics

| Metric | Numerator / denominator | Result |
|---|---:|---:|
| Confirmed questions with at least two variants | 20 / 20 | 100% |
| Standard and variant natural READ decisions | 60 / 60 | 100% |
| Canonical evidence present in route/generator prompts | 60 / 60 | 100% |
| Contract route mock and RQ-specific query guard passes | 60 / 60 | 100% |
| Expected synonym normalizations | 20 / 20 | 100% |
| Entity-name false replacements | 0 / 10 | 0 |

Normalization accuracy is `correct normalized outputs / 20 labeled cases`; the acceptance
threshold is 95%. For every standard question and its two variants, the test verifies that the
same labeled canonical evidence is present in the normalized text passed to both route and query
generator prompts. Route output itself is an ordered deterministic mock of the contract route;
this measures pipeline payload and contract consistency, not live model routing accuracy.

Each RQ has its own representative SQL and/or Cypher in the fixture. The mocked generators return
those RQ-specific shapes, including joins, aggregates, BOM traversals, supplier paths, work-order
operations, and hybrid SQL+Cypher pairs. The production query guard validates all generated
statements. This is a deterministic T2S/T2C regression check, not a claim about live LLM quality or
database result correctness.

Normalization elapsed time is measured in the normalization node with a monotonic clock. The
state, `/chat` response, and structured node log contain non-negative milliseconds. No unstable
latency threshold is enforced.

## Seed Idempotency

The seed prepares unique constraints for normalized `Term`, `BusinessConcept.conceptId`, and
`ActionConcept.conceptId`, then uses `MERGE` for nodes and `MEANS`. The integration test seeds an
ambiguous normalized term twice and expects one Term, two concepts, and two MEANS relationships
after both runs.

Live PostgreSQL and Neo4j tests require Docker services and are deselected from the default test
suite. When Docker is unavailable, unit and static tests still verify seed records, MERGE syntax,
Compose ordering, administrator credentials, reader credential isolation, and query guards.
