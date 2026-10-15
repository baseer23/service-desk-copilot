"""Bounded JSON schema handling; connected servers cannot trigger reference fetches."""

import json

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry
from referencing.exceptions import NoSuchResource


def no_remote_reference(uri):
    raise NoSuchResource(ref=uri)


LOCAL_REFERENCES_ONLY = Registry(retrieve=no_remote_reference)


def checked_schema(schema):
    if not isinstance(schema, dict) or len(json.dumps(schema)) > 65_536:
        raise ValueError("Integration tool schema is invalid or too large")
    pending, visited = [(schema, 0)], 0
    while pending:
        value, depth = pending.pop()
        visited += 1
        if depth > 32 or visited > 4096:
            raise ValueError("Integration tool schema exceeds safe complexity")
        if isinstance(value, dict):
            for name, child in value.items():
                if name in {"$ref", "$dynamicRef", "$recursiveRef"} and (
                    not isinstance(child, str) or not child.startswith("#")
                ):
                    raise ValueError("Integration schemas must use local references")
                pending.append((child, depth + 1))
        elif isinstance(value, list):
            pending.extend((child, depth + 1) for child in value)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        raise ValueError("Integration tool schema is invalid") from None
    return schema


def validator(schema):
    return Draft202012Validator(schema, registry=LOCAL_REFERENCES_ONLY)


def planning_schema(schema):
    """Preserve local definitions/constraints, remove presentation/default values."""
    if isinstance(schema, dict):
        return {name: planning_schema(value) for name, value in schema.items() if name not in {"title", "default"}}
    if isinstance(schema, list):
        return [planning_schema(value) for value in schema]
    return schema
