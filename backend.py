"""
Drone C2 + Counter-UAS Backend — FastAPI + Claude AI
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

from github_data_client import GitHubDataClient

load_dotenv()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

def _make_data_client():
    """Use local JSON files in the repository as the app data store."""
    print("[INFO] Using local JSON file data source")
    return GitHubDataClient()

# ── Claude tools ───────────────────────────────────────────────────────────

CLAUDE_TOOLS = [
    {
        "name": "list_drones",
        "description": "Return all friendly drone IDs currently tracked.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_drone_telemetry",
        "description": "Retrieve position, speed, battery, and status for a friendly drone.",
        "input_schema": {
            "type": "object",
            "properties": {
                "drone_id": {"type": "string"},
                "start_time": {"type": "string"},
                "end_time": {"type": "string"},
            },
            "required": ["drone_id"],
        },
    },
    {
        "name": "get_missions",
        "description": "Return all friendly drone missions with waypoints and status.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_threats",
        "description": "Return all detected hostile/suspected UAS with full track history including per-point speed and confidence.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_sensors",
        "description": "Return all sensors (acoustic, RF, imagery) with position, orientation, FOV, range, and active detections.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_detections",
        "description": "Return correlated sensor-to-threat detections with confidence values.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_response_assets",
        "description": "Return all friendly response assets: interceptor drones, C-RAM batteries, and Patriot battery — with capabilities, status, and position.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "recommend_counteraction",
        "description": (
            "Analyse a specific threat against available response assets. "
            "Returns range, altitude, speed compatibility, probability of kill, "
            "and response time for each asset so you can recommend the best option. "
            "Always call this before recommending a counteraction."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "threat_id": {"type": "string", "description": "e.g. THREAT-001"}
            },
            "required": ["threat_id"],
        },
    },
    {
        "name": "update_sensor",
        "description": "Reposition or retask a sensor: change bearing, type, modality, active state.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sensor_id": {"type": "string"},
                "orientation": {"type": "number"},
                "active": {"type": "boolean"},
                "position": {
                    "type": "object",
                    "properties": {
                        "latitude": {"type": "number"},
                        "longitude": {"type": "number"},
                    },
                },
                "modality": {"type": "string", "enum": ["EO", "IR"]},
                "type": {"type": "string", "enum": ["ACOUSTIC", "RF", "IMAGERY"]},
            },
            "required": ["sensor_id"],
        },
    },
    {
        "name": "deploy_asset",
        "description": "Order a response asset to engage a threat. Updates asset status to ENGAGED.",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string"},
                "threat_id": {"type": "string"},
            },
            "required": ["asset_id", "threat_id"],
        },
    },
    {
        "name": "execute_drone_command",
        "description": "Execute a command on a friendly drone: hold, abort, reroute, rtb.",
        "input_schema": {
            "type": "object",
            "properties": {
                "drone_id": {"type": "string"},
                "command": {"type": "string", "enum": ["hold", "abort", "reroute", "rtb"]},
                "parameters": {"type": "object"},
            },
            "required": ["drone_id", "command"],
        },
    },
    {
        "name": "execute_sensor_command",
        "description": "Activate or deactivate a sensor by ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sensor_id": {"type": "string"},
                "command": {"type": "string", "enum": ["activate", "deactivate"]},
            },
            "required": ["sensor_id", "command"],
        },
    },
]

SYSTEM_PROMPT = """You are an AI operator for a Counter-UAS (C-UAS) Command & Control platform.

ASSETS UNDER YOUR COMMAND:
- Friendly UAS: reconnaissance/patrol drones
- Interceptor Drones (UAS_INTERCEPTOR): net-capture, effective against slow/low UAS ≤25 m/s, range 5 km
- C-RAM (CRAM): Phalanx 20mm CIWS, very fast response (8s), effective at ≤2 km, handles up to 300 m/s targets
- Patriot Battery (PATRIOT): PAC-3 MSE hit-to-kill, 160 km range, best for high-speed or high-altitude threats

COUNTER-ACTION DECISION LOGIC:
1. Slow quadrotor UAS (≤15 m/s, low alt): prefer Interceptor Drone (non-kinetic) or C-RAM if close
2. Fast fixed-wing UAS (15–50 m/s, low alt): prefer C-RAM if within 2 km, else Interceptor
3. High-altitude or very fast threats: Patriot
4. Always call recommend_counteraction before advising — it computes exact range/altitude/speed compatibility
5. Prefer non-kinetic (interceptor) over kinetic (C-RAM, Patriot) when target is slow and interceptor is available

RESPONSE STYLE: Military brevity. Lead with the recommendation, follow with rationale.
Example: "Recommend CRAM-01 for immediate engagement. THREAT-001 at 57m, 20 m/s — within C-RAM envelope, 800m separation. Pk 0.92. UAS interceptors not optimal for fixed-wing at this speed."
"""


# ── App ────────────────────────────────────────────────────────────────────

data_client: Optional[GitHubDataClient] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global data_client
    data_client = _make_data_client()
    yield
    data_client.close()


app = FastAPI(title="C-UAS C2", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000", "http://127.0.0.1:8000",
        "http://localhost:3000", "https://ben-claflin.github.io",
    ],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory="static"), name="static")


# ── Tool execution ─────────────────────────────────────────────────────────

def execute_tool(name: str, inputs: dict) -> str:
    try:
        if name == "list_drones":
            return json.dumps({"drones": data_client.list_drones()})
        elif name == "get_drone_telemetry":
            rows = data_client.get_telemetry(inputs["drone_id"], inputs.get("start_time"), inputs.get("end_time"))
            return json.dumps({"count": len(rows), "telemetry": rows[:20]})
        elif name == "get_missions":
            return json.dumps({"missions": data_client.get_missions()})
        elif name == "get_threats":
            return json.dumps({"threats": data_client.get_threats()})
        elif name == "get_sensors":
            return json.dumps({"sensors": data_client.get_sensors()})
        elif name == "get_detections":
            return json.dumps({"detections": data_client.get_detections()})
        elif name == "get_response_assets":
            return json.dumps({"assets": data_client.get_response_assets()})
        elif name == "recommend_counteraction":
            return json.dumps(data_client.recommend_counteraction(inputs["threat_id"]))
        elif name == "update_sensor":
            sid = inputs.pop("sensor_id")
            updated = data_client.update_sensor(sid, inputs)
            if not updated:
                return json.dumps({"error": f"Sensor {sid} not found"})
            return json.dumps({"status": "updated", "sensor": updated})
        elif name == "deploy_asset":
            result = data_client.execute_action("asset_deploy", {
                "assetId": inputs["asset_id"],
                "threatId": inputs["threat_id"],
            })
            asset = data_client.get_response_asset(inputs["asset_id"])
            return json.dumps({"status": "deployed", "result": result, "asset": asset})
        elif name == "execute_drone_command":
            action_map = {"hold": "drone_hold", "abort": "drone_abort",
                          "reroute": "drone_reroute", "rtb": "drone_return_to_base"}
            params = {"droneId": inputs["drone_id"]}
            params.update(inputs.get("parameters") or {})
            return json.dumps(data_client.execute_action(action_map.get(inputs["command"], inputs["command"]), params))
        elif name == "execute_sensor_command":
            action = "sensor_activate" if inputs["command"] == "activate" else "sensor_deactivate"
            return json.dumps(data_client.execute_action(action, {"sensorId": inputs["sensor_id"]}))
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
    return {"drones": data_client.list_drones()}

@app.get("/api/telemetry/{drone_id}")
async def api_telemetry(drone_id: str, start_time: Optional[str] = None, end_time: Optional[str] = None):
    rows = data_client.get_telemetry(drone_id, start_time, end_time)
    return {"drone_id": drone_id, "count": len(rows), "telemetry": rows}

@app.get("/api/missions")
async def api_missions():
    return {"missions": data_client.get_missions()}

@app.get("/api/sensors")
async def api_sensors():
    return {"sensors": data_client.get_sensors()}

@app.put("/api/sensors/{sensor_id}")
async def api_update_sensor(sensor_id: str, patch: dict):
    updated = data_client.update_sensor(sensor_id, patch)
    if not updated:
        raise HTTPException(404, f"Sensor {sensor_id} not found")
    return updated

@app.get("/api/threats")
async def api_threats():
    return {"threats": data_client.get_threats()}

@app.get("/api/threats/active")
async def api_active_threats():
    return {"threats": data_client.get_active_threats()}

@app.get("/api/detections")
async def api_detections():
    return {"detections": data_client.get_detections()}

@app.get("/api/response-assets")
async def api_response_assets():
    return {"assets": data_client.get_response_assets()}

@app.put("/api/response-assets/{asset_id}")
async def api_update_asset(asset_id: str, patch: dict):
    updated = data_client.update_response_asset(asset_id, patch)
    if not updated:
        raise HTTPException(404, f"Asset {asset_id} not found")
    return updated

@app.get("/api/recommend/{threat_id}")
async def api_recommend(threat_id: str):
    return data_client.recommend_counteraction(threat_id)

@app.get("/api/map-center")
async def api_map_center():
    if hasattr(data_client, "get_map_center"):
        return data_client.get_map_center()
    return {"latitude": 38.905, "longitude": -77.037, "zoom": 13}

@app.get("/api/status")
async def api_status():
    return {
        "data_source": data_client.__class__.__name__ if data_client else "uninitialized",
        "storage": "local_json",
        "voice_ai_configured": bool(ANTHROPIC_API_KEY),
    }


# ── Command (Claude agentic loop) ──────────────────────────────────────────

class CommandRequest(BaseModel):
    text: str
    drone_id: Optional[str] = None
    conversation_history: list = []

class CommandResponse(BaseModel):
    response: str
    actions_taken: list[str]
    updated_history: list
    sensor_updates: list[dict] = []
    asset_updates: list[dict] = []

@app.post("/api/command", response_model=CommandResponse)
async def api_command(req: CommandRequest):
    if not ANTHROPIC_API_KEY:
        raise HTTPException(503, "ANTHROPIC_API_KEY not configured")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    messages = list(req.conversation_history)
    messages.append({"role": "user", "content": req.text})

    actions_taken, sensor_updates, asset_updates = [], [], []

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
            text = next((b.text for b in response.content if hasattr(b, "text")), "")
            return CommandResponse(response=text, actions_taken=actions_taken,
                                   updated_history=messages, sensor_updates=sensor_updates,
                                   asset_updates=asset_updates)

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result_str = execute_tool(block.name, dict(block.input))
                    actions_taken.append(f"{block.name}({json.dumps(block.input)})")
                    data = json.loads(result_str)
                    if block.name == "update_sensor" and "sensor" in data:
                        sensor_updates.append(data["sensor"])
                    if block.name == "deploy_asset" and "asset" in data:
                        asset_updates.append(data["asset"])
                    tool_results.append({"type": "tool_result",
                                         "tool_use_id": block.id, "content": result_str})
            messages.append({"role": "user", "content": tool_results})
        else:
            text = next((b.text for b in response.content if hasattr(b, "text")), "")
            return CommandResponse(response=text or "Done.", actions_taken=actions_taken,
                                   updated_history=messages, sensor_updates=sensor_updates,
                                   asset_updates=asset_updates)


# ── WebSocket telemetry stream ─────────────────────────────────────────────

@app.websocket("/ws/telemetry/{drone_id}")
async def ws_telemetry(websocket: WebSocket, drone_id: str):
    await websocket.accept()
    speed = float(websocket.query_params.get("speed", 1))
    try:
        rows = data_client.get_telemetry(
            drone_id,
            websocket.query_params.get("start"),
            websocket.query_params.get("end"),
        )
        if not rows:
            await websocket.send_json({"error": "No telemetry found"})
            return
        await websocket.send_json({"type": "init", "total_points": len(rows), "drone_id": drone_id})
        for i, row in enumerate(rows):
            await websocket.send_json({"type": "telemetry", "index": i, "data": row})
            await asyncio.sleep(1.0 / speed)
        await websocket.send_json({"type": "complete"})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
