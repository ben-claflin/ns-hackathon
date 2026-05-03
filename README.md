# Drone C2 Platform

Command and control platform for drone operations, powered by Palantir Foundry + Claude AI.

## Architecture

```
Voice Input (Web Speech API)
        ↓
   FastAPI Backend (backend.py)
        ↓
   Claude API (claude-sonnet-4-6)  ←── tool calls
        ↓
   Foundry Client (foundry_client.py)
        ↓
   Palantir Foundry (nshackathon.palantirfoundry.com)
   Datasets: drone_telemetry_points, drone_missions
```

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure environment
```bash
cp .env.example .env
# Edit .env — add your ANTHROPIC_API_KEY
# FOUNDRY_TOKEN and FOUNDRY_HOST are already set
```

### 3. Discover Foundry resource IDs (run from hackathon venue network)
```bash
python discover.py
# Copy the output lines into .env
```

### 4. Start the server
```bash
uvicorn backend:app --reload --host 0.0.0.0 --port 8000
```

Open http://localhost:8000

## Usage

1. Click **LOAD DRONES** to pull asset list from Foundry
2. Click a drone to load its telemetry onto the map
3. Use the **timeline scrubber** to step through second-by-second history
4. Adjust **playback speed** (1x / 5x / 30x / 60x) and hit **PLAY**
5. Use the **voice button** or type commands like:
   - *"Show me Drone Alpha's last known position"*
   - *"What is Drone 001's battery level?"*
   - *"Hold Drone 002 in place"*
   - *"Abort mission and return Drone 001 to base"*

## MCP Server (Claude Code integration)

To let Claude Code directly query Foundry during development:

```bash
# Add to ~/.claude/mcp.json
{
  "mcpServers": {
    "foundry-drone-c2": {
      "command": "python",
      "args": ["mcp_server.py"],
      "cwd": "/path/to/ns-hackathon"
    }
  }
}
```

## Foundry Access Note

The Foundry instance at `nshackathon.palantirfoundry.com` uses IP allowlisting.
All API calls must originate from the hackathon venue network (WiFi or VPN).
Run the backend locally at the venue — do not deploy to a cloud host.

## Key Files

| File | Purpose |
|------|---------|
| `foundry_client.py` | Foundry REST API wrapper (ontology + datasets + actions) |
| `discover.py` | One-time discovery script to find dataset/ontology RIDs |
| `backend.py` | FastAPI server with Claude tool-use integration |
| `mcp_server.py` | MCP server for Claude Code direct Foundry access |
| `static/index.html` | C2 frontend (map, timeline, voice commands) |
