"""
MCP server that exposes Palantir Foundry drone data as Claude tools.
Run this alongside Claude Code so the AI can directly query Foundry.

Usage:
    python mcp_server.py

Then add to ~/.claude/mcp.json:
{
  "mcpServers": {
    "foundry-drone": {
      "command": "python",
      "args": ["/path/to/ns-hackathon/mcp_server.py"],
      "env": {}
    }
  }
}
"""
import json
import sys
import asyncio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types
from foundry_client import FoundryClient


app = Server("foundry-drone-c2")
foundry = FoundryClient()


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="foundry_list_drones",
            description="List all drone IDs tracked in Palantir Foundry telemetry.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="foundry_get_telemetry",
            description=(
                "Retrieve telemetry points for a drone from Palantir Foundry. "
                "Returns lat, lon, alt, speed, heading, battery, status per second."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "drone_id": {"type": "string"},
                    "start_time": {"type": "string", "description": "ISO-8601 start"},
                    "end_time": {"type": "string", "description": "ISO-8601 end"},
                    "limit": {"type": "integer", "default": 100},
                },
                "required": ["drone_id"],
            },
        ),
        types.Tool(
            name="foundry_get_missions",
            description="Return all drone missions from Palantir Foundry.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="foundry_list_object_types",
            description="List all ontology object types in Foundry (for discovery).",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="foundry_list_action_types",
            description="List all executable action types in Foundry (for discovery).",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="foundry_execute_action",
            description="Execute a Foundry ontology action (e.g. drone_hold, drone_abort).",
            inputSchema={
                "type": "object",
                "properties": {
                    "action_type": {"type": "string", "description": "Foundry action API name"},
                    "parameters": {"type": "object"},
                },
                "required": ["action_type", "parameters"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        if name == "foundry_list_drones":
            result = foundry.list_drones()
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "foundry_get_telemetry":
            rows = foundry.get_telemetry(
                arguments["drone_id"],
                arguments.get("start_time"),
                arguments.get("end_time"),
                limit=arguments.get("limit", 100),
            )
            return [types.TextContent(type="text", text=json.dumps(rows, indent=2))]

        elif name == "foundry_get_missions":
            missions = foundry.get_missions()
            return [types.TextContent(type="text", text=json.dumps(missions, indent=2))]

        elif name == "foundry_list_object_types":
            types_list = foundry.list_object_types()
            return [types.TextContent(type="text", text=json.dumps(types_list, indent=2))]

        elif name == "foundry_list_action_types":
            actions = foundry.list_action_types()
            return [types.TextContent(type="text", text=json.dumps(actions, indent=2))]

        elif name == "foundry_execute_action":
            result = foundry.execute_action(
                arguments["action_type"],
                arguments.get("parameters", {}),
            )
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        else:
            return [types.TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        return [types.TextContent(type="text", text=f"Error: {e}")]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
