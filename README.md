# PumpFuse Dashboard

Polls two ESP32-based PumpFuse sump pump devices over a local network and publishes live status to a GitHub Pages dashboard. Only commits and pushes when pump state changes.

**Live dashboard:** https://joshmiller83.github.io/pumpfuse/

---

## Architecture

```
[Sump Pump ESP32]    ←── HTTP /status ──┐
[Ejector Pit ESP32]  ←── HTTP /status ──┤
                                         │ SSH (tunneled via network gateway)
                                   [Mac cron job]
                                         │ git push (on state change only)
                                   [GitHub Pages]
```

- Both devices live on an **IoT VLAN**, isolated from the main LAN
- The Mac cannot reach the IoT VLAN directly — all HTTP calls are tunneled through the **network gateway** via SSH
- `poll.py` runs every 30 seconds, detects state changes, and pushes `docs/data.json` to GitHub
- GitHub Pages serves the dashboard, which auto-refreshes every 30 seconds

---

## Setup on a New Machine

### Prerequisites

- macOS (Apple Silicon or Intel)
- Python 3.10+
- `gh` CLI authenticated to the GitHub account
- SSH access to the network gateway (UniFi Dream Machine or similar)

### 1. Clone the repo

```bash
git clone git@github.com:joshmiller83/pumpfuse.git
cd pumpfuse
```

### 2. Discover your device addresses

You need three addresses: the **gateway** and the **two pump devices**.

**Gateway:** Your default route — run:
```bash
route -n get default | grep gateway
```

**Pump devices:** The ESP32 modules will appear in your router/controller's client list.
Look for devices with:
- Manufacturer: **Espressif Inc.**
- Hostnames matching your pump names (e.g. `Sump-Pump`, `Ejector-Pit`)
- Connected to your IoT VLAN/network

In UniFi: open the **Console → Clients** list, filter by the IoT network, and look for Espressif devices. Note the IP address of each.

Verify a device is reachable from the gateway:
```bash
ssh root@<gateway-ip> "curl -s http://<device-ip>/status"
```

A healthy response looks like:
```json
{"millies":..., "ap_name":"...", "power":0, "amps":0, "voltage":..., "state":10, "version":"..."}
```

`state: 10` = idle. Any other value means the pump is running.

### 3. Create your `.env` file

```bash
cp .env.example .env
```

Edit `.env` and fill in the values you discovered above:

```
UDM_SSH_HOST=          # SSH alias from ~/.ssh/config, or gateway IP
DEVICE_SUMP=           # IP of the sump pump ESP32
DEVICE_EJECTOR=        # IP of the ejector pit ESP32
```

**Never commit `.env`** — it's in `.gitignore`.

### 4. Configure SSH to reach the gateway

Add an entry to `~/.ssh/config` for your gateway (use the alias you put in `UDM_SSH_HOST`):

```
Host <your-alias>
  HostName <gateway-ip>
  User root
  IdentityFile ~/.ssh/<your-key>
  StrictHostKeyChecking no
```

Copy your public key to the gateway (enter the password once):

```bash
ssh-copy-id -i ~/.ssh/<your-key> root@<gateway-ip>
```

To enable SSH on a UniFi Dream Machine: **Console → OS Settings → Advanced → SSH → Enable**.

Test passwordless access:
```bash
ssh <your-alias> "echo connected"
```

### 5. Configure git identity

```bash
git config user.name "Your Name"
git config user.email "you@example.com"
```

Ensure `gh` is authenticated:
```bash
gh auth login
```

### 6. Test the poller

```bash
python3 poll.py
```

Expected output (both idle):
```
OK     sump-pump: state=10 (no change)
OK     ejector-pit: state=10 (no change)
```

### 7. Install the cron jobs

```bash
chmod +x cron.sh
crontab -e
```

Add these two lines (30-second polling via two offset entries):

```
* * * * * /path/to/pumpfuse/cron.sh
* * * * * sleep 30 && /path/to/pumpfuse/cron.sh
```

Update `cron.sh` with the correct `HOME` path for the new machine.

Check it's running after a minute:
```bash
tail -f data/poll.log
```

### 8. Enable GitHub Pages

In the GitHub repo: **Settings → Pages → Deploy from branch → `main` / `/docs`**.

---

## Files

```
poll.py          # Poller — fetches device status, detects changes, pushes to GitHub
cron.sh          # Cron wrapper — sets PATH/SSH env for headless execution
.env.example     # Template for local config (copy to .env, never commit .env)
docs/
  index.html     # Dashboard (GitHub Pages)
  data.json      # Current device state + recent events (auto-generated)
data/
  events.jsonl   # Append-only log of all state change events
  state/         # Last known state per device (not committed)
  poll.log       # Cron output log (not committed)
```

---

## Data Format

**`docs/data.json`** is updated and pushed on every state change:
```json
{
  "updated": "<ISO timestamp>",
  "devices": {
    "sump-pump": {
      "ap_name": "Sump-Pump",
      "power": 0, "amps": 0, "voltage": 122.4,
      "state": 10, "running": false,
      "last_seen": "...", "last_change": "..."
    }
  },
  "recent_events": [
    {
      "device": "sump-pump", "timestamp": "...",
      "prev_state": 10, "state": 1,
      "power": 450, "amps": 3.8, "voltage": 121.1, "running": true
    }
  ]
}
```

**`data/events.jsonl`** — one JSON object per line, appended on every state change.
