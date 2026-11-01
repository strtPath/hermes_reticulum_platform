# Archived standalone bridge

This package is the original long-lived Reticulum/LXMF bridge. It drives the Hermes
CLI in one-shot mode: `hermes-reticulum run` starts an LXMF router, receives a mesh
message, runs one Hermes turn, and sends the reply back over LXMF.

It is kept because it is the deployed, working system and some installs still run it.
It is **not** the direction of travel. New work belongs in the platform adapter at
`src/hermes_reticulum/plugin/`, which ships Reticulum as a native Hermes gateway
platform. The adapter design is in `docs/specs/adapter/spec.md`.

## What stayed in `core/`

`core/` is now shared, not bridge-only:

- `core/acl.py` — the access-control gate. The adapter imports it directly
  (`plugin/adapter.py`), and the bridge's `control_server.py` does too.
- `core/tool_emoji.py` — the tool-name → emoji table used by both architectures'
  step/recap rendering.

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
