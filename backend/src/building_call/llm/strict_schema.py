"""The project's JSON Schemas in the strict form that Structured Outputs require.

OpenAI (strict json_schema) and Bedrock (outputConfig.textFormat) both need every property in
`required` and `additionalProperties: false`. The project's schemas leave every key optional
(the model writes only the keys with a value, which keeps local models fast), so here every
property becomes nullable instead: `{"type": ["string", "null"]}` and `null` in its enum. The
nulls are dropped again by `_validate` and the Pydantic `Interpretation`, so the rest of the code
sees the same dict as with any other provider.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def _nullable(spec: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(spec)
    kind = out.get("type", "string")
    kinds = list(kind) if isinstance(kind, list) else [kind]
    if "null" not in kinds:
        kinds.append("null")
    out["type"] = kinds
    if "enum" in out and None not in out["enum"]:
        out["enum"] = [*out["enum"], None]
    return out


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    props: dict[str, Any] = schema.get("properties", {})
    return {
        "type": "object",
        "properties": {key: _nullable(spec) for key, spec in props.items()},
        "required": list(props),
        "additionalProperties": False,
    }


def schema_name(schema: dict[str, Any]) -> str:
    """`title` of the schema as an API name (letters, digits, _ and -)."""
    title = str(schema.get("title") or "output")
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in title)[:64]


def without_nulls(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if v is not None}
