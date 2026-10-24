"""Bound LangChain invocation. Schemas/descriptions come from MCP discovery."""

from typing import Any, Literal
from referencing.exceptions import Unresolvable
from backend.app.mcp.schema import checked_schema, validator, planning_schema
from jsonschema.exceptions import ValidationError as SchemaValidationError

from langchain_core.tools import StructuredTool
from pydantic import ConfigDict, create_model

# Explicit permission boundary: planning may read, while infrastructure/human
# confirmation owns writes. MCP discovery alone never grants write permission.
READ_TOOLS = {
    "search_knowledge",
    "search_graph",
    "search_previous_tickets",
    "get_ticket",
    "get_user_context",
    "get_company_context",
    "get_site_context",
    "get_device_context",
    "get_active_alerts",
    "get_recent_changes",
    "search_web",
    "search_news",
    "fetch_web_page",
    "get_current_time",
    "calculate_date_time",
    "calculate",
    "convert_unit",
    "geocode_location",
    "get_weather",
    "search_places",
    "get_route",
    "convert_currency",
    "get_holidays",
    "search_memory",
    "search_remembered_facts",
    "get_authenticated_profile",
    "search_semantic_memory",
    "get_case_history",
    "list_available_context",
    "list_documents",
    "list_websites",
    "get_website",
    "list_registry",
    "resolve_entity",
    "reload_registry",
}
KNOWLEDGE_TOOLS = {
    "search_knowledge",
    "search_graph",
    "search_memory",
    "search_remembered_facts",
    "get_authenticated_profile",
    "search_semantic_memory",
    "search_web",
    "search_news",
    "fetch_web_page",
    "get_current_time",
    "calculate_date_time",
    "calculate",
    "convert_unit",
    "geocode_location",
    "get_weather",
    "search_places",
    "get_route",
    "convert_currency",
    "get_holidays",
    "list_available_context",
    "list_documents",
    "list_websites",
    "get_website",
}


# Session inspection and write tools are application-controlled, never model-planned.
def allowed_tools(workflow: str) -> set[str]:
    return KNOWLEDGE_TOOLS if workflow == "knowledge" else READ_TOOLS


def annotation(spec):
    if "enum" in spec:
        return (
            Any
            if any(isinstance(value, dict | list) for value in spec["enum"])
            else Literal[tuple(spec["enum"])]
        )
    if "anyOf" in spec:
        types = [annotation(s) for s in spec["anyOf"]]
        result = types[0]
        for kind in types[1:]:
            result = result | kind
        return result
    kind = spec.get("type")
    if kind == "array":
        return list[annotation(spec.get("items", {}))]
    return {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "null": type(None),
        "object": dict[str, Any],
    }.get(kind, Any)


class AgentToolHarness:
    def __init__(self, mcp_client):
        self.mcp_client = mcp_client
        self.tools = {}
        self.descriptors = {}
        self.invocation_count = 0

    async def initialize(self):
        items = await self.mcp_client.list_tools()
        self.tools.clear()
        self.descriptors.clear()
        for item in items:
            try:
                name = item["name"]
                if not isinstance(name, str):
                    continue
                schema = checked_schema(item["inputSchema"])
                required = schema.get("required", [])
                fields = {
                    k: (annotation(v), ... if k in required else v.get("default"))
                    for k, v in schema.get("properties", {}).items()
                }
                args = create_model(name + "Input", __config__=ConfigDict(extra="forbid"), **fields)

                def bind(tool_name):
                    async def call(**arguments):
                        self.invocation_count += 1
                        return await self.mcp_client.call_tool(tool_name, arguments)

                    return call

                tool = StructuredTool.from_function(
                    name=name,
                    description=item.get("description") or name,
                    coroutine=bind(name),
                    args_schema=args,
                )
            except (KeyError, TypeError, ValueError):
                continue  # An invalid integration must not hide healthy tools.
            self.tools[name] = tool
            self.descriptors[name] = item

    async def invoke(self, name, arguments):
        if name not in self.tools:
            raise ValueError(f"Unknown capability: {name}")
        self.validate_arguments(name, arguments)
        return await self.tools[name].ainvoke(arguments)

    def allowed_names(self, workflow):
        permitted = set(allowed_tools(workflow)) | {
            name
            for name, item in self.descriptors.items()
            if item.get("_deskmate_read_permitted") is True
            and workflow in item.get("_deskmate_workflows", [])
        }

        def available(item):
            metadata = item.get("meta") or item.get("_meta") or {}
            if not isinstance(metadata, dict):
                return False
            source = metadata.get("deskmate", {})
            return isinstance(source, dict) and source.get("available") is not False

        return {name for name in permitted if available(self.descriptors.get(name, {}))}

    def validate_arguments(self, name, arguments):
        try:
            validator(self.descriptors[name]["inputSchema"]).validate(arguments)
        except (SchemaValidationError, Unresolvable, RecursionError):
            raise ValueError("Tool arguments do not match its discovered schema") from None

    def validate_plan(self, plan, workflow):
        allowed = self.allowed_names(workflow)
        for call in plan.tool_calls:
            if call.name not in allowed or call.name not in self.tools:
                raise ValueError(f"Capability not permitted in {workflow}: {call.name}")
            self.tools[call.name].args_schema.model_validate(call.arguments)
            self.validate_arguments(call.name, call.arguments)

    def schemas(self, workflow="service_desk"):
        allowed = self.allowed_names(workflow)
        results = []
        for name, item in self.descriptors.items():
            if name not in allowed:
                continue
            results.append(
                {
                    "name": name,
                    "description": item.get("description", name),
                    "input_schema": planning_schema(item["inputSchema"]),
                }
            )
        return results
