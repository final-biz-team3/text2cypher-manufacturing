CREATE TABLE app.query_failure_reviews (
    review_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id BIGINT NOT NULL UNIQUE REFERENCES app.conversation_history(id) ON DELETE CASCADE,
    request_id VARCHAR(64) NOT NULL UNIQUE,
    question_fingerprint CHAR(64) NOT NULL,
    route VARCHAR(10) NOT NULL CHECK (route IN ('UNKNOWN', 'SQL', 'GRAPH', 'HYBRID')),
    failed_stage VARCHAR(32) NOT NULL,
    failed_tool VARCHAR(10) CHECK (failed_tool IN ('sql', 'graph')),
    issue_code VARCHAR(64) NOT NULL,
    sql_attempt_count SMALLINT NOT NULL DEFAULT 0 CHECK (sql_attempt_count BETWEEN 0 AND 3),
    graph_attempt_count SMALLINT NOT NULL DEFAULT 0 CHECK (graph_attempt_count BETWEEN 0 AND 3),
    status VARCHAR(20) NOT NULL DEFAULT 'NEW' CHECK (status IN ('NEW','TRIAGED','REPRODUCED','FIX_PLANNED','FIXED','WONT_FIX','DUPLICATE')),
    classification VARCHAR(32) CHECK (classification IN ('QUESTION_FILTER','ENTITY_RESOLUTION','ROUTING','PLANNING','SQL_GENERATION','CYPHER_GENERATION','SCHEMA_CONTEXT','REPAIR_POLICY','INFRASTRUCTURE','EVALUATION_DATA','OTHER')),
    assignee VARCHAR(128), notes TEXT, fixture_id VARCHAR(128), issue_url TEXT, pr_url TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), resolved_at TIMESTAMPTZ
);
CREATE INDEX query_failure_reviews_status_created_idx ON app.query_failure_reviews(status, created_at DESC);
CREATE INDEX query_failure_reviews_issue_created_idx ON app.query_failure_reviews(issue_code, created_at DESC);
CREATE INDEX query_failure_reviews_fingerprint_created_idx ON app.query_failure_reviews(question_fingerprint, created_at DESC);
CREATE INDEX query_failure_reviews_route_tool_created_idx ON app.query_failure_reviews(route, failed_tool, created_at DESC);
CREATE INDEX query_failure_reviews_class_updated_idx ON app.query_failure_reviews(classification, updated_at DESC);
