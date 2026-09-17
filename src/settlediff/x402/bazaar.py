"""Strict interpretation of captured embedded Bazaar response metadata."""

from __future__ import annotations

import re
from typing import cast

from pydantic import JsonValue

from settlediff.domain.models import ResponseContract
from settlediff.x402.models import ResourceInfo

_SCHEMA_KEYWORDS = frozenset({"type", "required", "properties", "items"})
_SCHEMA_TYPES = frozenset({"object", "array", "string", "number", "integer", "boolean", "null"})
_MEDIA_TYPE = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")
_MAX_SCHEMA_PROPERTIES = 128


class BazaarContractError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code


def response_contract_from(
    resource: ResourceInfo, extensions: dict[str, JsonValue]
) -> ResponseContract | None:
    media_type = _media_type(resource.mime_type)
    source_fields: list[str] = []
    if media_type is not None:
        source_fields.append("resource.mimeType")

    if "bazaar" not in extensions:
        if media_type is None:
            return None
        return ResponseContract(
            media_type=media_type, json_schema=None, source_fields=tuple(source_fields)
        )
    bazaar = extensions["bazaar"]
    if not isinstance(bazaar, dict):
        raise BazaarContractError("BAZAAR_MALFORMED", "extensions.bazaar must be an object")
    bazaar_mapping = cast(dict[str, JsonValue], bazaar)
    info = bazaar_mapping.get("info")
    schema = bazaar_mapping.get("schema")
    if not isinstance(info, dict) or not isinstance(schema, dict):
        raise BazaarContractError(
            "BAZAAR_MALFORMED", "Bazaar response metadata requires info and schema objects"
        )
    output = cast(dict[str, JsonValue], info).get("output")
    if not isinstance(output, dict) or cast(dict[str, JsonValue], output).get("type") != "json":
        raise BazaarContractError(
            "BAZAAR_MALFORMED", "Bazaar info.output.type must be the captured json shape"
        )
    canonical_schema = _json_schema(cast(dict[str, JsonValue], schema), path="schema")
    source_fields.extend(("extensions.bazaar.info.output.type", "extensions.bazaar.schema"))
    return ResponseContract(
        media_type=media_type,
        json_schema=canonical_schema,
        source_fields=tuple(source_fields),
    )


def _media_type(value: str | None) -> str | None:
    if value is None:
        return None
    media_type = value.split(";", maxsplit=1)[0].strip().lower()
    if _MEDIA_TYPE.fullmatch(media_type) is None:
        raise BazaarContractError(
            "RESPONSE_CONTRACT_MALFORMED", "resource.mimeType is not a valid media type"
        )
    return media_type


def _json_schema(schema: dict[str, JsonValue], *, path: str) -> dict[str, JsonValue]:
    unsupported = tuple(sorted(set(schema) - _SCHEMA_KEYWORDS))
    if unsupported:
        raise BazaarContractError(
            "SCHEMA_UNSUPPORTED", f"{path} contains unsupported keyword {unsupported[0]}"
        )
    schema_type = schema.get("type")
    if not isinstance(schema_type, str) or schema_type not in _SCHEMA_TYPES:
        raise BazaarContractError("BAZAAR_MALFORMED", f"{path}.type is unsupported or missing")

    result: dict[str, JsonValue] = {"type": schema_type}
    required = schema.get("required")
    properties = schema.get("properties")
    items = schema.get("items")

    if required is not None or properties is not None:
        if schema_type != "object" or not isinstance(properties, dict):
            raise BazaarContractError(
                "BAZAAR_MALFORMED", f"{path} object keywords require object type and properties"
            )
        property_mapping = cast(dict[str, JsonValue], properties)
        if len(property_mapping) > _MAX_SCHEMA_PROPERTIES:
            raise BazaarContractError("BAZAAR_MALFORMED", f"{path}.properties exceeds the limit")
        canonical_properties: dict[str, JsonValue] = {}
        for name, child in property_mapping.items():
            if not name or not isinstance(child, dict):
                raise BazaarContractError(
                    "BAZAAR_MALFORMED", f"{path}.properties must map names to schemas"
                )
            canonical_properties[name] = _json_schema(
                cast(dict[str, JsonValue], child), path=f"{path}.properties.{name}"
            )
        result["properties"] = canonical_properties
        if required is not None:
            if (
                not isinstance(required, list)
                or not required
                or any(not isinstance(name, str) or not name for name in required)
                or len(set(cast(list[str], required))) != len(required)
                or any(name not in property_mapping for name in cast(list[str], required))
            ):
                raise BazaarContractError(
                    "BAZAAR_MALFORMED", f"{path}.required must name unique declared properties"
                )
            result["required"] = cast(list[JsonValue], sorted(cast(list[str], required)))

    if items is not None:
        if schema_type != "array" or not isinstance(items, dict):
            raise BazaarContractError(
                "BAZAAR_MALFORMED", f"{path}.items requires array type and one schema"
            )
        result["items"] = _json_schema(cast(dict[str, JsonValue], items), path=f"{path}.items")
    elif schema_type == "array":
        raise BazaarContractError("BAZAAR_MALFORMED", f"{path} array schema requires items")

    return result
