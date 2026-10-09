"""Bound LangChain invocation. Schemas/descriptions come from MCP discovery."""

from typing import Any
from pydantic import ConfigDict, create_model
from langchain_core.tools import StructuredTool

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
    "fetch_web_page",
    "search_memory",
    "search_remembered_facts",
    "get_case_history",
    "list_available_context",
}
KNOWLEDGE_TOOLS = {
    "search_knowledge",
    "search_graph",
    "search_web",
    "fetch_web_page",
    "list_available_context",
}


def annotation(spec):
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
        for item in await self.mcp_client.list_tools():
            name = item["name"]
            schema = item["inputSchema"]
            required = schema.get("required", [])
            fields = {
                k: (annotation(v), ... if k in required else v.get("default"))
                for k, v in schema.get("properties", {}).items()
            }
            args = create_model(name + "Input", __config__=ConfigDict(extra="forbid"), **fields)

            async def call(_tool=name, **arguments):
                self.invocation_count += 1
                return await self.mcp_client.call_tool(_tool, arguments)

            self.tools[name] = StructuredTool.from_function(
                name=name,
                description=item.get("description", name),
                coroutine=call,
                args_schema=args,
            )
            self.descriptors[name] = item

    async def invoke(self, name, arguments):
        if name not in self.tools:
            raise ValueError(f"Unknown capability: {name}")
        return await self.tools[name].ainvoke(arguments)

    def validate_plan(self, plan, workflow):
        allowed = KNOWLEDGE_TOOLS if workflow == "knowledge" else READ_TOOLS
        for call in plan.tool_calls:
            if call.name not in allowed or call.name not in self.tools:
                raise ValueError(f"Capability not permitted in {workflow}: {call.name}")
            self.tools[call.name].args_schema.model_validate(call.arguments)

    def schemas(self, workflow="service_desk"):
        allowed = KNOWLEDGE_TOOLS if workflow == "knowledge" else READ_TOOLS
        results = []
        for name, item in self.descriptors.items():
            if name not in allowed:
                continue
            schema = item["inputSchema"]
            properties = {
                key: {k: v for k, v in spec.items() if k not in {"title", "default"}}
                for key, spec in schema.get("properties", {}).items()
            }
            results.append(
                {
                    "name": name,
                    "description": item["description"],
                    "input_schema": {
                        "properties": properties,
                        "required": schema.get("required", []),
                    },
                }
            )
        return results
