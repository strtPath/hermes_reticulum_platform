# Mesh-bridge findings — 2026-10-26 — adapter live delivery: the one-shot blocker

**Status:** adapter (gateway platform) replaces the retired `chat -q` bridge; live mesh
round-trip **confirmed working end-to-end** after a config fix. This doc records the
delivery-path diagnosis and the two remaining rough edges.

## TL;DR

The adapter was transmitting *system status text* (memory-recall indicators, tool-progress
ticks) over the mesh while **holding the model's real reply** — because the gateway's
per-platform display config was left at the global default (`tool_progress: all`), which
treats Reticulum like a streaming surface. Setting the per-platform overrides off made the
adapter finally call `send()` and deliver a real model reply.

## The fix (config, not adapter code)

```yaml
display:
  tool_progress: all          # global default — leave
  platforms:
    reticulum:
      tool_progress: false    # ← the fix
      thinking_progress: false
```

Set via `hermes config set display.platforms.reticulum.tool_progress off` (and
`thinking_progress`). Requires a gateway restart. After this, the adapter's `send()` is
reached and replies reach `DELIVERED`.

## What was actually happening (three distinct problems, all now understood)

### 1. Tool-progress/thinking ticks were being sent to the mesh as one-shot messages

`gateway/run_turn.py:1074` sets `tool_progress_enabled = progress_mode not in {"off","log"}`
and `needs_progress_queue = tool_progress_enabled or _thinking_enabled or ...` (line 1120).
Reticulum is not a webhook, so it was NOT exempted (`is_webhook` is the only platform that
disables it). Result: every tool step pushed a "working / iteration N / 150" tick over the
mesh as a separate LXMF message — exactly what the phone showed. RNS cannot stream or edit,
so each tick was its own one-shot send. The per-platform override (`display.platforms.<name>`
→ `resolve_display_setting`, `gateway/display_config.py:80`) is the intended off-switch.

### 2. The "holographic — recalled N memories" text is Hermes system code, not the model

`agent/memory_manager.py:409` `describe_recall()` builds that deterministic line, emitted via
`_emit_status` → `status_callback` → `_status_callback_sync` (`gateway/run_turn_progress.py:981`)
→ `_send_or_update_status_coro` → `adapter.send()`. It is a *lifecycle status event* meant for
a streaming/status-bar surface; on a one-shot platform it is transmitted as a real message.
**The user was right to insist this is not the model's answer.** It is the memory-prefetch
indicator, and on the mesh it looks like the reply.

### 3. The model's real reply was being killed by barge-in interrupt

`gateway/run_turn.py:1398` `_run_agent_monitor_for_interrupt` polls `adapter._pending_messages`
every 200 ms; on a new inbound it calls `agent.interrupt()` (hard stop). With the slow local
model (`qwen3.8-27b-gsq-rco-iq3_s`, 150-iteration budget), a turn takes minutes. Sending a
second message — or `/new` then a message — while the turn is still running lands in
`_pending_messages` and **hard-interrupts the model call**. The turn then ends with
`Operation interrupted: waiting for model response (N s elapsed).` and that notice is what
gets transmitted instead of the reply. This is the "I sent it but nothing (real) came back"
signature.

## Verified end-to-end success (new session, 14:23–14:24)

After the config fix and a `/new` that did take effect this time:

```
14:23:48  user:    'rns test message'            (session 20261026_142339_0c3ac3a5)
14:24:24  assistant: tool_calls (fact_store)
14:24:26  assistant: 'Received — round trip confirmed, mesh link up.'  finish=stop
14:24:26  response ready: 46 chars → adapter.send() → DELIVERED
```

A real model reply — tool call, answer, transmit, delivered. The delivery pipeline is fixed.

## What the logs prove about the earlier "I never saw it" exchanges

Every earlier "Message handed to the mesh" + "DELIVERED" line was the **notice/status path**
(home-channel intro, memory-recall indicator), not the model reply. The model replies were
either (a) suppressed by the progress/streaming gate, or (b) killed by barge-in interrupt.
The adapter's `send()` was never reached for a real reply until the config fix.

## Remaining rough edges (separate from delivery)

1. **`/new` session reset over RNS is flaky.** The 14:23 `/new` worked (new session id), but
   earlier ones resumed the old session. Spec §17.10 says the wiring is correct and the likely
   cause is the leading `/` being mangled or the auth gate. Worth re-verifying against the
   working case now that delivery works.
2. **Reply content is clumsy** — the model narrates its own internals ("holographic recalled a
   memory", "Operation interrupted…"). That is a prompt/behavior concern for the mesh platform
   (platform_hint + session state), not a delivery defect.
3. **Barge-in interrupt on a slow local model** — a second message during a long turn kills the
   reply. For a one-shot, high-latency platform this may warrant a different interrupt policy
   (queue instead of hard-stop), but that is a product decision, not a bug.

## PII note

No real peer hashes/display names in this doc; the owner hash is referenced only as "the
owner hash" / "the peer hash" and never written literally.