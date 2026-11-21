# Archived standalone bridge

This package is the original long-lived Reticulum/LXMF bridge. It drives the Hermes
CLI in one-shot mode: `hermes-reticulum run` starts an LXMF router, receives a mesh
message, runs one Hermes turn, and sends the reply back over LXMF.

It is kept because some installs still run it; it gets bug fixes only. It is archived:
new features belong in the platform adapter at `src/hermes_reticulum/plugin/`, which
ships Reticulum as a native Hermes gateway platform. The adapter design is in
`docs/specs/adapter/spec.md`.

## What stayed in `core/`

`core/` is now shared, not bridge-only:

- `core/acl.py` — the access-control gate. The adapter imports it directly
  (`plugin/adapter.py`), and the bridge's `control_server.py` does too.
- `core/tool_emoji.py` — the tool-name → emoji table, imported only by the bridge's
  modules (and their tests).

Everything else in `core/` moved here.

## Configuration

Do not confuse the two configuration layers:

- The **bridge** reads its `.env` from the repository root (`start.sh` sources it;
  `cli.py:_load_dotenv()` loads it). This is the legacy path.
- The **adapter** runs inside the Hermes gateway and reads the Hermes profile's
  `.env` / `config.yaml` through `platform_gate_env` and `_get_scoped_secret`. The
  repository `.env` is not read by the adapter.

If you are running the adapter, configure Hermes' `.env` and `config.yaml`, not the
repository's `.env`.

## Status

No new features land here. Bug fixes only for existing deployments. Anything that
overlaps the adapter should be fixed in the adapter and this copy left alone.

## Running the bridge

```bash
hermes-reticulum run          # LXMF router; runs one Hermes turn per message
hermes-reticulum address      # print the LXMF delivery hash of the saved identity
hermes-reticulum status       # show announce and storage state
```

Configuration comes from the repository `.env` (see `config/env.example`). Bridge
settings and defaults are listed below. The adapter also accepts
`RETICULUM_DISPLAY_NAME`, `RETICULUM_ANNOUNCE_INTERVAL`, and the legacy
`RETICULUM_STORAGE` key — through the Hermes profile, where its defaults may differ
(announce 60 min vs 30 here, storage `~/.hermes/.reticulum-gateway/storage` vs
`~/.lxmf/storage` here).

| Variable | Default | Notes |
|----------|---------|-------|
| `RETICULUM_DISPLAY_NAME` | `Hermes for Reticulum` | Mesh announce name |
| `RETICULUM_STORAGE` | `~/.lxmf/storage` | Identity/LXMF storage |
| `RETICULUM_STAMP_COST` | `8` | LXMF stamp cost |
| `RETICULUM_CONFIG` | *(RNS default)* | Reticulum config file path |
| `RETICULUM_ANNOUNCE_INTERVAL` | `30` | Re-announce cadence, minutes; floor 1 (below that it is a spam loop), 0 disables periodic re-announce |
| `HERMES_BIN` | *(auto-detected)* | Hermes CLI binary/path |
| `HERMES_TIMEOUT` | `300` | Seconds allowed per Hermes turn |

The ACL variables (`HERMES_RETICULUM_ALLOWED_USERS`, `..._BLOCKED_USERS`,
`..._ALLOW_ALL`) work the same on both paths. systemd units ship as
`config/hermes-reticulum.service` (system) and `config/hermes-reticulum.user.service`
(user unit); install.sh renders them from the checkout path.

The bridge always runs a local control endpoint on port 8471 (tool-step events plus
`/approve` `/deny` `/stop` `/steer`). The optional `mesh-tool-gate` plugin posts to
it and is inert for sessions it cannot identify as bridge sessions (`mesh-*` titles).
Timeout alignment for the gate lives in the top-level README.
