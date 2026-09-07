"""Compile request-scoped outputs into the existing PR61 execution protocol."""

import json
from typing import Any

from orchestrator.grounded.models import QueryIntent
from orchestrator.nodes.plan_outputs import (
    _outgoing_binding_outputs,
    _with_required_outputs,
)
from orchestrator.planning import validate_subqueries


def plan_request_outputs(state: dict[str, Any]) -> dict[str, Any]:
    intent = QueryIntent.model_validate(state["query_intent"])
    routes = state["routeDraft"]["subqueries"]
    outgoing = _outgoing_binding_outputs(routes)
    planned = []
    for route in routes:
        definitions = [o for o in intent.outputs if o.tool == route["tool"]]
        aliases = list(
            dict.fromkeys(
                [o.alias for o in definitions]
                + route["joinKeys"]
                + outgoing[route["id"]]
            )
        )
        if not aliases:
            raise ValueError("Source responsibility has no grounded output")
        rules = [
            "Preserve ALL original question conditions, including negation and grouping grain.",
            "Do not relax conditions to obtain nonempty results. Empty results can be correct.",
            "Requested outputs with physical provenance: "
            + json.dumps([o.model_dump() for o in definitions], ensure_ascii=False),
            "Requirements: "
            + json.dumps(
                [r.model_dump() for r in intent.requirements], ensure_ascii=False
            ),
        ]
        if state.get("candidate_feedback"):
            rules.append(
                "Previous candidate diagnostics (not facts): "
                + json.dumps(state["candidate_feedback"], ensure_ascii=False)
            )
        planned.append(_with_required_outputs(route, aliases, rules))
    return {"subqueries": validate_subqueries(planned), "outputPlanRepairCount": 0}
