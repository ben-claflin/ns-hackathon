"""
Run this ONCE from the hackathon venue network to discover Foundry resource IDs.
Writes discovered values to discovered_config.json and prints .env lines to add.

Usage:
    python discover.py
"""
import json
import sys
from foundry_client import FoundryClient


def main():
    client = FoundryClient()
    config = {}

    print("=== Discovering Foundry Resources ===\n")

    # 1. Find ontology
    print("1. Discovering ontology...")
    try:
        ontology = client.discover_ontology()
        config["ontology_rid"] = ontology["rid"]
        config["ontology_api_name"] = ontology.get("apiName", "")
        print(f"   ✓ Ontology: {ontology['rid']}")
        print(f"     API Name: {ontology.get('apiName', 'N/A')}")
    except Exception as e:
        print(f"   ✗ Failed: {e}")
        config["ontology_rid"] = None

    # 2. List object types in ontology
    if config.get("ontology_rid"):
        print("\n2. Listing object types...")
        try:
            types = client.list_object_types(config["ontology_rid"])
            config["object_types"] = [t.get("apiName") for t in types]
            for t in types:
                print(f"   • {t.get('apiName', 'unknown')} — {t.get('displayName', '')}")
        except Exception as e:
            print(f"   ✗ Failed: {e}")

    # 3. List action types
    if config.get("ontology_rid"):
        print("\n3. Listing action types...")
        try:
            actions = client.list_action_types(config["ontology_rid"])
            config["action_types"] = [a.get("apiName") for a in actions]
            for a in actions:
                print(f"   • {a.get('apiName', 'unknown')} — {a.get('displayName', '')}")
        except Exception as e:
            print(f"   ✗ Failed: {e}")

    # 4. Search Compass for drone datasets
    print("\n4. Searching Compass for drone datasets...")
    for dataset_name in ["drone_telemetry_points", "drone_missions"]:
        try:
            results = client.search_compass(dataset_name)
            if results:
                rid = results[0].get("rid") or results[0].get("path", "")
                config[f"dataset_{dataset_name}_rid"] = rid
                print(f"   ✓ {dataset_name}: {rid}")
            else:
                print(f"   ✗ {dataset_name}: not found")
        except Exception as e:
            print(f"   ✗ {dataset_name}: {e}")

    # 5. Sample telemetry to inspect schema
    print("\n5. Sampling drone_telemetry_points schema...")
    try:
        rows = client.query_objects("DroneTelemetryPoint", page_size=1)
        if rows:
            sample = rows[0]
            props = sample.get("properties", sample)
            config["telemetry_schema"] = list(props.keys())
            print(f"   ✓ Fields: {list(props.keys())}")
    except Exception as e:
        print(f"   ✗ {e}")

    # 6. Sample missions
    print("\n6. Sampling drone_missions schema...")
    try:
        rows = client.query_objects("DroneMission", page_size=1)
        if rows:
            sample = rows[0]
            props = sample.get("properties", sample)
            config["mission_schema"] = list(props.keys())
            print(f"   ✓ Fields: {list(props.keys())}")
    except Exception as e:
        print(f"   ✗ {e}")

    # Write config
    with open("discovered_config.json", "w") as f:
        json.dump(config, f, indent=2)
    print("\n\n=== Saved to discovered_config.json ===")
    print("\nAdd these to your .env file:\n")
    if config.get("ontology_rid"):
        print(f"FOUNDRY_ONTOLOGY_RID={config['ontology_rid']}")
    if config.get("dataset_drone_telemetry_points_rid"):
        print(f"FOUNDRY_DATASET_TELEMETRY_RID={config['dataset_drone_telemetry_points_rid']}")
    if config.get("dataset_drone_missions_rid"):
        print(f"FOUNDRY_DATASET_MISSIONS_RID={config['dataset_drone_missions_rid']}")


if __name__ == "__main__":
    main()
