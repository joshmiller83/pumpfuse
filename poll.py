#!/usr/bin/env python3
"""
Poll PumpFuse devices via SSH through the UDM.
On state change: writes docs/data.json, appends to data/events.jsonl, commits and pushes.
Run every 30 seconds via cron (see cron.sh).
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent

# Load .env if present (never committed — see .env.example)
_env = ROOT / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        line = line.split("#")[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

UDM_HOST = os.environ["UDM_SSH_HOST"]
DEVICES = {
    "sump-pump":   os.environ["DEVICE_SUMP"],
    "ejector-pit": os.environ["DEVICE_EJECTOR"],
}
DATA_DIR  = ROOT / "data"
STATE_DIR = DATA_DIR / "state"
EVENTS    = DATA_DIR / "events.jsonl"
DATA_JSON = ROOT / "docs" / "data.json"
IDLE_STATE = 10
MAX_RECENT = 50


def fetch(ip: str) -> dict:
    cmd = ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", UDM_HOST,
           f"curl -s http://{ip}/status"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(result.stderr.strip() or "empty response")
    return json.loads(result.stdout.strip())


def load_state(name: str) -> dict | None:
    path = STATE_DIR / f"{name}.json"
    return json.loads(path.read_text()) if path.exists() else None


def save_state(name: str, data: dict):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / f"{name}.json").write_text(json.dumps(data))


def load_events() -> list:
    if not EVENTS.exists():
        return []
    events = []
    for line in EVENTS.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return events


def write_data_json(device_states: dict):
    events = load_events()
    DATA_JSON.parent.mkdir(exist_ok=True)
    payload = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "devices": device_states,
        "recent_events": events[-MAX_RECENT:][::-1],
    }
    DATA_JSON.write_text(json.dumps(payload, indent=2))


def git_commit_push(message: str):
    repo = str(ROOT)
    subprocess.run(["git", "-C", repo, "add", "docs/data.json", "data/events.jsonl"],
                   check=True, capture_output=True)
    result = subprocess.run(
        ["git", "-C", repo, "commit", "-m", message],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return  # nothing to commit
    subprocess.run(["git", "-C", repo, "push"], check=True, capture_output=True)


def main():
    DATA_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    changed = False
    device_states = {}

    for name, ip in DEVICES.items():
        try:
            current = fetch(ip)
        except Exception as e:
            print(f"ERR {name}: {e}", file=sys.stderr)
            last = load_state(name)
            if last:
                device_states[name] = last
            continue

        last = load_state(name)
        prev_state = last["state"] if last else None
        curr_state = current["state"]
        running = curr_state != IDLE_STATE

        device_states[name] = {
            **current,
            "voltage":    round(current["voltage"], 2),
            "running":    running,
            "last_seen":  timestamp,
            "last_change": timestamp if prev_state != curr_state else (last or {}).get("last_change"),
        }

        if prev_state != curr_state:
            changed = True
            event = {
                "device":     name,
                "timestamp":  timestamp,
                "prev_state": prev_state,
                "state":      curr_state,
                "power":      current["power"],
                "amps":       current["amps"],
                "voltage":    round(current["voltage"], 2),
                "running":    running,
            }
            with EVENTS.open("a") as f:
                f.write(json.dumps(event) + "\n")
            label = "STARTED" if running else "STOPPED"
            print(f"{label} {name}: state {prev_state}→{curr_state} power={current['power']}W")
        else:
            print(f"OK     {name}: state={curr_state} (no change)")

        save_state(name, device_states[name])

    if changed:
        write_data_json(device_states)
        devices_changed = [n for n, s in device_states.items() if s.get("running") is not None]
        msg = "data: state change – " + ", ".join(
            f"{n} {'running' if device_states[n]['running'] else 'idle'}"
            for n in device_states if "running" in device_states[n]
        )
        try:
            git_commit_push(msg)
            print(f"PUSHED: {msg}")
        except Exception as e:
            print(f"ERR git push: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
