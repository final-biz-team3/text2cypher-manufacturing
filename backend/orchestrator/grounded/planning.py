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
    missing_tools = {o.tool for o in intent.outputs} - {r["tool"] for r in routes}
    if missing_tools:
        raise ValueError(
            "Route omits requested output sources: " + ", ".join(sorted(missing_tools))
        )
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
            "Preserve the original question across the COMPLETE plan. Apply this source's "
            "conditions here; conditions owned by the other source must be enforced there "
            "and carried through input bindings or the final identity join. Never invent "
            "a local field to implement a condition owned by another source.",
            "Source responsibility: " + route["question"],
            "Complete dependency and composition plan: "
            + json.dumps(routes, ensure_ascii=False),
            "Input arrays from the same producer are aligned rows, not independent sets. "
            "Preserve their pairing, NULLs and multiplicity. Use membership filtering for "
            "an identity set; do not multiply aggregates by repeated IDs. Apply global "
            "ranking/limits only after every requested filter has been enforced.",
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
