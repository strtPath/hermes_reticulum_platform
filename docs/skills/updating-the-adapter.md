# Skill: updating the adapter (keeping a running gateway current)

Answers "I pulled the repo — is the gateway running the new adapter yet?" and tells you
what else a given update demands. The short answer is almost always: pull, then restart
the gateway when it suits you.

## Why a pull and a restart are two separate steps

The Hermes gateway does **not** carry a copy of the adapter. What lives in
`~/.hermes/plugins/reticulum/` is a thin shim (`__init__.py`, `plugin.yaml`,
`venv_path.txt`). At gateway start the shim reads `venv_path.txt` — written by
`install.sh` with two roots, the repo venv and the repo `src/` — and prepends to
`sys.path` whichever root actually contains the `hermes_reticulum` package, plus the
venv's `site-packages` (where RNS/LXMF live). The adapter is then imported **live from
this checkout**.

Consequences:

- A `git pull` in the checkout changes what the *next* gateway start imports. There is
  nothing to reinstall or re-copy for adapter code.
- Imports bind at process start: there is no hot reload. A running gateway keeps using
  the code it loaded when it started until it is restarted. Restarting now or later is
  the operator's choice; the pulled code simply waits in the checkout.
- Python's normal mtime checks invalidate stale bytecode inside `src/`, so no
  `__pycache__` clearing is needed there.

## Procedure

```bash
bash scripts/update-adapter.sh          # pulls the current branch, classifies the diff
```

The script fast-forwards the checked-out branch (refusing on divergence or a detached
HEAD — it will not switch branches behind you), lists the new commits, and prints only
the extra steps the diff actually requires. It ends with the restart reminder.

Manual equivalent, if you prefer (follows the checked-out branch's own
upstream, same as the script; explicit fetch+merge rather than `git pull`
because this repo's `pull.rebase` config makes `git pull --ff-only` refuse
dirty trees):

```bash
up="$(git rev-parse --abbrev-ref '@{upstream}')"
git fetch "${up%%/*}" --prune
git merge --ff-only "$up"
git diff --name-only <old>..<new> -- src/hermes_reticulum \
    ':(exclude)src/hermes_reticulum/bridge' ':(exclude)src/hermes_reticulum/mesh-tool-gate'
hermes gateway restart
```

## What each kind of change needs

| Diff touches | What it needs beyond the pull |
|---|---|
| `src/hermes_reticulum/` (except `bridge/` and `mesh-tool-gate/`) | Gateway restart only — imported live from the checkout |
| `pyproject.toml`, `requirements.txt` | Sync the installed venv first — the one named in `~/.hermes/plugins/reticulum/venv_path.txt` (default install: `venv/`; `--global` installs use `install.sh --global`) — then restart |
| `plugins/reticulum/` (the shim itself, rare) | Re-copy the shim to `~/.hermes/plugins/reticulum/` (re-run `install.sh`), then restart |
| `src/hermes_reticulum/mesh-tool-gate/` | Re-copy — this plugin **is** a copy, not a shim: replace `~/.hermes/plugins/mesh-tool-gate/` and clear its `__pycache__`, then restart |
| docs, tests, `config/` only | Nothing — the gateway imports none of it |

The asymmetry to remember: `reticulum` is a shim pointing at the checkout, while
`mesh-tool-gate` is a real copied directory. A branch that touches the gate without a
re-copy leaves the gateway loading the stale copy.

## Restart and verify

```bash
hermes gateway restart
```

A restart drops the live gateway session (in-flight chats end); pick the moment. Then
confirm the adapter came back:

- `hermes gateway status` — service running.
- The gateway state file (`gateway_state.json` under the Hermes home) — the
  `reticulum` platform entry should read `state: connected` with a fresh
  `updated_at`.
- The gateway agent log — adapter transport lines (registration, announce) appear on
  startup.

If the platform is missing entirely after a restart, the shim degraded to a no-op
because `hermes_reticulum` or its deps were not importable — check that the checkout
exists at the path written in `~/.hermes/plugins/reticulum/venv_path.txt` and that the
venv has the deps (`venv/bin/pip install -e .`).
