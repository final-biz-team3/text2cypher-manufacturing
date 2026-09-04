from prometheus_client import Counter, Histogram

LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60)
DB_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30)

CHAT_REQUESTS = Counter(
    "itda_chat_requests_total", "Chat requests", ("route", "final_status")
)
CHAT_DURATION = Histogram(
    "itda_chat_request_duration_seconds",
    "Chat latency",
    ("route", "final_status"),
    buckets=LATENCY_BUCKETS,
)
ROUTING = Counter(
    "itda_routing_decisions_total", "Routing decisions", ("route", "outcome")
)
PLANNED_TOOLS = Counter("itda_planned_tools_total", "Planned tools", ("route", "tool"))
TOOL_EXECUTIONS = Counter(
    "itda_tool_executions_total", "Tool executions", ("route", "tool", "outcome")
)
TOOL_SKIPS = Counter("itda_tool_skips_total", "Tool skips", ("route", "tool", "reason"))
QUERY_ATTEMPTS = Counter(
    "itda_query_attempts_total", "Query attempts", ("tool", "issue_code", "outcome")
)
REPAIRS = Counter(
    "itda_repairs_total", "Repairs", ("tool", "issue_code", "outcome", "engine")
)
REPAIR_EXHAUSTED = Counter(
    "itda_repair_exhausted_total", "Exhausted repairs", ("tool", "issue_code")
)
FAILURE_REVIEWS = Counter(
    "itda_failure_reviews_total", "Failure reviews", ("route", "tool", "issue_code")
)
MODEL_CALLS = Counter("itda_model_calls_total", "Model calls", ("purpose", "outcome"))
MODEL_CALL_DURATION = Histogram(
    "itda_model_call_duration_seconds",
    "Model call latency",
    ("purpose", "outcome"),
    buckets=LATENCY_BUCKETS,
)
MODEL_INPUT_TOKENS = Counter(
    "itda_model_input_tokens_total", "Model input tokens", ("purpose",)
)
MODEL_OUTPUT_TOKENS = Counter(
    "itda_model_output_tokens_total", "Model output tokens", ("purpose",)
)
MODEL_CACHED_INPUT_TOKENS = Counter(
    "itda_model_cached_input_tokens_total", "Model cached input tokens", ("purpose",)
)
MODEL_CACHE_WRITE_TOKENS = Counter(
    "itda_model_cache_write_tokens_total", "Model cache write tokens", ("purpose",)
)
MODEL_REASONING_TOKENS = Counter(
    "itda_model_reasoning_tokens_total", "Model reasoning tokens", ("purpose",)
)
MODEL_ESTIMATED_COST = Counter(
    "itda_model_estimated_cost_usd_total",
    "Estimated model cost in USD based on configured pricing",
    ("purpose",),
)
DROPPED_EVENTS = Counter(
    "itda_observability_events_dropped_total", "Dropped events", ("category", "reason")
)
EMPTY_RESULTS = Counter(
    "itda_empty_results_total", "Empty query results", ("tool", "empty_reason")
)
QUERY_ATTEMPT_DURATION = Histogram(
    "itda_query_attempt_duration_seconds",
    "Query attempt latency",
    ("tool", "outcome"),
    buckets=DB_BUCKETS,
)
PIPELINE_NODE_DURATION = Histogram(
    "itda_pipeline_node_duration_seconds",
    "Pipeline node latency",
    ("node",),
    buckets=LATENCY_BUCKETS,
)
DB_QUERY_DURATION = Histogram(
    "itda_db_query_duration_seconds",
    "Database query latency",
    ("tool", "outcome"),
    buckets=DB_BUCKETS,
)
