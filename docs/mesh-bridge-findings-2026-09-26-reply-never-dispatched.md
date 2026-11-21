---
title: Finding — Reticulum reply generated but never dispatched (gateway final-send lane)
date: 2026-09-26
status: resolved-after-restart
---

# Reply "generated" but never reaches the adapter's send()

## Symptom

After the announce fix landed (phone hears "<new-identity>"), a phone → agent
message arrives, the GPU generates a reply, the gateway prints
`response ready: platform=reticulum ... response=32 chars`, but the reply
never arrives on the phone. The phone's client shows its outbound ("hello") as
delivered — the inbound leg works.

## What is verified working (ruled out)

- **Mesh path to the peer is live.** `rnpath <peer>` → "Path found ... 2 hops
  away via ... TCPInterface[rns.michmesh.net:7822]". Not a routing/path issue.
- **Transport `send_to` works.** Unit-tested (49 passed) and the outbound path
  logic is exercised; a path exists.
- **Adapter connected & announced.** gateway.log: `✓ reticulum connected`
  (19:05); agent.log: `Announced delivery identity <new-identity>`, periodic
  re-announce armed. Running new code (gateway restarted 19:04 after the fix).
- **Streaming is OFF** (not the eat-the-reply bug). `reticulum.streaming`
  resolves `False` today; NO `Suppressing normal final send` line in the log
  window. So the known streaming-suppression failure does not apply.

## The absence that pins it

After `response ready` (19:13:28) the gateway emits **nothing** for the
Reticulum platform:
- No `[Reticulum] Sending response (N chars)` line (base.py:4206) — Telegram
  emits this on every reply; Reticulum never does.
- No `delivery_obligations` row for platform=reticulum (the `send_final_ledgered`
  bracket at base.py:4208 records one before every send). All obligations are
  `platform=telegram`.
- No transport-level "Message handed to the mesh", no CANCELLED/FAILED, no
  adapter error, no exception.

Meaning: the reply left the agent turn (`response ready`) and the gateway's
final-send lane for the Reticulum session **never reached the adapter's
`send()`**. `send_final_ledgered` was not invoked.

## Session / routing state (all correct)

The gateway routing index has the Reticulum DM session → Hermes session id
`20260925_071537_50d17b44`, `updated_at` 19:13:28.818 (= the hello turn),
`origin.platform=reticulum`, chat_id the peer hash. The routing index is fine.

## Honest status

The reply is dropped between the agent turn and the Reticulum adapter's `send()`,
with zero logging. This is a gateway final-delivery lane issue for the plugin
Reticulum platform, NOT a mesh/transport defect. The precise decision in
`gateway/run_turn.py` / `gateway/platforms/base.py` that skips the send for a
plugin platform under these conditions is not yet identified from logs alone.

## Not relevant (ruled out)

- `mesh-tool-events` hook POST failures (`Connection refused` on /step/full):
  the hook is deliberately passive, dispatches to a daemon thread, and swallows
  errors — it cannot block the final send. It fires because the standalone
  bridge's control server (port 8471) is not running under the adapter model.
- Telegram/Discord credential-sharing warnings between profiles: unrelated to a
  Reticulum delivery.
- tell-tale suppression/cursor: absent.

## Next step (diagnostic, not yet done)

Instrument the adapter's `send()` (temporary debug log) or the gateway's
`_handle_message_with_agent` return path to capture the exact decision, then
reproduce a fresh Reticulum message. The candidate gates in run_turn.py:
`_is_session_run_current`, `_intentional_silence`, `diagnostic_wake_muted`,
`_deliver_queued_first_response`, and the final `adapter.send` dispatch.

## Resolution (2026-09-26, instrumented after restart)

Temporary WARNING logging was added to `ReticulumPlatformAdapter.send()` and the
gateway was restarted to load it, then a fresh `ping restart test` message sent.
The instrumented run proves the send path is healthy when freshly loaded:

- `inbound message: platform=reticulum ... msg='ping restart test'` (20:02:15)
- `TEMP-DEBUG Reticulum.send ENTER ... content_len=166 connected=True transport=True`
  → the gateway DOES reach `adapter.send()` for a Reticulum reply.
- `Message handed to the mesh for the peer (hops=2, method=direct)` — direct,
  2 hops to the phone.
- `TEMP-DEBUG Reticulum.send RETURN: success=True`
- `Reticulum: outbound message reached DELIVERED (delivered)` — the phone got it.

So the round-trip works. The earlier 19:13 drop (same engine, reply = 32 chars,
nothing after `response ready`) is best explained as a stale instance state in
the gateway that had been running since 19:04 while the code changed under it —
the freshly restarted gateway (20:01) delivers correctly through the identical
`adapter.send()`/transport path. No code defect in the send lane was found.

## Separate, unbuilt: tool-call visibility on the mesh

In the SAME turn a tool call DID run on the host (`tool terminal completed`,
`tools.terminal_tool: Creating new local environment`), and the
`mesh-tool-events` hook tried to push the step to the operator, but POST
`/step/full` → `Connection refused`: the standalone bridge's control server
(port 8471) no longer exists under the gateway-adapter model, so tool progress
/ tool calls are NOT surfaced to the phone. That is a distinct, never-built
feature (different from the reply path, which now works). Believed design:
expose tool progress to the Reticulum chat via `display.platforms.reticulum`
tool_progress settings (`tool_progress: new` in config) rather than the old HTTP
bridge control server.