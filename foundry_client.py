"""
Palantir Foundry REST API client.
Handles auth, dataset queries, ontology object queries, and action execution.
"""
import os
import json
import httpx
from typing import Any, Optional
from dotenv import load_dotenv

load_dotenv()

FOUNDRY_HOST = os.environ["FOUNDRY_HOST"]
FOUNDRY_TOKEN = os.environ["FOUNDRY_TOKEN"]

# Populated by discover.py or set in .env
ONTOLOGY_RID = os.environ.get("FOUNDRY_ONTOLOGY_RID", "ri.ontology.main.ontology.00000000-0000-0000-0000-000000000000")
DATASET_TELEMETRY_RID = os.environ.get("FOUNDRY_DATASET_TELEMETRY_RID", "")
DATASET_MISSIONS_RID = os.environ.get("FOUNDRY_DATASET_MISSIONS_RID", "")


class FoundryClient:
    def __init__(self):
        self.host = FOUNDRY_HOST.rstrip("/")
        self.http = httpx.Client(
            headers={"Authorization": f"Bearer {FOUNDRY_TOKEN}"},
            timeout=30.0,
        )

    # ── Low-level helpers ──────────────────────────────────────────────────

    def get(self, path: str, **kwargs) -> httpx.Response:
        return self.http.get(f"{self.host}{path}", **kwargs)

    def post(self, path: str, **kwargs) -> httpx.Response:
        return self.http.post(f"{self.host}{path}", **kwargs)

    def _raise(self, r: httpx.Response) -> httpx.Response:
        if not r.is_success:
            raise RuntimeError(f"Foundry {r.status_code} on {r.url}: {r.text[:300]}")
        return r

    # ── Discovery ──────────────────────────────────────────────────────────

    def discover_ontology(self) -> dict:
        """Return the first available ontology (usually 'default')."""
        r = self._raise(self.get("/api/v2/ontologies"))
        ontologies = r.json().get("data", [])
        if not ontologies:
            raise RuntimeError("No ontologies found")
        return ontologies[0]

    def list_object_types(self, ontology_rid: str = None) -> list[dict]:
        rid = ontology_rid or ONTOLOGY_RID
        r = self._raise(self.get(f"/api/v2/ontologies/{rid}/objectTypes"))
        return r.json().get("data", [])

    def list_action_types(self, ontology_rid: str = None) -> list[dict]:
        rid = ontology_rid or ONTOLOGY_RID
        r = self._raise(self.get(f"/api/v2/ontologies/{rid}/actionTypes"))
        return r.json().get("data", [])

    def search_compass(self, query: str, resource_types: str = "DATASET") -> list[dict]:
        """Find datasets/resources by name."""
        r = self._raise(self.get(
            "/api/v1/compass/resources",
            params={"query": query, "resourceTypes": resource_types},
        ))
        return r.json().get("data", [])

    # ── Ontology Object queries ────────────────────────────────────────────

    def query_objects(
        self,
        object_type: str,
        where: Optional[str] = None,
        order_by: Optional[str] = None,
        page_size: int = 200,
        ontology_rid: str = None,
    ) -> list[dict]:
        """
        Query ontology objects by type.
        object_type: API name e.g. "DroneTelemetryPoint", "DroneMission"
        where: OSQL filter e.g. "droneId = '001'"
        """
        rid = ontology_rid or ONTOLOGY_RID
        params: dict[str, Any] = {"pageSize": page_size}
        if where:
            params["where"] = where
        if order_by:
            params["orderBy"] = order_by

        r = self._raise(self.get(
            f"/api/v2/ontologies/{rid}/objects/{object_type}",
            params=params,
        ))
        return r.json().get("data", [])

    def query_objects_post(
        self,
        object_type: str,
        filter_body: dict,
        page_size: int = 500,
        ontology_rid: str = None,
    ) -> list[dict]:
        """POST-based object search for complex filters."""
        rid = ontology_rid or ONTOLOGY_RID
        body = {"pageSize": page_size, **filter_body}
        r = self._raise(self.post(
            f"/api/v2/ontologies/{rid}/objects/{object_type}/search",
            json=body,
        ))
        return r.json().get("data", [])

    # ── Drone-specific helpers ─────────────────────────────────────────────

    def get_telemetry(
        self,
        drone_id: str,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        limit: int = 3600,
    ) -> list[dict]:
        """
        Pull telemetry rows for a drone from the drone_telemetry_points object type.
        Returns list of dicts sorted by timestamp ascending.
        Falls back to dataset API if ontology query fails.
        """
        # Try ontology first (most structured)
        try:
            filter_parts = [f"droneId = '{drone_id}'"]
            if start_time:
                filter_parts.append(f"timestamp >= '{start_time}'")
            if end_time:
                filter_parts.append(f"timestamp <= '{end_time}'")
            where = " AND ".join(filter_parts)

            rows = self.query_objects(
                "DroneTelemetryPoint",
                where=where,
                order_by="timestamp ASC",
                page_size=min(limit, 500),
            )
            if rows:
                return rows
        except Exception:
            pass

        # Fallback: try lowercase API name
        try:
            rows = self.query_objects(
                "drone_telemetry_points",
                where=f"droneId = '{drone_id}'",
                page_size=min(limit, 500),
            )
            if rows:
                return rows
        except Exception:
            pass

        # Fallback: raw dataset read
        if DATASET_TELEMETRY_RID:
            return self._read_dataset_rows(DATASET_TELEMETRY_RID, limit=limit)
        return []

    def get_missions(self) -> list[dict]:
        """Pull all drone missions."""
        try:
            return self.query_objects("DroneMission", page_size=100)
        except Exception:
            pass
        try:
            return self.query_objects("drone_missions", page_size=100)
        except Exception:
            pass
        if DATASET_MISSIONS_RID:
            return self._read_dataset_rows(DATASET_MISSIONS_RID, limit=100)
        return []

    def list_drones(self) -> list[str]:
        """Return distinct drone IDs found in telemetry."""
        try:
            objects = self.query_objects("DroneTelemetryPoint", page_size=500)
            ids = {o.get("properties", {}).get("droneId") or o.get("droneId") for o in objects}
            return sorted(filter(None, ids))
        except Exception:
            return []

    # ── Actions ────────────────────────────────────────────────────────────

    def execute_action(
        self,
        action_type: str,
        parameters: dict,
        ontology_rid: str = None,
    ) -> dict:
        """Execute a Foundry ontology action (e.g. reroute, abort)."""
        rid = ontology_rid or ONTOLOGY_RID
        r = self._raise(self.post(
            f"/api/v2/ontologies/{rid}/actions/{action_type}/apply",
            json={"parameters": parameters},
        ))
        return r.json()

    # ── Raw dataset fallback ───────────────────────────────────────────────

    def _read_dataset_rows(self, dataset_rid: str, branch: str = "master", limit: int = 1000) -> list[dict]:
        """Read rows from a raw Foundry dataset via the row-read API."""
        r = self.post(
            f"/api/v2/datasets/{dataset_rid}/rows:read",
            json={"branchName": branch, "pageSize": limit},
        )
        if r.is_success:
            return r.json().get("rows", [])
        return []

    def close(self):
        self.http.close()
