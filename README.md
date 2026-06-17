# PumpFuse Dashboard

Polls two ESP32-based PumpFuse sump pump devices over a local network and publishes live status to a GitHub Pages dashboard. Only commits and pushes when pump state changes.

**Live dashboard:** https://joshmiller83.github.io/pumpfuse/

---

## Architecture

```
[Sump-Pump ESP32]  ←─── HTTP /status ───┐
[Ejector-Pit ESP32] ←── HTTP /status ───┤
                                         │ SSH (via UDM gateway)
                                   [Mac cron job]
                                         │ git push (on state change)
                                   [GitHub Pages]
```

- Both devices live on the **IoT VLAN** (`10.6.5.x`, VLAN 5), isolated from the main LAN
- The Mac cannot reach the IoT VLAN directly — all curl calls are tunneled through the **UniFi Dream Machine Pro Max** (`10.6.2.1`) via SSH
- `poll.py` runs every 30 seconds via cron, detects state changes, and pushes `docs/data.json` to GitHub
- GitHub Pages serves `docs/index.html`, which fetches `data.json` and auto-refreshes every 30 seconds

---

## Device Info

| Device | Hostname | IP | MAC | VLAN |
|---|---|---|---|---|
| Sump Pump | `Sump-Pump.sugarfield.local` | `10.6.5.85` | `8c:ce:4e:10:08:93` | IoT (5) |
| Ejector Pit | `Ejector-Pit.sugarfield.local` | `10.6.5.126` | `8c:ce:4e:10:09:4e` | IoT (5) |

Both run **PumpFuse firmware 4.0.15** on Espressif ESP32 chips. They expose a single HTTP endpoint:

```
GET http://<device-ip>/status
→ {"millies":..., "ap_name":"...", "power":0, "amps":0, "voltage":122.4, "state":10, "version":"4.0.15"}
```

`state: 10` = idle. Any other value = pump running.

---

## Setup on a New Machine

### Prerequisites

- macOS (Apple Silicon or Intel)
- [Homebrew](https://brew.sh)
- Python 3.10+
- `gh` CLI authenticated to the `joshmiller83` GitHub account
- SSH access to the UniFi Dream Machine at `10.6.2.1`

### 1. Clone the repo

```bash
git clone git@github.com:joshmiller83/pumpfuse.git
cd pumpfuse
```

### 2. Configure SSH to reach the UDM

Add this to `~/.ssh/config`:

```
Host unifi udm 10.6.2.1
  HostName 10.6.2.1
  User root
  IdentityFile ~/.ssh/id_rsa_mb_pantheon   # or whichever key you copy
  StrictHostKeyChecking no
```

Copy your SSH public key to the UDM (enter the UDM password once):

```bash
ssh-copy-id -i ~/.ssh/id_rsa_mb_pantheon root@10.6.2.1
```

Enable SSH on the UDM first: **UniFi Console → OS Settings → Advanced → SSH → Enable**.

Test:

```bash
ssh unifi "curl -s http://10.6.5.85/status"
```

### 3. Enable SSH on the UDM (if not already)

In the UniFi web console (`https://10.6.2.1`):
- Go to **OS Settings** (gear icon) → **Advanced**
- Toggle **SSH** on and set a password

### 4. Configure git identity

```bash
git config user.name "Josh Miller"
git config user.email "jomiller.urban@gmail.com"
```

Make sure `gh` is authenticated:

```bash
gh auth login
```

### 5. Make cron.sh executable

```bash
chmod +x cron.sh
```

Update the `HOME` path in `cron.sh` if the username differs from `jomiller`.

### 6. Test the poller

```bash
python3 poll.py
```

You should see `OK sump-pump: state=10 (no change)` etc.

### 7. Install the cron jobs

```bash
crontab -e
```

Add these two lines (30-second polling — cron minimum is 1 minute, so two offset entries):

```
* * * * * /Users/jomiller/Developer/github/joshmiller83/pumpfuse/cron.sh
* * * * * sleep 30 && /Users/jomiller/Developer/github/joshmiller83/pumpfuse/cron.sh
```

### 8. Enable GitHub Pages

In the GitHub repo settings:
- Go to **Settings → Pages**
- Source: **Deploy from a branch**
- Branch: `main`, folder: `/docs`

The dashboard will be live at `https://joshmiller83.github.io/pumpfuse/`

---

## Files

```
poll.py          # Poller — fetches device status, detects changes, pushes to GitHub
cron.sh          # Cron wrapper — sets PATH/SSH env for headless execution
docs/
  index.html     # Dashboard (GitHub Pages)
  data.json      # Current device state + recent events (auto-generated, committed on change)
data/
  events.jsonl   # Append-only log of all state change events
  state/         # Last known state per device (not committed)
  poll.log       # Cron output log (not committed)
```

---

## Data Format

**`docs/data.json`** (pushed to GitHub on state change):
```json
{
  "updated": "2026-06-17T20:00:00Z",
  "devices": {
    "sump-pump": {
      "ap_name": "Sump-Pump",
      "power": 0, "amps": 0, "voltage": 122.4,
      "state": 10, "running": false,
      "last_seen": "...", "last_change": "..."
    }
  },
  "recent_events": [
    {"device": "sump-pump", "timestamp": "...", "prev_state": 10, "state": 1,
     "power": 450, "amps": 3.8, "voltage": 121.1, "running": true}
  ]
}
```

**`data/events.jsonl`** — one JSON object per line, appended on every state change.
