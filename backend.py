"""
Drone C2 Backend — FastAPI + Claude AI + Palantir Foundry
"""
import os
import json
import asyncio
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import anthropic
from dotenv import load_dotenv

from foundry_client import FoundryClient

load_dotenv()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ── Claude tools that map to Foundry operations ───────────────────────────

CLAUDE_TOOLS = [
    {
        "name": "get_drone_telemetry",
        "description": (
            "Retrieve second-by-second telemetry for a specific drone. "
            "Returns position (lat/lon/alt), speed, heading, battery, and status fields."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "drone_id": {"type": "string", "description": "Drone identifier"},
                "start_time": {"type": "string", "description": "ISO-8601 start time (optional)"},
                "end_time": {"type": "string", "description": "ISO-8601 end time (optional)"},
            },
            "required": ["drone_id"],
        },
    },
    {
        "name": "list_drones",
        "description": "Return all drone IDs currently tracked in the telemetry dataset.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_missions",
        "description": "Return all drone missions with waypoints, status, and assigned drone.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "execute_drone_command",
        "description": (
            "Execute a command on a drone via Foundry action. "
            "Commands: hold (loiter in place), abort (return to base immediately), "
            "reroute (change waypoint), rtb (return to base)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "drone_id": {"type": "string"},
                "command": {
                    "type": "string",
                    "enum": ["hold", "abort", "reroute", "rtb"],
                },
                "parameters": {
                    "type": "object",
                    "description": "Optional extra params (e.g. new waypoint lat/lon for reroute)",
                },
            },
            "required": ["drone_id", "command"],
        },
    },
]

SYSTEM_PROMPT = """You are a drone command and control AI operator.
You have direct access to Palantir Foundry which contains live drone telemetry and missions.
When the operator gives a voice command, interpret their intent, query the relevant data,
and execute the appropriate action. Be concise and military-precise in responses.
Always confirm the action taken and current drone status."""


# ── FastAPI app ────────────────────────────────────────────────────────────

foundry: Optional[FoundryClient] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global foundry
    foundry = FoundryClient()
    yield
    foundry.close()


app = FastAPI(title="Drone C2", lifespan=lifespan)

# Allow CORS for GitHub Pages + local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://ben-claflin.github.io",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


# ── Tool execution router ──────────────────────────────────────────────────

def execute_tool(name: str, inputs: dict) -> str:
    try:
        if name == "get_drone_telemetry":
            rows = foundry.get_telemetry(
                inputs["drone_id"],
                inputs.get("start_time"),
                inputs.get("end_time"),
                limit=3600,
            )
            return json.dumps({"count": len(rows), "telemetry": rows[:10], "total_rows": len(rows)})

        elif name == "list_drones":
            drones = foundry.list_drones()
            return json.dumps({"drones": drones})

        elif name == "get_missions":
            missions = foundry.get_missions()
            return json.dumps({"missions": missions})

        elif name == "execute_drone_command":
            # Map user-friendly command to Foundry action type
            action_map = {
                "hold": "drone_hold",
                "abort": "drone_abort",
                "reroute": "drone_reroute",
                "rtb": "drone_return_to_base",
            }
            action = action_map.get(inputs["command"], inputs["command"])
            params = {"droneId": inputs["drone_id"]}
            params.update(inputs.get("parameters") or {})
            result = foundry.execute_action(action, params)
            return json.dumps({"status": "executed", "action": action, "result": result})

        else:
            return json.dumps({"error": f"Unknown tool: {name}"})

    except Exception as e:
        return json.dumps({"error": str(e)})


# ── REST endpoints ─────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return FileResponse("static/index.html")


@app.get("/api/drones")
async def api_list_drones():
    drones = foundry.list_drones()
    return {"drones": drones}


@app.get("/api/telemetry/{drone_id}")
async def api_telemetry(
    drone_id: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
):
    rows = foundry.get_telemetry(drone_id, start_time, end_time)
    return {"drone_id": drone_id, "count": len(rows), "telemetry": rows}


@app.get("/api/missions")
async def api_missions():
    missions = foundry.get_missions()
    return {"missions": missions}


class CommandRequest(BaseModel):
    text: str
    drone_id: Optional[str] = None
    conversation_history: list = []


class CommandResponse(BaseModel):
    response: str
    actions_taken: list[str]
    updated_history: list


@app.post("/api/command", response_model=CommandResponse)
async def api_command(req: CommandRequest):
    """Process a voice/text command through Claude with Foundry tool access."""
    if not ANTHROPIC_API_KEY:
        raise HTTPException(503, "ANTHROPIC_API_KEY not configured")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    messages = list(req.conversation_history)
    messages.append({"role": "user", "content": req.text})

    actions_taken = []

    # Agentic loop: Claude calls tools until it has a final answer
    while True:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=CLAUDE_TOOLS,
            messages=messages,
        )

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            text = next(
                (b.text for b in response.content if hasattr(b, "text")), ""
            )
            return CommandResponse(
                response=text,
                actions_taken=actions_taken,
                updated_history=messages,
            )

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = execute_tool(block.name, block.input)
                    actions_taken.append(f"{block.name}({json.dumps(block.input)})")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            messages.append({"role": "user", "content": tool_results})

        else:
            # Unexpected stop reason — return whatever we have
            text = next(
                (b.text for b in response.content if hasattr(b, "text")), ""
            )
            return CommandResponse(
                response=text or "Done.",
                actions_taken=actions_taken,
                updated_history=messages,
            )


# ── WebSocket: live telemetry stream ──────────────────────────────────────

@app.websocket("/ws/telemetry/{drone_id}")
async def ws_telemetry(websocket: WebSocket, drone_id: str):
    """
    Streams telemetry for a drone second-by-second.
    Query params: ?start=<ISO>&end=<ISO>&speed=1  (speed multiplier)
    """
    await websocket.accept()
    speed = float(websocket.query_params.get("speed", 1))
    start_time = websocket.query_params.get("start")
    end_time = websocket.query_params.get("end")

    try:
        rows = foundry.get_telemetry(drone_id, start_time, end_time)
        if not rows:
            await websocket.send_json({"error": "No telemetry found", "drone_id": drone_id})
            await websocket.close()
            return

        await websocket.send_json({"type": "init", "total_points": len(rows), "drone_id": drone_id})

        for i, row in enumerate(rows):
            await websocket.send_json({"type": "telemetry", "index": i, "data": row})
            await asyncio.sleep(1.0 / speed)

        await websocket.send_json({"type": "complete", "drone_id": drone_id})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
