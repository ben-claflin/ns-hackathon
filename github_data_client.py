"""
Data client that reads drone telemetry, missions, sensors, and threats
from JSON files stored in /data/. Supports in-memory write-back for
sensor repositioning and modality changes during a session.
"""
import json
import copy
from typing import Optional
from pathlib import Path


class GitHubDataClient:

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self._load_data()

    def _load_data(self):
        self.telemetry = self._read("drone_telemetry.json", [])
        self.missions  = self._read("drone_missions.json",  [])
        self.sensors   = self._read("sensors.json",         [])
        self.threats   = self._read("threats.json",         [])

    def _read(self, filename: str, default):
        path = self.data_dir / filename
        if path.exists():
            with open(path) as f:
                return json.load(f)
        return default

    def reload(self):
        self._load_data()

    # ── Telemetry ──────────────────────────────────────────────────────────

    def get_telemetry(self, drone_id: str, start_time: Optional[str] = None,
                      end_time: Optional[str] = None, limit: int = 3600) -> list[dict]:
        rows = [t for t in self.telemetry if t.get("droneId") == drone_id]
        if start_time:
            rows = [t for t in rows if t.get("timestamp", "") >= start_time]
        if end_time:
            rows = [t for t in rows if t.get("timestamp", "") <= end_time]
        rows.sort(key=lambda x: x.get("timestamp", ""))
        return rows[:limit]

    def list_drones(self) -> list[str]:
        ids = {t.get("droneId") for t in self.telemetry}
        return sorted(filter(None, ids))

    # ── Missions ───────────────────────────────────────────────────────────

    def get_missions(self) -> list[dict]:
        return self.missions

    def get_missions_for_drone(self, drone_id: str) -> list[dict]:
        return [m for m in self.missions if m.get("droneId") == drone_id]

    # ── Sensors ────────────────────────────────────────────────────────────

    def get_sensors(self) -> list[dict]:
        return self.sensors

    def get_sensor(self, sensor_id: str) -> Optional[dict]:
        return next((s for s in self.sensors if s["sensorId"] == sensor_id), None)

    def update_sensor(self, sensor_id: str, patch: dict) -> Optional[dict]:
        """Apply a partial update to a sensor (in-memory). Returns updated sensor."""
        sensor = self.get_sensor(sensor_id)
        if not sensor:
            return None
        allowed = {"position", "orientation", "active", "fov", "range",
                   "type", "modality", "sensitivity", "frequency_band"}
        for key, val in patch.items():
            if key in allowed:
                sensor[key] = val
        return copy.deepcopy(sensor)

    # ── Threats ────────────────────────────────────────────────────────────

    def get_threats(self) -> list[dict]:
        return self.threats

    def get_threat(self, threat_id: str) -> Optional[dict]:
        return next((t for t in self.threats if t["threatId"] == threat_id), None)

    def get_active_threats(self) -> list[dict]:
        return [t for t in self.threats if t.get("status") in ("TRACKING", "DETECTED")]

    # ── Detections summary ─────────────────────────────────────────────────

    def get_detections(self) -> list[dict]:
        """Cross-reference sensors with threats they've detected."""
        detections = []
        for sensor in self.sensors:
            for threat_id in sensor.get("detections", []):
                threat = self.get_threat(threat_id)
                if threat and threat.get("track"):
                    latest = threat["track"][-1]
                    detections.append({
                        "sensorId": sensor["sensorId"],
                        "sensorType": sensor["type"],
                        "threatId": threat_id,
                        "confidence": threat.get("confidence", 0),
                        "threatLevel": threat.get("threatLevel", "UNKNOWN"),
                        "latitude": latest.get("latitude"),
                        "longitude": latest.get("longitude"),
                        "timestamp": latest.get("timestamp"),
                    })
        return detections

    # ── Action execution ───────────────────────────────────────────────────

    def execute_action(self, action_type: str, parameters: dict) -> dict:
        """In-memory action stubs — sensor repositioning is handled by update_sensor."""
        action_map = {
            "drone_hold":           "Hold acknowledged",
            "drone_abort":          "Abort acknowledged — RTB initiated",
            "drone_reroute":        "Reroute acknowledged",
            "drone_return_to_base": "Return-to-base initiated",
            "sensor_activate":      self._sensor_activate(parameters),
            "sensor_deactivate":    self._sensor_deactivate(parameters),
        }
        result = action_map.get(action_type, f"Action '{action_type}' logged")
        if callable(result):
            result = result
        return {"status": "logged", "action": action_type,
                "result": result, "parameters": parameters}

    def _sensor_activate(self, params: dict):
        sid = params.get("sensorId")
        if sid:
            self.update_sensor(sid, {"active": True})
        return f"Sensor {sid} activated"

    def _sensor_deactivate(self, params: dict):
        sid = params.get("sensorId")
        if sid:
            self.update_sensor(sid, {"active": False})
        return f"Sensor {sid} deactivated"

    def close(self):
        pass
