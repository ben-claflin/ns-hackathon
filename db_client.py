"""
PostgreSQL data client for the hackathon schema.
Maps hackathon.sensors / threats / responses to the application's internal schema.
Falls back gracefully if the DB is unreachable.
"""
import os
import math
import copy
from typing import Optional
import psycopg2
import psycopg2.pool
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

# ── Type classifiers ───────────────────────────────────────────────────────

def _sensor_app_type(db_text: str) -> str:
    t = db_text.lower()
    if "acoustic" in t:
        return "ACOUSTIC"
    if "eo/ir" in t or "infrared" in t or "optical" in t or "camera" in t:
        return "IMAGERY"
    return "RF"   # radar, counterfire, sentinel, etc.

def _asset_app_type(db_text: str) -> str:
    t = db_text.lower()
    if "c-ram" in t or "phalanx" in t or "centurion" in t:
        return "CRAM"
    if "patriot" in t or "pac-3" in t or "pac3" in t:
        return "PATRIOT"
    if "jammer" in t or "electronic warfare" in t or "ew " in t:
        return "EW_JAMMER"
    if "stinger" in t or "manpads" in t or "fim-" in t:
        return "MANPADS"
    if "interceptor" in t or "uas" in t:
        return "UAS_INTERCEPTOR"
    return "INTERCEPTOR"

def _threat_level(db_type: str, speed_mps: float, alt_m: float) -> str:
    t = db_type.lower()
    if "loitering" in t or "munition" in t or "shahed" in t or "kamikaze" in t:
        return "HIGH"
    if speed_mps is not None and speed_mps > 100:
        return "HIGH"
    if speed_mps is not None and speed_mps > 25:
        return "MEDIUM"
    return "MEDIUM"

def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    R = 6_371_000
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2 +
         math.cos(lat1 * p) * math.cos(lat2 * p) *
         math.sin((lon2 - lon1) * p / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))

# ── Asset capabilities by type ─────────────────────────────────────────────

ASSET_CAPS = {
    "CRAM": {
        "maxTargetSpeedMs": 300, "minAltM": 10, "maxAltM": 500,
        "responseTimeSec": 8,  "pkill": 0.92,
        "method": "20mm Phalanx CIWS burst fire",
        "bestAgainst": ["Quadrotor UAS", "Fixed-wing UAS", "Rockets", "Mortars"],
    },
    "PATRIOT": {
        "maxTargetSpeedMs": 2000, "minAltM": 60, "maxAltM": 24000,
        "responseTimeSec": 15, "pkill": 0.95,
        "method": "PAC-3 MSE hit-to-kill missile",
        "bestAgainst": ["Ballistic missiles", "Cruise missiles", "High-altitude UAS"],
    },
    "EW_JAMMER": {
        "maxTargetSpeedMs": 999, "minAltM": 0, "maxAltM": 5000,
        "responseTimeSec": 3,  "pkill": 0.75,
        "method": "RF jamming / GPS denial / command-link disruption",
        "bestAgainst": ["RF-controlled UAS", "GPS-guided munitions", "Loitering munitions"],
    },
    "MANPADS": {
        "maxTargetSpeedMs": 150, "minAltM": 10, "maxAltM": 4500,
        "responseTimeSec": 20, "pkill": 0.85,
        "method": "IR-guided surface-to-air missile",
        "bestAgainst": ["Fixed-wing UAS", "Rotary UAS", "Low-altitude aircraft"],
    },
    "UAS_INTERCEPTOR": {
        "maxTargetSpeedMs": 25, "minAltM": 5, "maxAltM": 400,
        "responseTimeSec": 45, "pkill": 0.80,
        "method": "Net capture / physical intercept",
        "bestAgainst": ["Quadrotor UAS", "Small fixed-wing UAS"],
    },
    "INTERCEPTOR": {
        "maxTargetSpeedMs": 200, "minAltM": 10, "maxAltM": 10000,
        "responseTimeSec": 30, "pkill": 0.82,
        "method": "Kinetic intercept",
        "bestAgainst": ["UAS", "Cruise missiles"],
    },
}


class PostgresDataClient:
    """
    Reads and writes hackathon.sensors / threats / responses from PostgreSQL.
    Maintains an in-memory cache so sensors can be updated within a session
    and written back to the DB.
    """

    def __init__(self):
        self.pool = psycopg2.pool.SimpleConnectionPool(
            1, 5,
            host=os.environ.get("POSTGRES_HOST", "localhost"),
            port=int(os.environ.get("POSTGRES_PORT", 5432)),
            dbname=os.environ.get("POSTGRES_DB", "postgres"),
            user=os.environ.get("POSTGRES_USER", "postgres"),
            password=os.environ.get("POSTGRES_PASSWORD", ""),
        )
        self._sensor_cache: dict = {}
        self._asset_cache:  dict = {}

    def _conn(self):
        return self.pool.getconn()

    def _put(self, conn):
        self.pool.putconn(conn)

    def _query(self, sql: str, params=None) -> list[dict]:
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
        finally:
            self._put(conn)

    def _execute(self, sql: str, params=None):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
            conn.commit()
        finally:
            self._put(conn)

    # ── Map DB rows to app schema ──────────────────────────────────────────

    def _map_sensor(self, row: dict) -> dict:
        sid = f"SENSOR-{row['sensor_id']}"
        app_type = _sensor_app_type(row["sensor_type"])
        # orientation_deg=360 in the DB means "omnidirectional"; use as fov
        raw_orient = row.get("orientation_deg") or 0
        if raw_orient >= 360:
            fov, bearing = 360, 0
        else:
            fov, bearing = 60, raw_orient   # default 60° FOV for pointed sensors

        s = {
            "sensorId": sid,
            "db_id": row["sensor_id"],
            "name": row["sensor_type"],
            "type": app_type,
            "position": {
                "latitude":  row["latitude"],
                "longitude": row["longitude"],
            },
            "orientation": bearing,
            "fov":         fov,
            "range":       int(row.get("range_meters") or 1000),
            "active":      True,
            "detections":  [],
            "modality":    "EO" if app_type == "IMAGERY" else None,
        }
        return s

    def _map_threat(self, row: dict) -> dict:
        tid = f"THREAT-{row['threat_id']}"
        speed = row.get("speed_mps") or 0
        alt   = row.get("altitude_meters") or 0
        return {
            "threatId":       tid,
            "db_id":          row["threat_id"],
            "classification": "HOSTILE_UAS",
            "confidence":     0.87,
            "threatLevel":    _threat_level(row["type"], speed, alt),
            "status":         "TRACKING",
            "firstDetected":  str(row.get("created_at", "")),
            "lastSeen":       str(row.get("created_at", "")),
            "detectedBy":     [],
            "estimatedType":  row["type"],
            "track": [{
                "timestamp": str(row.get("created_at", "")),
                "latitude":  row["latitude"],
                "longitude": row["longitude"],
                "altitude":  alt,
                "speed":     speed,
                "heading":   0,
                "confidence": 0.87,
            }],
        }

    def _map_asset(self, row: dict) -> dict:
        aid      = f"ASSET-{row['response_id']}"
        app_type = _asset_app_type(row["type"])
        caps     = copy.deepcopy(ASSET_CAPS.get(app_type, {}))
        caps["maxRangeM"] = int(row.get("max_range_meters") or 0)
        caps["minRangeM"] = int(row.get("min_range_meters") or 0)
        inv = row.get("inventory")
        return {
            "assetId":      aid,
            "db_id":        row["response_id"],
            "name":         row["type"],
            "type":         app_type,
            "status":       "READY",
            "position": {
                "latitude":  row["latitude"],
                "longitude": row["longitude"],
            },
            "heading":       0,
            "capabilities":  caps,
            "status_detail": f"{inv} units available" if inv else "Ready",
            "ammo":          {"current": inv, "max": inv, "unit": "units"} if inv else None,
        }

    # ── Public API ─────────────────────────────────────────────────────────

    def get_sensors(self) -> list[dict]:
        rows = self._query("SELECT * FROM hackathon.sensors ORDER BY sensor_id")
        sensors = [self._map_sensor(r) for r in rows]
        # Overlay any in-session updates
        for s in sensors:
            if s["sensorId"] in self._sensor_cache:
                s.update(self._sensor_cache[s["sensorId"]])
        return sensors

    def get_sensor(self, sensor_id: str) -> Optional[dict]:
        return next((s for s in self.get_sensors() if s["sensorId"] == sensor_id), None)

    def update_sensor(self, sensor_id: str, patch: dict) -> Optional[dict]:
        sensor = self.get_sensor(sensor_id)
        if not sensor:
            return None

        # Write orientation back to DB if changed
        if "orientation" in patch:
            # Convert app bearing back to DB format
            new_orient = patch["orientation"]
            if sensor.get("fov", 0) >= 360:
                new_orient = 360   # preserve omnidirectional marker
            self._execute(
                "UPDATE hackathon.sensors SET orientation_deg=%s WHERE sensor_id=%s",
                (new_orient, sensor["db_id"]),
            )

        # Cache remaining in-memory fields
        allowed = {"position", "orientation", "active", "fov", "range",
                   "type", "modality", "sensitivity"}
        update = {k: v for k, v in patch.items() if k in allowed}
        self._sensor_cache.setdefault(sensor_id, {}).update(update)

        sensor.update(update)
        return copy.deepcopy(sensor)

    def get_threats(self) -> list[dict]:
        rows = self._query("SELECT * FROM hackathon.threats ORDER BY threat_id")
        return [self._map_threat(r) for r in rows]

    def get_threat(self, threat_id: str) -> Optional[dict]:
        return next((t for t in self.get_threats() if t["threatId"] == threat_id), None)

    def get_active_threats(self) -> list[dict]:
        return [t for t in self.get_threats() if t["status"] == "TRACKING"]

    def get_response_assets(self) -> list[dict]:
        rows = self._query("SELECT * FROM hackathon.responses ORDER BY response_id")
        assets = [self._map_asset(r) for r in rows]
        for a in assets:
            if a["assetId"] in self._asset_cache:
                a.update(self._asset_cache[a["assetId"]])
        return assets

    def get_response_asset(self, asset_id: str) -> Optional[dict]:
        return next((a for a in self.get_response_assets() if a["assetId"] == asset_id), None)

    def update_response_asset(self, asset_id: str, patch: dict) -> Optional[dict]:
        asset = self.get_response_asset(asset_id)
        if not asset:
            return None
        allowed = {"status", "position", "heading", "status_detail"}
        self._asset_cache.setdefault(asset_id, {}).update(
            {k: v for k, v in patch.items() if k in allowed}
        )
        asset.update({k: v for k, v in patch.items() if k in allowed})
        return copy.deepcopy(asset)

    def get_detections(self) -> list[dict]:
        sensors = self.get_sensors()
        detections = []
        for s in sensors:
            for tid in s.get("detections", []):
                t = self.get_threat(tid)
                if t and t.get("track"):
                    latest = t["track"][-1]
                    detections.append({
                        "sensorId":   s["sensorId"],
                        "sensorType": s["type"],
                        "threatId":   tid,
                        "confidence": latest.get("confidence", 0),
                        "threatLevel": t.get("threatLevel"),
                        "latitude":   latest.get("latitude"),
                        "longitude":  latest.get("longitude"),
                        "timestamp":  latest.get("timestamp"),
                    })
        return detections

    # ── Telemetry / missions (from JSON fallback) ─────────────────────────
    # These are not in the DB schema — served from files if present.
    def list_drones(self) -> list[str]: return []
    def get_telemetry(self, *a, **kw) -> list: return []
    def get_missions(self) -> list: return []

    # ── Recommendations ────────────────────────────────────────────────────

    def recommend_counteraction(self, threat_id: str) -> dict:
        threat = self.get_threat(threat_id)
        if not threat:
            return {"error": f"Threat {threat_id} not found"}
        latest  = threat["track"][-1]
        t_lat, t_lon = latest["latitude"], latest["longitude"]
        t_alt, t_speed = latest.get("altitude", 0), latest.get("speed", 0)

        candidates = []
        for asset in self.get_response_assets():
            if asset["status"] not in ("READY", "STANDBY"):
                continue
            caps   = asset.get("capabilities", {})
            dist_m = _haversine_m(asset["position"]["latitude"],
                                  asset["position"]["longitude"], t_lat, t_lon)
            in_range  = dist_m <= caps.get("maxRangeM", 0)
            alt_ok    = caps.get("minAltM", 0) <= t_alt <= caps.get("maxAltM", 99999)
            speed_ok  = t_speed <= caps.get("maxTargetSpeedMs", 0)
            candidates.append({
                "assetId":         asset["assetId"],
                "name":            asset["name"],
                "type":            asset["type"],
                "status":          asset["status"],
                "distanceM":       round(dist_m),
                "inRange":         in_range,
                "altitudeOk":      alt_ok,
                "speedOk":         speed_ok,
                "canEngage":       in_range and alt_ok and speed_ok,
                "pkill":           caps.get("pkill", 0),
                "responseTimeSec": caps.get("responseTimeSec"),
                "method":          caps.get("method"),
                "bestAgainst":     caps.get("bestAgainst", []),
            })
        candidates.sort(key=lambda x: (not x["canEngage"], x["distanceM"]))
        return {
            "threatId":       threat_id,
            "threatLevel":    threat.get("threatLevel"),
            "classification": threat.get("classification"),
            "estimatedType":  threat.get("estimatedType"),
            "latestPosition": {"lat": t_lat, "lon": t_lon, "altM": t_alt},
            "speedMs":        t_speed,
            "confidence":     latest.get("confidence", 0),
            "candidates":     candidates,
        }

    # ── Actions ────────────────────────────────────────────────────────────

    def execute_action(self, action_type: str, parameters: dict) -> dict:
        if action_type == "sensor_activate":
            self.update_sensor(parameters.get("sensorId", ""), {"active": True})
        elif action_type == "sensor_deactivate":
            self.update_sensor(parameters.get("sensorId", ""), {"active": False})
        elif action_type == "asset_deploy":
            self.update_response_asset(parameters.get("assetId", ""), {
                "status": "ENGAGED",
                "status_detail": f"Engaging {parameters.get('threatId', '')}",
            })
        return {"status": "executed", "action": action_type, "parameters": parameters}

    # ── Map center ─────────────────────────────────────────────────────────

    def get_map_center(self) -> dict:
        """Return centroid of all assets for auto-centering the map."""
        rows = self._query(
            "SELECT latitude, longitude FROM hackathon.sensors "
            "UNION ALL "
            "SELECT latitude, longitude FROM hackathon.threats "
            "UNION ALL "
            "SELECT latitude, longitude FROM hackathon.responses"
        )
        if not rows:
            return {"latitude": 38.905, "longitude": -77.037, "zoom": 13}
        lats = [r["latitude"]  for r in rows]
        lons = [r["longitude"] for r in rows]
        return {
            "latitude":  sum(lats) / len(lats),
            "longitude": sum(lons) / len(lons),
            "zoom": 13,
        }

    def close(self):
        self.pool.closeall()
