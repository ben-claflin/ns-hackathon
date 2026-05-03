"""
Data client that reads drone telemetry and missions from JSON files stored in GitHub.
Use this instead of Foundry when the IP allowlist is blocking access.

Data is stored in /data/ folder in the repo.
"""
import json
import os
from typing import Optional
from pathlib import Path


class GitHubDataClient:
    """Read-only client for GitHub-hosted drone data."""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.telemetry_file = self.data_dir / "drone_telemetry.json"
        self.missions_file = self.data_dir / "drone_missions.json"
        self._load_data()

    def _load_data(self):
        """Load all data from JSON files into memory."""
        self.telemetry = []
        self.missions = []

        if self.telemetry_file.exists():
            with open(self.telemetry_file) as f:
                self.telemetry = json.load(f)

        if self.missions_file.exists():
            with open(self.missions_file) as f:
                self.missions = json.load(f)

    def reload(self):
        """Reload data from disk (for updates)."""
        self._load_data()

    # ── Telemetry queries ──────────────────────────────────────────────────

    def get_telemetry(
        self,
        drone_id: str,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        limit: int = 3600,
    ) -> list[dict]:
        """Filter telemetry for a drone by ID and optional time range."""
        rows = [t for t in self.telemetry if t.get("droneId") == drone_id]

        if start_time:
            rows = [t for t in rows if t.get("timestamp", "") >= start_time]
        if end_time:
            rows = [t for t in rows if t.get("timestamp", "") <= end_time]

        # Sort by timestamp ascending
        rows.sort(key=lambda x: x.get("timestamp", ""))

        return rows[:limit]

    def list_drones(self) -> list[str]:
        """Return distinct drone IDs."""
        ids = {t.get("droneId") for t in self.telemetry}
        return sorted(filter(None, ids))

    # ── Mission queries ────────────────────────────────────────────────────

    def get_missions(self) -> list[dict]:
        """Return all missions."""
        return self.missions

    def get_mission_by_id(self, mission_id: str) -> Optional[dict]:
        """Get a single mission by ID."""
        return next((m for m in self.missions if m.get("missionId") == mission_id), None)

    def get_missions_for_drone(self, drone_id: str) -> list[dict]:
        """Get all missions assigned to a drone."""
        return [m for m in self.missions if m.get("droneId") == drone_id]

    # ── Action execution (no-op for GitHub data) ──────────────────────────

    def execute_action(self, action_type: str, parameters: dict) -> dict:
        """
        GitHub data is read-only.
        Log the action request but don't actually execute it.
        """
        return {
            "status": "logged",
            "action": action_type,
            "parameters": parameters,
            "note": "GitHub data client is read-only. To execute actions, connect to live Foundry.",
        }

    def close(self):
        """No-op for file-based client."""
        pass
