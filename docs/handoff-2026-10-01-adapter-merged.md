# Handoff: adapter PR #13 merged (A1 + A8 landed)

Date: 2026-10-01. Base branch: `dev`. Merge: squash commit `efef313`.

## What landed

PR #13 ("Adapter: platform adapter + A1 docs foundation") is **merged to `dev`**. The
feature branch `feat/reticulum-platform-adapter` is deleted. The squash carried:

- **A1** (docs foundation) — spec set `docs/specs/adapter/`, the PII quarantine rule, the
  registration `_bool_conv` fix (`src/hermes_reticulum/plugin/registration.py:32`, used at
  `:40` for `RETICULUM_REQUIRE_SHARED_INSTANCE`). Card status set to **done**; verification
  rows filled with re-runnable evidence.
- **A8** (approvals 900s) — `approvals.timeout: 900` ships in `plugins/reticulum/config.yaml`
  and is applied by `install.sh` (fast path and YAML-merge path; an existing user value is
  preserved). See spec §17.6 and ADR-0003. Verification row is **BLOCKED** on the live wire
  check, not passed: the mechanism is in place, the end-to-end latency behaviour is unproven.
- **Review-driven fixes** to the delivery layer and adapter, found by the review bot on the
  branch (not separate cards):
  - pending-send resolution, chunker part-count reservation (silent 200-byte loss at 155+
    parts), payload-keyed receipt attribution, the failed-callback path, sends off the event
    loop, and the cancellation handling (Option A: a cancel lets the in-flight send finish,
    the worker's real result decides the entry's fate, cancellation surfaced afterwards).
  - `scripts/security-sweep.py`: home-path allowlist matched by exact segment, not prefix
    (an account name that extends a generic placeholder no longer slips past it).
  - `adapter.connect()` now passes `PlatformConfig.extra` to the transport.

## What a next agent should know

- **Open cards** (A2-A7, A9, A10) are unchanged and listed in
  `docs/specs/adapter/tasks.md`. A7's transport half (settings reaching the transport) is
  done; the `apply_yaml_config_fn` registration is still open.
- **A8's wire check** is the one unproven acceptance row. If a live mesh test happens, fill
  the row and clear the BLOCKED.
- **A8's mechanism is install-time, not plugin-subtree.** The gateway reads
  `approvals.timeout` from top-level config; a plugin cannot set it from its own settings
  subtree. The value reaches the user's `config.yaml` via `install.sh`. Do not "fix" this to
  look plugin-native without a core hook.
- **Two stale git worktrees** existed at handoff time (`/tmp/pr2-worktree`, `/tmp/rns_dev`);
  they hold no unique work and can be pruned with `git worktree prune`.

## Rules that still bind

PII sweep before every commit/push (`scripts/security-sweep.py --untracked`); no AI
attribution in commits; never comment on the repo while babysitting (see
`docs/skills/babysit-pr-for-review-bot.md`).
