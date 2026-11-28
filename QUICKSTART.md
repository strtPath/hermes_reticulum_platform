# QUICKSTART — Hermes for Reticulum

> **Which path is this?** This guide covers the supported path: the Hermes gateway plugin (`src/hermes_reticulum/plugin/`), which runs Reticulum/LXMF as a native Hermes platform inside the gateway process. The standalone bridge (`hermes-reticulum run`) is archived but still deployed; its quickstart lives in [src/hermes_reticulum/bridge/README.md](src/hermes_reticulum/bridge/README.md).

**Goal:** run an AI agent on your Reticulum mesh in under 10 minutes.

**Outcome:** any LXMF client — an off-grid [RNode](https://github.com/markqvist/Reticulum) on LoRa, [Sideband](https://github.com/markqvist/Sideband) on your phone, NomadNet, or a custom Python app — can send a message to your gateway and receive a Hermes Agent reply. Mesh clients do not need direct internet. The gateway node (often a VPS or home server) holds the uplink to LLM APIs.

**Who this guide is for:** developers and architects who already know Reticulum basics and want the fastest path from zero to a working LXMF endpoint.

## Before you start (5W2H checklist)

| Question | Answer for this guide |
|----------|----------------------|
| **What** are you building? | A Hermes gateway platform that dispatches mesh LXMF traffic to the agent |
| **Why** now? | To give off-grid nodes AI capability without custom gateway code |
| **Who** sends messages? | Your RNode, Sideband install, or any LXMF peer you allowlist |
| **When** does it run? | As soon as the Hermes gateway starts with the plugin enabled and Reticulum has mesh paths |
| **Where** does it live? | A Linux/macOS host with Python 3.11+ — VPS, home server, or mesh gateway |
| **How** do you validate success? | A mesh client message reaches the gateway; Hermes reply returns over LXMF |
| **How much** setup? | ~10 minutes: `install.sh`, two Hermes settings, one allowlist entry |

## Prerequisites

- Linux or macOS with Python **3.11+**
- [Hermes Agent](https://github.com/NousResearch/hermes-agent) installed and responding:

```bash
hermes chat -q "reply with OK only"
```

- (Optional) VPS with port **37428/TCP** open in the firewall, if internet-connected Reticulum peers should reach your node

## 1. Clone and install

```bash
git clone https://github.com/strtPath/hermes_reticulum_platform.git
cd hermes_reticulum_platform
bash install.sh
```

`install.sh` creates the venv (RNS/LXMF live there), prepares `~/.lxmf/storage`, copies `config/reticulum.conf` to `~/.reticulum/config` when it does not exist yet, installs both Hermes plugins (`reticulum`, `mesh-tool-gate`) into `~/.hermes/plugins/`, and adds them to `plugins.enabled` in `~/.hermes/config.yaml`.

Hermes directory plugins are opt-in: a copied plugin only loads when its name is listed under `plugins.enabled`, so this step is not optional — and the gateway must be **restarted** afterwards before anything takes effect.

## 2. Configure the Hermes profile

The adapter runs inside the gateway and reads the **Hermes profile's** environment — never this repository's `.env` (that file belongs to the archived bridge). Set in `~/.hermes/.env`:

```bash
# Required: without a display name the platform is considered not set up
# and registers nothing.
RETICULUM_DISPLAY_NAME="My Mesh Agent"

# LXMF hash of your mesh client (deny-by-default)
HERMES_RETICULUM_ALLOWED_USERS=YOUR_LXMF_HASH_HERE
```

**Why allowlist?** Unknown senders are rejected before reaching Hermes. Add the 32-character hex hash from your RNode identity, Sideband (Settings → Identity), or any client that will talk to the agent. `HERMES_RETICULUM_ALLOW_ALL=true` exists — set it only if you mean it.

Optional env keys the adapter honours: `RETICULUM_ANNOUNCE_INTERVAL`, `RETICULUM_STORAGE_PATH`, `RETICULUM_RNS_CONFIG_PATH`, `RETICULUM_REQUIRE_SHARED_INSTANCE`, `RETICULUM_HOME_CHANNEL`.

If you prefer gateway config over `.env`, `display_name` and `announce_interval`
also work as keys under `platforms.reticulum:` in `~/.hermes/config.yaml` —
the display name there enables the platform on its own. `.env` values win on
conflict.

## 3. Tame the progress stream (recommended)

The gateway's global default streams tool-progress and thinking ticks as separate one-shot LXMF messages — fine on Telegram, ruinous on a LoRa link (and it can hold back the real reply). In `~/.hermes/config.yaml`:

```yaml
display:
  platforms:
    reticulum:
      tool_progress: false
      thinking_progress: false
  show_reasoning: false
```

`install.sh` already ships `approvals.timeout: 900` for the mesh (the gateway default of 300s fails closed before a LoRa round-trip plus an AFK operator can answer). If you hand-edited config instead, keep it. If you also run `mesh-tool-gate` with custom gate timeouts, read the timeout-alignment rule in [README.md](README.md) before changing anything.

## 4. Adjust Reticulum (if needed)

File: `~/.reticulum/config` (or `config/reticulum.conf` in the repo as reference). The adapter shares this Reticulum instance; interfaces are configured here regardless of which Hermes path you run.

**TCP Server** — listens for connections on your machine (typical for a VPS node):

```ini
[[TCP Server Interface]]
  type = TCPServerInterface
  enabled = yes
  listen_ip = 0.0.0.0
  listen_port = 37428
```

**TCP Client** — connects to an existing mesh peer (optional; links off-grid nodes to internet gateways):

```ini
[[TCP Client]]
  type = TCPClientInterface
  enabled = yes
  target_host = YOUR_MESH_PEER_HOST
  target_port = YOUR_MESH_PEER_PORT
```

Firewall (example):

```bash
sudo ufw allow 37428/tcp
```

## 5. Start the gateway

```bash
hermes gateway restart   # or: hermes gateway start
hermes gateway status    # reticulum should appear as an enabled platform
```

At startup the adapter announces on the mesh. Get the agent's **LXMF address** (32-character hex hash) with `hermes-reticulum address` — a read-only command that prints it from the identity file under `RETICULUM_STORAGE_PATH` — or read it off Sideband/RNode once the node appears. This is the contact address mesh clients use to reach your agent.

## 6. Connect from the mesh

Pick the path that matches your deployment.

### Option A — RNode (off-grid, primary use case)

1. Ensure your RNode Reticulum instance has a path to the gateway (LoRa mesh and/or TCP/I2P peers)
2. Send an LXMF message to the gateway hash from your RNode/LXMF tooling
3. The reply routes back over Reticulum — no internet required on the RNode itself

**Success signal:** you receive a Hermes reply on the RNode after one or more mesh hops.

### Option B — Sideband (mobile / desktop)

1. Install [Sideband](https://github.com/markqvist/Sideband/releases/latest)
2. Configure a network interface (TCP to gateway IP:37428, or LoRa)
3. Add the gateway LXMF address as a **contact**
4. Send a test message

**Success signal:** Sideband shows an agent reply within your configured Hermes timeout.

### Option C — Any other LXMF client

Use the same address with NomadNet, custom LXMF scripts, or any Reticulum peer — no Sideband-specific setup required.

## Quick verification

- `hermes gateway status` — reticulum platform listed and enabled
- The node announces on the mesh (`hermes-reticulum address` prints its LXMF hash)
- A message from an allowlisted hash gets a reply; one from an unknown hash gets the automated rejection (or silence, per your ACL config)

If nothing announces, check in order: is `reticulum` in `plugins.enabled`? Is `RETICULUM_DISPLAY_NAME` set in the Hermes profile env (without it the platform silently never registers)? Does `~/.reticulum/config` have at least one working interface?

## Common issues

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Platform missing from `hermes gateway status` | Plugin not in `plugins.enabled`, or no `RETICULUM_DISPLAY_NAME` | Re-run `install.sh`, or add both yourself; restart the gateway |
| Message ignored, no reply | Sender not allowlisted (deny-by-default) | Add client hash to `HERMES_RETICULUM_ALLOWED_USERS` |
| Client won't connect | No Reticulum path or blocked port | Check mesh peers, IP, port 37428, firewall, LoRa links |
| Mesh floods with tiny status messages | Global `display.tool_progress` streaming onto a one-shot link | Set the per-platform overrides in step 3 |
| LXMF identity error | Corrupted local identity store | Delete `~/.lxmf/storage` **only** if you can regenerate contacts |
| Replies stop after a code update | Gateway imports the adapter live from this checkout; imports bind at start | `git pull` here, then `hermes gateway restart` (see [docs/skills/updating-the-adapter.md](docs/skills/updating-the-adapter.md)) |

## Next steps

- Architecture, gate timeouts, security: [README.md](README.md)
- Adapter design spec: [docs/specs/adapter/spec.md](docs/specs/adapter/spec.md)
- Archived bridge (standalone `hermes-reticulum run`): [src/hermes_reticulum/bridge/README.md](src/hermes_reticulum/bridge/README.md)
- Contribute or run tests: `pip install -e ".[dev]" && pytest tests/ -v`

When your first off-grid message gets a reply, you have a production-ready pattern: **mesh transport on the edge, AI reasoning at the gateway.**
