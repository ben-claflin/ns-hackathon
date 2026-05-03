"""
Generate realistic sample drone telemetry and mission data.
Useful for testing when Foundry is not accessible.

Usage:
    python generate_sample_data.py --drones 5 --duration 300 --output data/
"""
import json
import argparse
from datetime import datetime, timedelta
from pathlib import Path
import random
import math


def generate_telemetry(
    num_drones: int = 3,
    duration_seconds: int = 300,
    output_dir: str = "data",
) -> list[dict]:
    """Generate realistic drone telemetry data."""
    telemetry = []
    start_time = datetime(2026, 5, 3, 14, 0, 0)

    # Drone flight paths (lat, lon center points)
    drone_paths = [
        {
            "id": f"ALPHA-{i+1:03d}",
            "center_lat": 38.9072 + random.uniform(-0.01, 0.01),
            "center_lon": -77.0369 + random.uniform(-0.01, 0.01),
            "speed": random.uniform(8, 15),
            "altitude": random.uniform(80, 220),
            "heading": random.uniform(0, 360),
        }
        for i in range(num_drones)
    ]

    for drone in drone_paths:
        current_time = start_time
        current_heading = drone["heading"]
        battery = 100

        for sec in range(duration_seconds):
            # Slight variation in heading and speed (realistic flight)
            heading_delta = random.uniform(-2, 2)
            current_heading = (current_heading + heading_delta) % 360
            speed_delta = random.uniform(-0.5, 0.5)
            speed = max(0, drone["speed"] + speed_delta)

            # Calculate lat/lon movement
            lat_delta = (speed / 111000) * math.cos(math.radians(current_heading))
            lon_delta = (speed / 111000) * math.sin(math.radians(current_heading))

            lat = drone["center_lat"] + lat_delta
            lon = drone["center_lon"] + lon_delta
            alt = drone["altitude"] + random.uniform(-1, 1)
            battery = max(20, battery - random.uniform(0, 0.1))

            status = "AIRBORNE"
            if battery < 30:
                status = "WARNING"
            elif sec > duration_seconds * 0.8:
                status = "LANDING"

            telemetry.append({
                "droneId": drone["id"],
                "timestamp": (current_time + timedelta(seconds=sec)).isoformat() + "Z",
                "latitude": round(lat, 6),
                "longitude": round(lon, 6),
                "altitude": round(alt, 1),
                "speed": round(speed, 1),
                "heading": round(current_heading, 1),
                "battery": round(battery, 1),
                "status": status,
            })

    return telemetry


def generate_missions(num_drones: int = 3) -> list[dict]:
    """Generate drone missions with waypoints."""
    missions = []

    for i in range(num_drones):
        drone_id = f"ALPHA-{i+1:03d}"
        mission_id = f"MISSION-{i+1:03d}"

        # Generate 3-5 waypoints per mission
        num_waypoints = random.randint(3, 5)
        waypoints = []

        for wp in range(num_waypoints):
            waypoints.append({
                "sequence": wp + 1,
                "latitude": 38.9072 + random.uniform(-0.02, 0.02),
                "longitude": -77.0369 + random.uniform(-0.02, 0.02),
                "altitude": random.uniform(100, 200),
                "action": random.choice(["LOITER", "SURVEY", "PHOTO", "VIDEO", "RTB"]),
            })

        missions.append({
            "missionId": mission_id,
            "droneId": drone_id,
            "missionName": f"{random.choice(['Patrol', 'Survey', 'Escort', 'Reconnaissance', 'Delivery'])} {i+1}",
            "status": random.choice(["ACTIVE", "PAUSED", "COMPLETED"]),
            "startTime": (datetime(2026, 5, 3, 13, 45, 0) + timedelta(minutes=i*5)).isoformat() + "Z",
            "plannedEndTime": (datetime(2026, 5, 3, 15, 0, 0) + timedelta(minutes=i*5)).isoformat() + "Z",
            "waypoints": waypoints,
            "currentWaypoint": random.randint(1, len(waypoints)),
            "progress": random.randint(5, 95),
        })

    return missions


def main():
    parser = argparse.ArgumentParser(description="Generate sample drone data")
    parser.add_argument("--drones", type=int, default=3, help="Number of drones")
    parser.add_argument("--duration", type=int, default=300, help="Telemetry duration in seconds")
    parser.add_argument("--output", type=str, default="data", help="Output directory")
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(exist_ok=True)

    print(f"Generating {args.drones} drones with {args.duration}s of telemetry...")
    telemetry = generate_telemetry(args.drones, args.duration, str(output_dir))
    missions = generate_missions(args.drones)

    # Write files
    tel_file = output_dir / "drone_telemetry.json"
    mis_file = output_dir / "drone_missions.json"

    with open(tel_file, "w") as f:
        json.dump(telemetry, f, indent=2)
    print(f"✓ Wrote {len(telemetry)} telemetry points to {tel_file}")

    with open(mis_file, "w") as f:
        json.dump(missions, f, indent=2)
    print(f"✓ Wrote {len(missions)} missions to {mis_file}")


if __name__ == "__main__":
    main()
