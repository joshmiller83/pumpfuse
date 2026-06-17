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
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).parent

# Load .env if present
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

# Gallons per minute estimates based on measured power draw.
# Sump pump: ~870W real power ≈ 1.2 HP → ~2,200 GPH → 36.7 GPM
# Override via GPM_SUMP / GPM_EJECTOR in .env once you have better data.
GALLONS_PER_MINUTE = {
    "sump-pump":   float(os.environ.get("GPM_SUMP",    "36.7")),
    "ejector-pit": float(os.environ.get("GPM_EJECTOR", "36.7")),
}

DEVICE_DESC = {
    "sump-pump":   "Perimeter foundation drain",
    "ejector-pit": "Downstairs bathroom, floor drain, HVAC drainage",
}

IDLE_STATE    = 10
RUNNING_STATE = 11

DATA_DIR  = ROOT / "data"
STATE_DIR = DATA_DIR / "state"
EVENTS    = DATA_DIR / "events.jsonl"
DATA_JSON = ROOT / "docs" / "data.json"
MAX_RECENT_RUNS = 100


# ── I/O helpers ──────────────────────────────────────────────────────────────

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


def load_events() -> list[dict]:
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


# ── Stats ─────────────────────────────────────────────────────────────────────

def compute_stats(events: list[dict]) -> dict:
    """Aggregate completed runs (STOPPED events with duration) by time window."""
    now = datetime.now(timezone.utc)
    windows = {
        "last_hour": now - timedelta(hours=1),
        "last_24h":  now - timedelta(hours=24),
        "last_30d":  now - timedelta(days=30),
        "last_90d":  now - timedelta(days=90),
    }
    completed = [e for e in events if not e.get("running") and "duration_seconds" in e]

    stats = {name: {w: {"runs": 0, "gallons": 0.0} for w in windows}
             for name in list(DEVICES) + ["combined"]}

    for e in completed:
        try:
            ts = datetime.fromisoformat(e["timestamp"])
        except (KeyError, ValueError):
            continue
        device = e.get("device")
        gallons = e.get("gallons_estimated", 0.0)
        for w, cutoff in windows.items():
            if ts >= cutoff:
                if device in stats:
                    stats[device][w]["runs"]    += 1
                    stats[device][w]["gallons"] += gallons
                stats["combined"][w]["runs"]    += 1
                stats["combined"][w]["gallons"] += gallons

    # Round gallons
    for device in stats.values():
        for w in device.values():
            w["gallons"] = round(w["gallons"], 1)

    return stats


def compute_daily_gallons(events: list[dict], days: int = 90) -> list[dict]:
    """Return per-day gallons for each device, last N days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    completed = [e for e in events
                 if not e.get("running") and "duration_seconds" in e]

    daily: dict[str, dict[str, float]] = {}
    for e in completed:
        try:
            ts = datetime.fromisoformat(e["timestamp"])
        except (KeyError, ValueError):
            continue
        if ts < cutoff:
            continue
        date = ts.date().isoformat()
        device = e.get("device", "unknown")
        daily.setdefault(date, {d: 0.0 for d in DEVICES})
        if device in daily[date]:
            daily[date][device] += e.get("gallons_estimated", 0.0)

    return [{"date": d, **{k: round(v, 1) for k, v in vals.items()}}
            for d, vals in sorted(daily.items())]


def recent_runs(events: list[dict]) -> list[dict]:
    completed = [e for e in events
                 if not e.get("running") and "duration_seconds" in e]
    return completed[-MAX_RECENT_RUNS:][::-1]


# ── data.json ────────────────────────────────────────────────────────────────

def write_data_json(device_states: dict):
    DATA_JSON.parent.mkdir(exist_ok=True)
    events = load_events()
    payload = {
        "updated":      datetime.now(timezone.utc).isoformat(),
        "devices":      device_states,
        "device_desc":  DEVICE_DESC,
        "stats":        compute_stats(events),
        "daily_gallons": compute_daily_gallons(events),
        "recent_runs":  recent_runs(events),
    }
    DATA_JSON.write_text(json.dumps(payload, indent=2))


# ── Git ───────────────────────────────────────────────────────────────────────

def git_commit_push(message: str):
    repo = str(ROOT)
    subprocess.run(["git", "-C", repo, "add", "docs/data.json", "data/events.jsonl"],
                   check=True, capture_output=True)
    result = subprocess.run(
        ["git", "-C", repo, "commit", "-m", message],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return
    subprocess.run(["git", "-C", repo, "push"], check=True, capture_output=True)


# ── Main ──────────────────────────────────────────────────────────────────────

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

        state_entry = {
            **current,
            "voltage":    round(current["voltage"], 2),
            "running":    running,
            "last_seen":  timestamp,
            "last_change": timestamp if prev_state != curr_state
                           else (last or {}).get("last_change"),
            "run_started_at": timestamp if running
                              else (last or {}).get("run_started_at"),
        }

        if prev_state != curr_state:
            changed = True
            event: dict = {
                "device":     name,
                "timestamp":  timestamp,
                "prev_state": prev_state,
                "state":      curr_state,
                "power":      current["power"],
                "amps":       round(current["amps"], 3),
                "voltage":    round(current["voltage"], 2),
                "running":    running,
            }

            # Compute duration + gallons when pump stops
            if not running and last and last.get("run_started_at"):
                try:
                    start = datetime.fromisoformat(last["run_started_at"])
                    duration = (datetime.fromisoformat(timestamp) - start).total_seconds()
                    gallons = round(duration / 60 * GALLONS_PER_MINUTE[name], 2)
                    event["duration_seconds"]  = round(duration, 1)
                    event["gallons_estimated"] = gallons
                except Exception:
                    pass

            with EVENTS.open("a") as f:
                f.write(json.dumps(event) + "\n")

            label = "STARTED" if running else "STOPPED"
            extra = ""
            if "duration_seconds" in event:
                extra = f"  {event['duration_seconds']}s  {event['gallons_estimated']}gal"
            print(f"{label} {name}: state {prev_state}→{curr_state} "
                  f"power={current['power']}W{extra}")
        else:
            print(f"OK     {name}: state={curr_state} (no change)")

        if not running:
            state_entry["run_started_at"] = None
        save_state(name, state_entry)
        device_states[name] = state_entry

    if changed:
        write_data_json(device_states)
        msg = "data: " + "; ".join(
            f"{n} {'running' if s['running'] else 'idle'}"
            for n, s in device_states.items()
        )
        try:
            git_commit_push(msg)
            print(f"PUSHED: {msg}")
        except Exception as e:
            print(f"ERR git push: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
