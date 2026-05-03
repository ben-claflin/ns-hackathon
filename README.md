# Drone C2 Platform

Command and control platform for drone operations. Supports both live Foundry connection and GitHub-hosted sample data.

## Quick Start (No Foundry Access Needed)

The platform works out-of-the-box with sample data stored in `/data/` folder:

```bash
# 1. Install
pip install -r requirements.txt

# 2. Add Anthropic API key
cp .env.example .env
# Edit .env: add your ANTHROPIC_API_KEY

# 3. Run
uvicorn backend:app --reload --port 8000

# 4. Open frontend
# http://localhost:8000
```

The backend automatically uses PostgreSQL when `DATABASE_URL` or `POSTGRES_HOST` is configured. If PostgreSQL is not configured or reachable, it falls back to JSON files in `/data/`.

The frontend is served by the backend at `http://localhost:8000` and uses that same origin automatically. Click the backend label in the header only when you intentionally need to point the page at a different backend.

### PostgreSQL Data

Configure either a single connection URL or the individual connection fields:

```bash
DATABASE_URL=postgresql://postgres:password@localhost:5432/postgres
POSTGRES_SCHEMA=hackathon
```

Expected tables are `hackathon.sensors`, `hackathon.threats`, and `hackathon.responses`. The app reads live rows for the map, list panels, recommendations, and voice/text commands. Sensor repositioning, bearing, range, and response asset status are written back when those columns exist in the database schema.

### Updating Sample Data

Edit the JSON files directly or generate new data:

```bash
# Generate 5 drones, 600 seconds of telemetry
python generate_sample_data.py --drones 5 --duration 600 --output data/

# Commit and push
git add data/
git commit -m "Update sample drone data"
git push
```

The frontend will pick up changes automatically.

## Deployment

**Frontend:** Deployed to GitHub Pages at `https://ben-claflin.github.io/ns-hackathon/`

**Backend:** Runs locally at the hackathon venue (required for Foundry IP allowlist)

---

## Live Foundry Connection (Optional, requires venue network)

If you have access to the Foundry instance and are on an allowlisted network:

### 1. Discover Foundry resource IDs
```bash
# Run this from the hackathon venue WiFi
python discover.py
# Copy the output RIDs into .env
```

### 2. Start backend
```bash
uvicorn backend:app --reload --host 0.0.0.0 --port 8000
```

The backend will automatically use Foundry if the RIDs are configured and the IP is allowlisted. Otherwise, it falls back to GitHub data.

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
