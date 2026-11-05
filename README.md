# Hermes for Reticulum

**Put an AI agent on your Reticulum mesh — reachable from off-grid RNodes, Sideband, and any LXMF client, without a central messaging server.**

The supported path is the **Hermes gateway plugin** in `src/hermes_reticulum/plugin/`: Reticulum/LXMF runs as a native Hermes platform inside the gateway process. The original standalone bridge (`hermes-reticulum run`) still ships in `src/hermes_reticulum/bridge/`, but it is archived — kept running for existing deployments, not accepting new features. See [the bridge README](src/hermes_reticulum/bridge/README.md) for its details; this README covers the plugin path.

```
RNode / Sideband / any LXMF client          Hermes gateway (Reticulum node)
┌──────────────────────────┐   LoRa / TCP / I2P   ┌──────────────────────────┐
│  Off-grid mesh client    │ ◄════ Reticulum ═══► │  Hermes Agent platform   │
│  (no internet required)  │   LXMF (encrypted)   │  + mesh tool gate        │
└──────────────────────────┘                      └──────────────────────────┘
         ▲                                                    │
         │         mesh hops through Reticulum peers          │
         └──────── (field nodes, gateways, TCP/I2P bridges) ──┘
```

## Who this is for

| Persona | What you need | What this gives you |
|---------|---------------|---------------------|
| **Mesh developer** | A standard LXMF endpoint you can hit from Python, Sideband, or custom tooling | A drop-in Hermes platform — no proprietary API, no Sideband lock-in |
| **Systems architect** | AI capability at the edge of a heterogeneous mesh (LoRa + TCP + I2P) | One gateway pattern: mesh clients stay off-grid; the gateway holds the uplink to LLM APIs |
| **Field operator** | Reliable comms where cellular and Wi-Fi fail | Ask your agent from an [RNode](https://github.com/markqvist/Reticulum) in the bush; replies route back over the same mesh |

If you design or operate off-grid communication systems, this is the missing link between **Reticulum's transport layer** and **Hermes Agent's reasoning layer**.

## The problem this solves

Off-grid mesh nodes can move data. They usually cannot reach a capable AI without brittle workarounds — custom gateways, ad-hoc HTTP tunnels, or forcing every client to carry its own internet link. This project closes that gap: field nodes talk to an agent through the mesh they already trust, not through a new protocol stack. Typical deployment takes under 10 minutes.

## What you get

- **AI reachable from off-grid mesh nodes** — especially [RNodes](https://github.com/markqvist/Reticulum) on LoRa; no Android or Sideband required
- **Client-agnostic LXMF** — [Sideband](https://github.com/markqvist/Sideband), NomadNet, custom Python apps, or any Reticulum peer that speaks LXMF
- **Reticulum-native routing** — LoRa, TCP, I2P, and other transports interoperate on one network
- **End-to-end encryption** — Curve25519 key exchange + AES-128; Ed25519 message signatures
- **No central messaging server** — identity-based LXMF delivery across autonomous peers
- **Internet where it belongs** — the gateway node (often a VPS or home gateway) reaches LLM APIs; mesh clients do not need a direct ISP link
- **Access control by LXMF identity hash** — allowlist or blocklist senders before they reach Hermes
- **Pre-execution approval over the mesh** — dangerous commands prompt `/approve` or `/deny` on your LXMF client before anything runs

## Real-world scenarios

| Scenario | Challenge | Outcome |
|----------|-----------|---------|
| **RNode in the field** | No cellular coverage; operator needs situational answers | Message goes out over LoRa; Reticulum forwards through mesh peers; Hermes replies on the return path |
| **Off-grid camp or event** | Local LoRa mesh, no ISP on site | One gateway node with TCP or I2P reachability acts as the AI endpoint for the whole mesh |
| **Hybrid mesh** | Remote nodes on LoRa; infrastructure on TCP | Gateway on a VPS joins both worlds; Hermes uses cloud LLMs while clients stay radio-only |
| **Sideband on a phone** | Mobile operator wants the same agent contact | Same LXMF address — convenient client, not a requirement |

## Requirements

| Item | Version / detail |
|------|------------------|
| Python | 3.11 or newer (Linux and macOS; install.sh avoids GNU-only utilities) |
| Hermes Agent | Installed and working (`hermes chat -q "reply OK only"`). Verified against Hermes **v0.19.0** |
| Gateway node (optional) | Public IP with TCP port **37428** open, if internet-connected Reticulum peers should reach you |

## Quick start

```bash
git clone https://github.com/strtPath/rns_hermes_endpoint.git
cd rns_hermes_endpoint
bash install.sh          # venv + both Hermes plugins + plugins.enabled entries
cp config/env.example .env   # bridge config only; plugin reads Hermes profile env
hermes gateway restart   # activates the reticulum platform
```

`install.sh` does more than create the venv: it copies both Hermes plugins (`reticulum`, `mesh-tool-gate`) into `~/.hermes/plugins/` **and** adds them to `plugins.enabled` in `~/.hermes/config.yaml`. Hermes directory plugins are opt-in, so an install that only copied the directories would never load them. Restart the Hermes gateway afterwards so they activate.

The full walkthrough is [QUICKSTART.md](QUICKSTART.md). Note that QUICKSTART currently leads with the archived bridge path; for the plugin, configure the Hermes profile's `.env` and `config.yaml` — the repository `.env` belongs to the bridge only.

## Configuration (plugin path)

The adapter runs **inside the Hermes gateway** and reads the Hermes profile's `.env` / `config.yaml` — never the repository `.env`. Key settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `HERMES_RETICULUM_ALLOWED_USERS` | *(empty)* | LXMF hash allowlist (deny-by-default) |
| `HERMES_RETICULUM_BLOCKED_USERS` | *(empty)* | LXMF hash blocklist |
| `HERMES_RETICULUM_ALLOW_ALL` | `false` | Allow any sender — set only if you mean it |
| `HERMES_MESH_CONTROL_URL` | `http://127.0.0.1:8471` | Where the gate plugin posts `/gate/notify` |

Each client has a 32-character hex LXMF identity hash — from Sideband (Settings → Identity), your RNode/Reticulum identity, or the address printed when the platform starts.

The shipped `plugins/reticulum/config.yaml` sets `approvals.timeout: 900` as this platform's documented default: a multi-hop LoRa path can take minutes for a prompt and answer to cross, and 300s (the Hermes default) fails closed before an AFK operator can reply.

### Tool emojis

Tool activity on the mesh uses the same per-tool emoji the Telegram gateway shows (`📖 read_file`, `💻 terminal`, `🔍 web_search` …), so a transcript reads the same whichever platform the operator is on; a failed call is always `❌`. The table ships with the adapter, and to re-sync without waiting for a release, drop a flat `{"tool_name": "emoji"}` map at `~/.hermes/reticulum_tool_emojis.json` — entries there win over the built-in table.

### Clean replies on the mesh

Hermes renders a "Reasoning" recap panel that would be forwarded verbatim to LoRa/Sideband clients. For a mesh deployment, disable it in `~/.hermes/config.yaml`:

```yaml
display:
  show_reasoning: false
```

This is a Hermes display setting, not an adapter setting. To keep reasoning on your local CLI, run the gateway with a dedicated Hermes profile (`HERMES_HOME`) that has `show_reasoning: false`.

## Pre-execution gate

When the agent attempts a dangerous command, the `mesh-tool-gate` plugin prompts the operator on the mesh to `/approve` or `/deny` before the tool runs. It reuses Hermes' own dangerous-command detection — no duplicated patterns — and **fails closed**: an unanswered gate blocks the tool.

**Timeouts must stay aligned** or the gate wedges when the operator is AFK. Three layers stack: the control-server deny clock (`HERMES_MESH_APPROVAL_TIMEOUT`), the plugin's blocking POST (`MESH_GATE_TIMEOUT`), and Hermes' hook-callback timeout (`plugins.hook_callback_timeout`, in `~/.hermes/config.yaml`; default 30s, hard max 600s). They MUST satisfy:

```
MESH_GATE_TIMEOUT >= HERMES_MESH_APPROVAL_TIMEOUT
MESH_GATE_TIMEOUT < plugins.hook_callback_timeout < 600
```

Hermes' hook wrapper fires far before a 900s gate resolves unless you raise it, and after a timeout Hermes suppresses `pre_tool_call` for the rest of the turn. Since v0.21.x the gate also refuses to start if `MESH_GATE_TIMEOUT < HERMES_MESH_APPROVAL_TIMEOUT`. Raising the gate toward 900s is pointless — the 600s hard clamp in Hermes core caps the real operator window at ~590s. A safe example for a fast link: 480/480/490. Full rationale: [docs/pre-tool-callback-timeout-issue.md](docs/pre-tool-callback-timeout-issue.md).

Optional `MESH_GATE_TRIAGE=allow_benign` (with an `OPENROUTER_API_KEY` or `TYPESAFE_API_KEY`) lets a classifier auto-allow routine, benign tool calls before the human gate is offered; anything sensitive, uncertain, or below `MESH_GATE_TRIAGE_CONF` (default 0.6) still escalates, and errors fail open to the human gate. `execute_code` is always human-gated: its opaque payload can't be classified from a truncated excerpt, so triage may deny it but never auto-allow it. Details: [docs/mesh-gate-triage-confidence-floor.md](docs/mesh-gate-triage-confidence-floor.md).

## Keeping the adapter up to date

The gateway imports the adapter **live from this checkout** — the copy in `~/.hermes/plugins/reticulum/` is a thin shim that points `sys.path` at this repo's `src/`. Updating is two steps: `git pull` here, then a Hermes gateway restart to load the new code (imports bind at gateway start; there is no hot reload).

`scripts/update-adapter.sh` does the pull and tells you what the diff actually needs (a plain restart, a venv sync if dependencies moved, a re-copy if `mesh-tool-gate` changed). Full explanation: [docs/skills/updating-the-adapter.md](docs/skills/updating-the-adapter.md).

## Connecting from the mesh

Any LXMF client on Reticulum can reach the agent. Add the gateway's LXMF address as a contact or destination, then send a plain-text message.

- **RNode (primary off-grid use case)** — run Reticulum on the RNode with a LoRa interface linked to your mesh; ensure it has a path (direct LoRa or via TCP/I2P gateway peers) to the node; replies route back over the same mesh.
- **Sideband (Android / desktop)** — install [Sideband](https://github.com/markqvist/Sideband/releases/latest), configure a network interface, add the address as a contact.
- **Other LXMF clients** — NomadNet, custom Python scripts using the [LXMF](https://github.com/markqvist/lxmf) library, or any Reticulum node configured for LXMF delivery. No Sideband-specific features are required.

## The archived standalone bridge

`hermes-reticulum run` starts a standalone LXMF router that runs one Hermes turn per message and replies over LXMF. It remains the deployed path for some installs and gets bug fixes only; no new features land there. Its configuration lives in the repository `.env` (the adapter never reads it), its systemd units are `config/hermes-reticulum*.service`, and its full documentation — commands, environment variables, systemd setup, announce cadence — is in [src/hermes_reticulum/bridge/README.md](src/hermes_reticulum/bridge/README.md).

## Development

```bash
python -m venv venv && source venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
ruff check src/ tests/
```

## Security

- End-to-end encryption (Curve25519 + AES-128), forward secrecy via ephemeral links
- Ed25519 message signatures
- Access control by identity hash; deny-by-default allowlist
- Pre-execution human gate for dangerous commands, fail-closed
- The LXMF hash is public on the mesh (like a contact identifier)

## License

MIT

## Acknowledgments

- [Reticulum](https://reticulum.network) — mesh networking stack (RNode, transports, routing)
- [LXMF](https://github.com/markqvist/lxmf) — messaging protocol
- [Sideband](https://github.com/markqvist/Sideband) — LXMF client (one of many)
- [Hermes Agent](https://github.com/NousResearch/hermes-agent) — AI agent
- [rns_hermes_endpoint](https://github.com/apolosan/rns_hermes_endpoint) — the original project this was forked from
