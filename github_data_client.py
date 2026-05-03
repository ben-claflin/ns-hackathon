"""
Data client reading drone telemetry, missions, sensors, threats, and
response assets from JSON files in /data/. Supports in-memory write-back
for sensor repositioning and modality changes during a session.
"""
import json
import math
import copy
from typing import Optional
from pathlib import Path


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in metres between two lat/lon points."""
    R = 6_371_000
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2 +
         math.cos(lat1 * p) * math.cos(lat2 * p) *
         math.sin((lon2 - lon1) * p / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


class GitHubDataClient:

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self._load_data()

    def _load_data(self):
        self.telemetry       = self._read("drone_telemetry.json",   [])
        self.missions        = self._read("drone_missions.json",    [])
        self.sensors         = self._read("sensors.json",           [])
        self.threats         = self._read("threats.json",           [])
        self.response_assets = self._read("response_assets.json",   [])

    def _read(self, filename, default):
        p = self.data_dir / filename
        if p.exists():
            with open(p) as f:
                return json.load(f)
        return default

    def reload(self):
        self._load_data()

    # ── Telemetry ──────────────────────────────────────────────────────────

    def get_telemetry(self, drone_id, start_time=None, end_time=None, limit=3600):
        rows = [t for t in self.telemetry if t.get("droneId") == drone_id]
        if start_time:
            rows = [t for t in rows if t.get("timestamp", "") >= start_time]
        if end_time:
            rows = [t for t in rows if t.get("timestamp", "") <= end_time]
        rows.sort(key=lambda x: x.get("timestamp", ""))
        return rows[:limit]

    def list_drones(self):
        return sorted(filter(None, {t.get("droneId") for t in self.telemetry}))

    # ── Missions ───────────────────────────────────────────────────────────

    def get_missions(self):
        return self.missions

    # ── Sensors ────────────────────────────────────────────────────────────

    def get_sensors(self):
        return self.sensors

    def get_sensor(self, sensor_id):
        return next((s for s in self.sensors if s["sensorId"] == sensor_id), None)

    def update_sensor(self, sensor_id, patch):
        sensor = self.get_sensor(sensor_id)
        if not sensor:
            return None
        allowed = {"position", "orientation", "active", "fov", "range",
                   "type", "modality", "sensitivity", "frequency_band"}
        for k, v in patch.items():
            if k in allowed:
                sensor[k] = v
        return copy.deepcopy(sensor)

    # ── Threats ────────────────────────────────────────────────────────────

    def get_threats(self):
        return self.threats

    def get_threat(self, threat_id):
        return next((t for t in self.threats if t["threatId"] == threat_id), None)

    def get_active_threats(self):
        return [t for t in self.threats if t.get("status") in ("TRACKING", "DETECTED")]

    # ── Detections ─────────────────────────────────────────────────────────

    def get_detections(self):
        out = []
        for sensor in self.sensors:
            for tid in sensor.get("detections", []):
                threat = self.get_threat(tid)
                if threat and threat.get("track"):
                    latest = threat["track"][-1]
                    out.append({
                        "sensorId":   sensor["sensorId"],
                        "sensorType": sensor["type"],
                        "threatId":   tid,
                        "confidence": latest.get("confidence", threat.get("confidence", 0)),
                        "threatLevel": threat.get("threatLevel"),
                        "latitude":   latest.get("latitude"),
                        "longitude":  latest.get("longitude"),
                        "timestamp":  latest.get("timestamp"),
                    })
        return out

    # ── Response assets ────────────────────────────────────────────────────

    def get_response_assets(self):
        return self.response_assets

    def get_response_asset(self, asset_id):
        return next((a for a in self.response_assets if a["assetId"] == asset_id), None)

    def update_response_asset(self, asset_id, patch):
        asset = self.get_response_asset(asset_id)
        if not asset:
            return None
        allowed = {"status", "position", "heading", "status_detail"}
        for k, v in patch.items():
            if k in allowed:
                asset[k] = v
        return copy.deepcopy(asset)

    # ── Counter-action recommendation ──────────────────────────────────────

    def recommend_counteraction(self, threat_id: str) -> dict:
        """
        Compute which response assets can engage a threat and rank them.
        Returns structured data for Claude to reason over.
        """
        threat = self.get_threat(threat_id)
        if not threat:
            return {"error": f"Threat {threat_id} not found"}

        if not threat.get("track"):
            return {"error": "No track data for threat"}

        latest = threat["track"][-1]
        t_lat, t_lon = latest["latitude"], latest["longitude"]
        t_alt = latest.get("altitude", 0)
        t_speed = latest.get("speed", 0)
        t_conf = latest.get("confidence", threat.get("confidence", 0))

        candidates = []
        for asset in self.response_assets:
            if asset["status"] not in ("READY", "STANDBY"):
                continue
            caps = asset.get("capabilities", {})
            a_lat = asset["position"]["latitude"]
            a_lon = asset["position"]["longitude"]
            dist_m = _haversine_m(a_lat, a_lon, t_lat, t_lon)

            in_range   = dist_m <= caps.get("maxRangeM", 0)
            alt_ok     = caps.get("minAltM", 0) <= t_alt <= caps.get("maxAltM", 99999)
            speed_ok   = t_speed <= caps.get("maxTargetSpeedMs", 0)

            candidates.append({
                "assetId":        asset["assetId"],
                "name":           asset["name"],
                "type":           asset["type"],
                "status":         asset["status"],
                "distanceM":      round(dist_m),
                "inRange":        in_range,
                "altitudeOk":     alt_ok,
                "speedOk":        speed_ok,
                "canEngage":      in_range and alt_ok and speed_ok,
                "pkill":          caps.get("pkill", 0),
                "responseTimeSec": caps.get("responseTimeSec"),
                "method":         caps.get("method"),
                "bestAgainst":    caps.get("bestAgainst", []),
            })

        candidates.sort(key=lambda x: (not x["canEngage"], x["distanceM"]))

        return {
            "threatId":     threat_id,
            "threatLevel":  threat.get("threatLevel"),
            "classification": threat.get("classification"),
            "estimatedType": threat.get("estimatedType"),
            "latestPosition": {"lat": t_lat, "lon": t_lon, "altM": t_alt},
            "speedMs":      t_speed,
            "confidence":   t_conf,
            "candidates":   candidates,
        }

    # ── Actions ────────────────────────────────────────────────────────────

    def execute_action(self, action_type, parameters):
        handlers = {
            "drone_hold":            lambda p: f"Hold acknowledged for {p.get('droneId')}",
            "drone_abort":           lambda p: f"Abort — RTB initiated for {p.get('droneId')}",
            "drone_reroute":         lambda p: f"Reroute acknowledged for {p.get('droneId')}",
            "drone_return_to_base":  lambda p: f"RTB initiated for {p.get('droneId')}",
            "sensor_activate":       self._sensor_activate,
            "sensor_deactivate":     self._sensor_deactivate,
            "asset_deploy":          self._asset_deploy,
        }
        fn = handlers.get(action_type)
        result = fn(parameters) if fn else f"Action '{action_type}' logged"
        return {"status": "logged", "action": action_type,
                "result": result, "parameters": parameters}

    def _sensor_activate(self, p):
        self.update_sensor(p.get("sensorId"), {"active": True})
        return f"Sensor {p.get('sensorId')} activated"

    def _sensor_deactivate(self, p):
        self.update_sensor(p.get("sensorId"), {"active": False})
        return f"Sensor {p.get('sensorId')} deactivated"

    def _asset_deploy(self, p):
        asset_id = p.get("assetId")
        threat_id = p.get("threatId", "unknown")
        self.update_response_asset(asset_id, {
            "status": "ENGAGED",
            "status_detail": f"Engaging {threat_id}"
        })
        return f"{asset_id} deployed against {threat_id}"

    def close(self):
        pass
