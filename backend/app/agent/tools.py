"""Bound LangChain schemas and wrappers; every capability crosses MCP."""

from pydantic import create_model
from langchain_core.tools import StructuredTool

TOOL_FIELDS = {
    "search_knowledge": {"question": (str, ...), "mode": (str, "HYBRID"), "top_k": (int, 6)},
    "search_web": {"query": (str, ...), "max_results": (int, 3)},
    "fetch_web_page": {"url": (str, ...)},
    "search_previous_tickets": {
        "query": (str, ...),
        "user": (str | None, None),
        "company": (str | None, None),
        "site": (str | None, None),
    },
    "get_user_context": {"user_name": (str, ...), "company": (str | None, None)},
    "get_company_context": {"company": (str, ...)},
    "get_site_context": {"company": (str, ...), "site": (str, ...)},
    "get_device_context": {"device_or_user": (str, ...)},
    "get_active_alerts": {
        "company": (str, ...),
        "site": (str | None, None),
        "product": (str | None, None),
    },
    "get_recent_changes": {
        "company": (str, ...),
        "site": (str | None, None),
        "product": (str | None, None),
    },
    "save_case_note": {"case_id": (str, ...), "text": (str, ...), "source": (str, "agent")},
    "remember_fact": {"fact": (dict, ...)},
    "search_remembered_facts": {"subject": (str, ...), "query": (str | None, None)},
}
READ_TOOLS = set(TOOL_FIELDS) - {"remember_fact", "save_case_note"}


class AgentToolHarness:
    def __init__(self, mcp_client):
        self.mcp_client = mcp_client
        self.invocation_count = 0
        self.tools = {name: self._bind(name, fields) for name, fields in TOOL_FIELDS.items()}

    def _bind(self, name, fields):
        async def invoke(**arguments):
            self.invocation_count += 1
            return await self.mcp_client.call_tool(name, arguments)

        return StructuredTool.from_function(
            name=name,
            description=f"DeskMate MCP capability: {name}.",
            coroutine=invoke,
            args_schema=create_model(name + "Input", **fields),
        )

    async def invoke(self, name, arguments):
        if name not in self.tools:
            raise ValueError(f"Unknown capability: {name}")
        return await self.tools[name].ainvoke(arguments)

    def schemas(self):
        # Keep the prompt compact while retaining actual argument types and
        # required fields. LangChain performs full schema validation on invoke.
        result = []
        for name in sorted(READ_TOOLS):
            schema = self.tools[name].args_schema.model_json_schema()
            properties = {
                key: {k: v for k, v in spec.items() if k not in {"title", "default"}}
                for key, spec in schema["properties"].items()
            }
            result.append(
                {"name": name, "properties": properties, "required": schema.get("required", [])}
            )
        return result
