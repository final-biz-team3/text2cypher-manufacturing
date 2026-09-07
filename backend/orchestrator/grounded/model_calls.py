"""Bounded typed calls with existing model usage instrumentation."""

import json
import os
from typing import Any

from pydantic import BaseModel

from core.observability.model_calls import observe_model_call


async def typed_call[T: BaseModel](
    client: Any,
    model_type: type[T],
    *,
    purpose: str,
    system: str,
    payload: dict[str, Any],
) -> T:
    schema = model_type.model_json_schema()
    # All fields are required (nullable when optional), including nested definitions.
    for definition in [schema, *schema.get("$defs", {}).values()]:
        if definition.get("type") == "object":
            definition["required"] = list(definition.get("properties", {}))
            definition["additionalProperties"] = False
    model = os.environ["OPENAI_MODEL"]
    response = await observe_model_call(
        purpose,
        model,
        client.chat.completions.create(
            model=model,
            reasoning_effort="medium",
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, default=str),
                },
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": model_type.__name__,
                    "strict": True,
                    "schema": schema,
                },
            },
        ),
    )
    if not response.choices or response.choices[0].finish_reason != "stop":
        raise ValueError("Incomplete structured response")
    content = response.choices[0].message.content
    if not content:
        raise ValueError("Missing structured response")
    return model_type.model_validate_json(content)
