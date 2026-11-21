# Mesh-bridge findings — 2026-10-28 — adapter delivery and reconnect fixes (PR #23)

**Status:** merged to dev via PR #23 (`311ef0d`, squash). Live mesh round-trips confirmed
after a gateway restart, including a tool-call turn. This doc records the two delivery
bugs the PR fixed and the CI harness gap Greptile caught.

## TL;DR

Two separate bugs made the adapter look alive while being useless:

1. **LLM replies were computed and then dropped.** `deliver_inbound` called the raw
   gateway handler, which only computes the reply and returns it. The returned text was
   discarded, so every answer logged as "response ready" but never reached the mesh,
   while status pings (a separate `send()` path) still arrived. That is why the phone
   showed holographic-memory/"slow model" notices but never the actual reply.
2. **A failed connect could never be retried.** Every reconnect ran `RNS.Reticulum()`
   again. RNS is a strict process singleton, so once a first connect failed after RNS
   was up, every retry raised "Attempt to reinitialise Reticulum, when it was already
   running" until the gateway restarted. The trigger was a zero-byte LXMF ratchet file
   left by an unclean shutdown, which killed the LXMF router at startup.

## The fix (adapter code, PR #23)

- `deliver_inbound` routes through the inherited `handle_message`, the same pipeline
  every gateway platform uses: it runs the installed handler AND sends the reply.
  The raw-handler call stays only as a fallback for gateways that renamed the method.
  The gateway deny signal (A3 4.5 D7) is now read from `event._gateway_accepted`
  (False when a base gate dropped the event), not from a falsy handler return — the
  real handler returns `None` on admission reject and text on success, so
  falsy-but-not-None never happens on this path.
- `transport.start()` re-uses `RNS.Reticulum.get_instance()` when one exists and
  re-initialises the LXMF layer (router, delivery identity) instead of re-constructing
  RNS. A start after `stop()` behaves the same way; `RNS.exit()` hard-exits the
  process, so "re-initialise RNS from scratch" is not something an in-process adapter
  can do.
- Zero-byte `*.ratchets` files under `<storage>/lxmf/ratchets/` are moved aside (named
  `.corrupt-<ts>`) before the LXMF router is built, so an unclean shutdown heals on the
  next start instead of wedging the connect.

## The CI harness gap (Greptile P1)

`deliver_inbound` routing through `handle_message` exposed a stub-fidelity hole:
CI has no Hermes gateway, so `conftest.py` stubs `BasePlatformAdapter`, and its
`handle_message` was a no-op. That made the suite green locally (real gateway loaded
from `~/.hermes/hermes-agent`) and red in CI (admitted messages never reached the
handler). The stub now claims the event (`_gateway_accepted = True`) and runs the
installed handler. Reproduce the CI path locally with
`HOME=/tmp/fakehome venv/bin/python -m pytest tests/test_reticulum_adapter.py -q`.

## Verification

- Transport suite 52 passed, adapter suite 62 passed, with the real gateway AND with
  the CI stub. Ruff clean.
- Greptile review: pass (its single P1 was the stub gap above, fixed in commit
  `8d29c92` and verified).
- Live (gateway restarted 2026-10-28 11:04): `gateway_state.json` shows
  `reticulum.state = connected`; rnstatus reports the shared instance serving 2
  programs (bridge + adapter). Real round-trips on the mesh:
  `inbound message: platform=reticulum` → `response ready` → `SEND-CALLED` for the
  final reply → `outbound message reached DELIVERED`, including a turn with
  `tool_turns=1`.

## Spec updates (this PR)

- `docs/specs/adapter/a3-spec.md` 4.5 (D7): mechanism paragraph now describes the
  shipped `_gateway_accepted` signal instead of the planned "read the handler result".
- `docs/specs/adapter/spec.md` sections 5 and 13: "able to re-initialise RNS from
  scratch" corrected to "re-uses the live RNS singleton, re-initialises the LXMF
  layer" — the old requirement was impossible in-process.