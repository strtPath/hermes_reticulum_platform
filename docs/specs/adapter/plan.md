<!-- Part of the adapter feature bundle: docs/specs/adapter/.
     This file is the moved working decisions from the old spec, sections 18-21,
     numbering kept. The build cards in tasks.md cite these sections.
-->

# Adapter working decisions (spec sections 18-21, moved)

## 18. Context overflow and session growth (observed 2026-09-30)

Reproduced from gateway logs after a restart. Symptoms, in order: replies arrived normally
after reconnect (22 API calls, final send DELIVERED), then stopped while the inference GPU
stayed busy; and every mesh message resumed one growing session even after `/new`.

### 18.1 Root cause A: declared context window larger than the endpoint's slot

The llama.cpp server serves a slot window of `n_ctx=70144` (confirmed live from its `/props`
endpoint: `default_generation_settings.n_ctx`), while its `/v1/models` payload advertises
`n_ctx_train=262144`. Hermes's context-length resolution tries a live local probe
(`agent/model_metadata.py`, `_probe_local_context_length` / `_query_local_context_length`),
which reads the runtime `n_ctx` and would have gotten 70144. That probe never runs for this
deployment: its gate is `is_local_endpoint(base_url)` (`agent/model_metadata.py:703`), which
classifies loopback, RFC-1918, and Tailscale CGNAT (`100.64.0.0/10`) addresses, not MagicDNS
hostnames. `https://<host>.tail<net>.ts.net/v1` fails every arm (dotted, non-CGNAT), so the
agent falls through to a default that exceeds the slot. The request grows past 70144, the
server answers `HTTP 500: Context size has been exceeded`, and the turn fails closed.

Why replies *stopped* rather than *degraded*: the compression pass then ran on an
already-overlong transcript, burned its full time ceiling without producing a summary, and
the gateway classified the turn as a context-overflow failure. The overflow classifier is
correct behaviour (`gateway/run_turn.py`, `_hmwa_classify_turn_failure`): it skips transcript
persistence "to prevent session growth loop" and auto-resets the session after compression
exhaustion. The GPU stayed busy during the doomed compression attempts, which is what made
the outage look like a transport failure. It was not: the adapter, shared-instance attach,
and outbound lane were all healthy throughout.

Fix (config, not code): pin the window. `model.context_length` is the first arm of the
resolution chain ("explicit config override - user knows best", `get_model_context_length`)
and the gateway reads it per-turn (`gateway/run_turn.py`, `_raw_ctx` → `hs.config_context_length`).

```yaml
# config.yaml
model:
  context_length: 65536   # below the llama.cpp slot's n_ctx (observed 70144)
```

Pin below the live `n_ctx`, never `n_ctx` itself: Hermes estimates tokens and every request
carries system prompt + tool schemas as overhead. With `compression.threshold: 0.5`, a 65536
window compacts at roughly 33k request tokens, far below the wall. If the llama.cpp slot is
later relaunched with a different `n_ctx`, update this value to match; the stale-cache
reconcile (`_reconcile_local_cached_context_length`) cannot help because the probe never
fires for MagicDNS URLs.

### 18.2 Root cause B: `/new` resets history, not the visible session identity

Verified in gateway source: `/new` from a mesh peer reaches command dispatch (section 17.10's
chain holds: `deliver_inbound` → gateway `_handle_message` → `_hm_dispatch_idle_commands` →
`_hm_cmd_new` → `_handle_reset_command`, `gateway/slash_commands_session.py`). What it resets
is the session *row*: the old row is ended with `end_reason="session_reset"` and a new
session id is promoted under the same session key
(`gateway/session.py`, `session_recovery.py::_promote_session_reset`).

What it does not change is the session *key*. For this platform the key is
`agent:main:reticulum:dm:<peer-hash>` — a stable per-peer identity the adapter derives from
the transport, not from any bridge thread name. So after `/new`:

- history is genuinely cleared (the growth loop is broken; the next turn starts empty);
- but the log line still shows the same `session=agent:main:reticulum:dm:...`, and
  `hermes sessions`/resume surfaces still name the lane by that key.

To a phone user this reads as "still the same growing session". It is a presentation gap,
not a persistence bug: `/new` does break growth (the auto-reset at 09:33 also did). Do not
"fix" this by making the adapter mint a new key per `/new`: a new key would fork a new lane
with none of the gateway's session-history machinery attached, and the peer destination
identity is exactly what makes a resumed lane findable.

Spec ruling: the adapter's contract is one durable lane per peer; `/new` truncates the lane,
it does not rename it. If a user-facing signal is wanted later, it belongs in the gateway's
reset ack ("session reset — same conversation lane, new session id"), not in adapter-side
key churn. The old bridge's `Session reset — new thread: mesh-...` counter is bridge-only
behaviour and has no meaning under the adapter model; do not port it.

**2026-09-30 follow-up (is this how Telegram works?):** no. The visible difference is real,
and it is structural, not a Telegram quirk. `build_session_key` (`gateway/session.py:664`)
lays keys out as `<ns>:<platform>:<chat_type>[:chat_id][:thread_id][:user]`, and the
Reticulum adapter sets `chat_type="dm"` with **no `chat_id`** on every inbound. The key falls
back to the participant id (the peer hash), which is exactly why the lane name never moves.
Telegram DMs carry a stable `chat_id`, so the lane is addressable independently of the sender;
the reset machinery ends the row and promotes a new session id for both platforms the same
way. The visible "fresh" feeling on Telegram comes from chat_id stability plus platform-side
presentation, not from key rotation (the gateway invariant is that keys never rotate).
Ruling stands: keep the durable lane; if the peer wants a visible marker after `/new`, emit
a synthetic reset ack from the adapter, do not fork keys. Tracked as O-7.

*Corrected 2026-11-04:* the adapter cannot emit a reset ack — the gateway composes it
(`gateway/slash_commands_session.py:242`, `:283`). Card A5 was dropped on that evidence; see its
card record in `tasks.md`. The lane-keep / no-key-churn half of this ruling is unaffected.

### 18.3 Contributing factor: mesh-tool-gate step POSTs point at a deleted server

`mesh-tool-gate`'s step-push posts to the old bridge's control server (`127.0.0.1:8471`,
`HERMES_MESH_CONTROL_URL`). Under the adapter model that server never starts (it lived in the
bridge's `cli run`), so every step logs `Connection refused`. This is log noise, not a send
failure: the hook is passive and the error is swallowed; the final send is unaffected.
Section 17.9 already names the replacement (`display.platforms.reticulum` tool progress).
Until that replacement lands, disable the step POST rather than re-creating the control
server: point `HERMES_MESH_CONTROL_URL` at nothing or gate the POST on the server's presence.
Out of scope for this spec section: building the tool-progress surface itself.

### 18.4 Invariants added by this section

The adapter never asserts a context window it has not verified against the serving endpoint;
when a MagicDNS (or other non-CGNAT) URL disables the local probe, `model.context_length` in
config is the only correct answer and must be pinned below the slot's live `n_ctx`.

A session key for this platform is stable for the life of the peer lane. Reset commands
truncate history inside the lane; they never mint, fork, or rename the key.

A context-overflow turn failure must not be read as transport loss when diagnosing mesh
outages: check the adapter's outbound lane first, then the session's token budget.

## 19. Feature parity addendum (2026-09-30): bridge features the adapter dropped

`docs/feature-parity-roadmap.md` (2026-09-19) is a **bridge-era** document: every "✅ done"
there was built into the standalone bridge (`cli run`), and the adapter model deleted that
runtime. The roadmap's checkmarks do not describe the shipping adapter. This section is the
delta, feature by feature, as of 2026-09-30, with what must be re-homed into the adapter.

### 19.1 Landed on the adapter side (parity already restored)

| Bridge feature | Adapter home | Evidence |
|---|---|---|
| Session continuity (1.1) | Gateway session store, key `agent:main:reticulum:dm:<hash>` | §18.2; no adoption heuristics needed |
| Announce + display name (1.x) | Adapter announce path, `/announce` cadence | §12 |
| RNS-restart resilience (1.x) | Shared-instance attach; adapter re-attaches, never reinitialises | §13 |
| Chunk budget + `[tag i/N]` tagging (1.7) | `delivery.chunk_for_send` | §7 |
| Downlink ack tracking (1.7) | Propagation-delivery tracker (adapter-owned pending set) | §6 |
| Pre-execution approval gate + Jev triage (4.7) | `mesh-tool-gate` `pre_tool_call` plugin (in-process, gateway session key) | plugin `__init__.py` |
| `/approve`, `/deny`, `/steer`, `/model`, `/new`, `/help` | Gateway slash-command dispatch via `deliver_inbound` | §17.10 |

### 19.2 Dropped by §7 with no replacement (parity gap; each needs a decision)

| # | Bridge feature (roadmap ref) | What it did | Adapter status | Proposed disposition |
|---|---|---|---|---|
| P-1 | Tool-call visibility (Tier 3, 2026-08-22) | Step watcher polled `state.db` + `agent:step` pushes; per-tool emoji lines streamed to peer; `/tools` recap | **Gone.** §7 deleted the watcher; `display.platforms.reticulum` tool progress was never implemented. Gateway now emits tool events in-process; nothing consumes them for this platform. The dead symptom: mesh-tool-gate step POSTs to 8471 (§18.3) | **Decided 2026-09-30 (peer): build** (§21.3). Gateway-hook step pusher in the adapter repo: consume tool events → `chunk_for_send` → peer. Emoji registry ports unchanged (`tool_emoji.py`, 199 lines, gateway drift test); the emoji naming each tool must be the same registry Telegram/CLI use (§21.3) |
| P-2 | `/steps on\|off` step-through mode | Model instructed to announce each tool before running it; full call + full output chunked across posts | Gone | Re-home as a per-session flag the adapter injects into the system prompt; state file pattern already proven (`HERMES_STEP_MODE_FILE`) |
| P-3 | `/hold` / `/go` checkpoint gate | Final reply held until release or 30-min auto-release | Gone | Adapter-side send gate: `send()` holds the final reply text until `/go`; no gateway change needed. `/go` reaches the adapter through the same command path as `/approve` |
| P-4 | `/pause` / `/resume` emergency stop | Halts all new Hermes work mesh-wide | Gone | The gateway's `/platform reticulum pause` covers the platform lane but is not peer-facing. Either map `/pause` to a documented gateway command or add a tiny adapter-side queue hold |
| P-5 | `/retry` | Resends last prompt | Gone | Trivial adapter-side: adapter stores last inbound text per lane; `/retry` re-enters it via `deliver_inbound` |
| P-6 | `/usage`, `/status` bridge health | Model + session + tokens + process + watchdog, over mesh | Gone | `/usage` is gateway-provided (`/usage` exists in the gateway registry). `/status` needs an adapter-built answer: RNS attach state, announce cadence, watchdog (§19.3), peer pending-set depth |
| P-7 | `/verbose` | Toggle tool-args/results detail in recaps | Gone | **Decided 2026-09-30 (peer): build with P-1** (§21.3). Per-session detail flag on the step pusher. Default **on**, like the CLI default; the off state is the truncated recap, like the CLI's truncation |
| P-8 | Clarify round-trip (bridge watcher) | `clarify` tool question pushed to peer, answer captured out-of-band | **Gap.** §7 deleted the watcher and asserts "the gateway sets `agent.clarify_callback` in-process where a real user exists." **Unverified for this platform:** no adapter code registers a reticulum-facing clarify callback. Until verified, `clarify` from a mesh turn has no delivery path | Verify the gateway's clarify plumbing reaches plugin platforms (probe `agent.clarify_callback` wiring in `gateway/run_turn.py`); if absent, the adapter registers one onto the existing command/answer path |
| P-9 | Liveness watchdog (4.5, `BridgeLiveness`) | Daemon thread probing `RNS.Transport` job freshness + `sd_notify(WATCHDOG=1)` for systemd `WatchdogSec`; idle-heartbeat marker file | Gone with the bridge process. The gateway's own systemd unit has no RNS-aware watchdog, so a wedged RNS interface inside the gateway is now invisible to supervision | Port `bridge_liveness.py` (211 lines) as a daemon thread owned by the adapter instance (not the bridge); same probe, same marker file; the adapter is long-lived inside the gateway, so it can ping the gateway unit's watchdog if that unit gains `WatchdogSec` |
| P-10 | Downlink pacing + ack-timeout sweep (1.7 v2) | 500 ms inter-chunk pacing; `HERMES_DOWNLINK_ACK_TIMEOUT_S` sweep; `[pN i/N]` | Partial. Chunking + propagation tracker survive (§19.1); pacing and timeout sweep were in the deleted queue (§7 dropped the queue on purpose) | Pacing belongs in `chunk_for_send`'s caller loop inside `send()` (it is a property of LoRa interfaces, not of a scheduler); ack-timeout sweep belongs in the adapter's delivery-tracker housekeeping |
| P-11 | Preflight (1.5) | Checks `hermes` binary, storage, config before RNS start | Gone by design (no child binary). Retain the storage/config half as `connect()` diagnostics so a misconfigured adapter fails loudly instead of silently skipping (§17.1's `is_connected` trap) |
| P-12 | RSSI/SNR profiler (1.7, `profiler.py`) | Reading `RNS.Transport.local_client_rssi_cache` | Open even in bridge era (needs a real RNode radio). Port as an adapter diagnostic once a radio exists; no action now |
| P-13 | Structured command audit log (4.4, partial) | Commands logged to journal only | Same gap in adapter model. Add adapter-side audit entries (command, peer hash, ts) to the adapter's own log lane; cheap |
| P-14 | Tier 5 media (voice-out, image-out/in) | Never built | Still out of scope for the adapter (§9), but the transport design (RNS `Resource` + LXMF hash pointer) is unchanged and remains the roadmap for a v2 |

### 19.2a Dispositions superseded 2026-11-04 (read before acting on the table above)

The §19.2 rows were written 2026-09-30, when the adapter was the only thing that could answer a
peer. Cards A4, A6, A9 and A10 were re-scoped on 2026-11-04 after reading the installed gateway
source; the table above is kept as written for history, and these rows supersede it.

| # | Row said | What is true (source) | Superseded by |
|---|---|---|---|
| P-1 | "Adapter-side step pusher consuming tool events" | The gateway already renders tool progress per platform and delivers it through the adapter's own `send()` (`gateway/run_turn_progress.py:626`); the adapter only has to declare its display defaults (`gateway/platform_registry.py:117,121`) | Card A6 amendment |
| P-5 | "Trivial adapter-side: `/retry` re-enters the last inbound text" | `/retry` is a gateway command (`hermes_cli/commands.py:100`), dispatched for this platform like any other | Card A9 (dropped) |
| P-6 | "`/status` needs an adapter-built answer" | `/status` is a gateway command (`hermes_cli/commands.py:170`, handler `gateway/slash_commands_status.py:242`); an adapter-built duplicate would diverge from it | Card A9 (dropped) |
| P-7 | "Per-session detail flag on the step pusher" | The flag is the gateway's `/verbose` (`hermes_cli/commands.py:208`) over `display.platforms.<platform>.tool_progress` (`gateway/run_turn.py:872`); the adapter holds no state | Card A6 amendment |
| P-8 | "Verify the gateway's clarify plumbing ... adapter-side callback if absent" | Verified present for a non-overriding adapter: base `send_clarify` emits numbered text and arms text capture (`gateway/platforms/base.py:2830-2855`), and the turn runner drives that path explicitly (`gateway/run_turn_runner_clarify_delivery.py:29-39`) | Card A10 amendment |
| P-13 | "Add adapter-side audit entries (command, peer hash, ts) ... cheap" | Dropped 2026-11-04: the router admits an unrecognised slash command as a turn (`gateway/run_inbound.py:1155-1177`), so nothing vanishes; classifying "is this a command" in the adapter would duplicate gateway logic; the security-relevant events (ACL denials, desyncs) are already logged | Card A9 (dropped) |

The general lesson, worth carrying into the remaining cards: a `/command` in the old parity table
is not evidence that the adapter must implement it. Dispatch is gateway-side and every
non-`cli_only` command reaches this platform for free; check `hermes_cli/commands.py` and the
handler before carding adapter work.

### 19.3 Commands the adapter inherits for free

The gateway's full non-`cli_only` command set now applies to mesh peers automatically,
because dispatch is gateway-side (`is_command()` + `allow_gateway_control`): `/sessions`,
`/resume`, `/branch`, `/compress`, `/undo`, `/queue`, `/background`, `/agents`, `/title`,
`/save`, `/personality`, `/approvals`, `/reasoning`, `/fast`, `/voice`, `/insights`, and the
rest of the registry. These were Tier-2 "open" backlog items for the bridge; under the adapter
model they need no per-command port work. They must be **tested from the phone**, not assumed
(§17.10's lesson: the chain works when the ACL and the leading `/` are right), and any that
degrade over LXMF (button pickers, multi-page menus) get a plain-text adapter shim only when
a peer actually uses them.

`/whoami` stays deferred: the allowlist lives in `allowed_users_env`; introspection of it is
a gateway-side nicety, not adapter work.

### 19.4 Parity decision requested

> **Ruled 2026-09-30 (peer).** The order below stands, with P-1/P-7 confirmed as decided
> builds (§19.2 rows, §21.3) and **cron-over-RNS added as its own parity item**, noted in
> §21.3 item 8: a third-party LXMF bot (not built by us) is the delivery mechanism to
> explore; no lane design this round.

Build order for the gaps, by value to a phone user: **P-1 + P-7** (tool visibility with the
existing emoji registry) first, since a 22-API-call turn currently produces total silence on
the mesh; then **P-5, P-6, P-13** (cheap adapter-side quality); then **P-8** (clarify), which
may need no code at all after verification; P-2/P-3/P-4 on request; P-9 when the gateway unit
gains a watchdog slot. The roadmap file gets a "model: adapter" banner so its bridge-era
checkmarks are not mistaken for adapter reality.

## 20. Command feedback to the peer (2026-09-30): the request, and what the spec decides

The request is a ruling, not a question (the peer stated it as a requirement: commands must
give visual verification like CLI/Telegram).

**The request (peer, verbatim):** "if I do /new or /resume or /status, etc, I want a visual
verification back like the hermes cli or telegram chat." Note this phrasing joins two things
the gateway provides to Telegram and the phone user expects on the mesh: (a) every slash
command returns a visible result (Telegram gets a "Sending command '<name>' response (N chars)"
delivery log per `gateway/platforms/base.py`), and (b) that result is delivered through the
adapter's `send()`.

**What the gateway does with command results, with source evidence:**

- Slash dispatch always returns a value. `_hm_dispatch_idle_commands` (`gateway/run_inbound.py`)
  calls the handler table; the handler functions all return `(True, str)` (handled — reply),
  `(True, "ack")` (handled — feedback), or `(False, None)` (not handled — fall through to the
  agent). The returned string becomes the platform reply and is sent via the adapter's
  `send(chat_id, content)`.
- Acks like "Learning a skill from what you described…" go through `_send_command_ack`
  (`gateway/run_busy.py:896`): `adapter.send(str(source.chat_id), text, ...)`, wrapped in
  `try/except` and logging `"%s ack send failed"` on failure. So the ack is **best-effort
  and silent on failure** — an adapter that returns `SendResult.failed` or raises gets the
  error swallowed with a debug log, and the peer sees nothing.
- The normal-command responses (what `/resume`, `/status`, `/model` return) are the *return
  value* of the handler, and the gateway delivers them like any reply. These are NOT
  best-effort; they ride the same delivery path as a normal agent reply.

**The discovered gap — destructive commands sit behind a button prompt the mesh cannot
render.** `/new` (and `/reset`, `/undo`) go through `_hm_confirm_destructive`
(`gateway/run_inbound.py`) → `_maybe_confirm_destructive_slash`
(`gateway/run_busy.py:1254`). When `approvals.destructive_slash_confirm` is true (default),
that path does NOT return the handler's string immediately. It calls `_request_slash_confirm`,
which produces an interactive three-option prompt ("Confirm /new — Approve Once / Always
Approve / Cancel").

**Correction from live source (2026-09-30, second pass): the text fallback already works — the
gap is UX, not mechanism.** `_request_slash_confirm` (`gateway/run_busy.py:1339`) calls
`adapter.send_slash_confirm(...)`. The Reticulum adapter does **not** override
`send_slash_confirm`, so it inherits the base default (`gateway/platforms/base.py:2782`), which
returns `SendResult(success=False, error="Not supported")`. In `_request_slash_confirm`, a
falsy/unsuccessful button result skips straight to `return message` — the prompt message itself
is the direct reply. So on the mesh, `/new` already sends the confirm prompt as a **plain text
message** (not garbled buttons) with `_p` = `/` (Reticulum has no `typed_command_prefix`
override, so `_typed_command_prefix_for` returns the default `/`).

The peer then resolves it by replying any natural word: `_hm_slash_confirm_reply`
(`gateway/run_inbound.py:454`) normalizes inbound text (strips leading `!/`, lowercases) and
maps it through `_SLASH_CONFIRM_TEXT_CHOICES`:
- **once** for `approve`, `approve once`, `once` (plus command spellings `yes`, `ok`,
  `confirm`)
- **always** for `always`, `always approve` (plus `remember`, and `always` as command)
- **cancel** for `cancel`, `nevermind`, `no` (plus `/deny` as command)

So `/new` → peer sees the confirm prompt → replies `yes` → reset runs → handler result text is
returned and sent. The interaction is functional end to end on plain text. What the peer is
missing is not the confirm — it is a *clear, terse* prompt and a *satisfying result
confirmation* (both things the CLI and Telegram's button tap + toast give, and the raw
Markdown prompt does not). Section 20.3 specs that rendering.

Consequence: on Telegram, `/new` shows branded buttons and a result toast; on the mesh it shows
a raw Markdown menu the peer may not read as actionable, and the "session reset — done" toast
is missing. The request "I want a visual verification back" is satisfied by (a) rendering the
prompt as a terse single-line question and (b) confirming the outcome, not by changing the
confirm gate.

**The `send_clarify` sibling.** The same base class provides `send_clarify`
(`gateway/platforms/base.py:2792`) for the `/clear`-style follow-up. Its default already emits
a numbered plain-text list plus "Reply with the number, the option text, or your own answer"
and calls `mark_awaiting_text` so the next typed message resolves the wait. The Reticulum
adapter inherits that working default too (it does not override `send_clarify`). The same terse-
render here (§20.3 item 2) applies to clarify prompts so they read like CLI choices, not API
menus.

**Ruling — the mesh must get visible command results, with these specifics:**

1. **No change needed to the gateway core for the non-destructive commands.** `/resume`,
   `/status`, `/model`, `/sessions`, `/help` and every command whose handler returns a
   string already deliver it through `adapter.send()` to the peer hash. If the peer is not
   seeing these, that is a *delivery bug in the adapter's `send()`* and must be reproduced
   and fixed there, not patched around with a new surface. First verification step: send
   `/status` from the phone and confirm the reply packet reaches the peer (the gateway log
   line "response ready: platform=reticulum … response=N chars" tells us `send()` was called,
   not that LXMF delivered it).
2. **Destructive commands already degrade to a working text confirmation — do not disable
   it.** Because the adapter does not override `send_slash_confirm`, the gateway's built-in
   fallback sends the confirm prompt as plain text and the peer's natural-language reply
   (`yes`/`always`/`cancel`/`no`/`ok`) resolves it (corrected from the earlier draft's
   recommendation to set `approvals.destructive_slash_confirm: false`; that would skip the
   guard entirely and lose the accidental-`/new` protection for no gain, since the text path
   already works). The remaining work is presentation — §20.3.
3. **The Reticulum adapter should assert the ack path is live, not silent.** `_send_command_ack`
   swallows failures (debug log only). The adapter's `send()` should return a
   `SendResult.failed` (not silently succeed) when LXMF delivery fails, and the adapter's own
   delivery tracker (section 6) should log at WARNING when an ack cannot reach the peer, so a
   "command feedback lost" incident is diagnosable instead of silent.

### 20.3 Plain-text rendering for mesh-facing interactive commands

The gateway hands the adapter two interactive surfaces (confirm + clarify), both of which have
working plain-text fallbacks that the Reticulum adapter inherits by not overriding them. The
rest is making those surfaces read like CLI output, not API menus. Rules:

- **Override nothing in the gateway core.** The adapter should ride the base
  `send_slash_confirm` default (returns "Not supported" → the gateway fallback returns the
  prompt as the direct reply) and the base `send_clarify` default (numbered list +
  `mark_awaiting_text`). This keeps the interaction on the gateway's battle-tested
  `_hm_slash_confirm_reply` / `mark_awaiting_text` resolution paths instead of a second,
  adapter-private one.
- **Terse single-line prompt.** The adapter's `send()` should render the confirm/clarify
  message body through a `mesh_terse()` helper: collapse the three Markdown button labels to
  one line — e.g. `Confirm /new? reply: yes / always / cancel` — and drop the "Choose:"
  bullet block and the "Text fallback:" footer (they describe buttons the mesh does not
  render). Keep the detail line (the destructive-action warning) verbatim; that is the part
  the user must read. Drop no meaning: the three accepted replies map to the gateway's
  `_SLASH_CONFIRM_TEXT_CHOICES` (`yes`→once, `always`, `cancel`), so the shortened prompt is
  exactly the interaction contract.
- **Result confirmation is the other half.** After `_hm_slash_confirm_reply` resolves and
  `_handle_reset_command` returns its result string, the gateway sends that string back
  through `send()` — so `/new`'s "🟡 /new cancelled. Conversation unchanged." or the
  reset-ack already reaches the peer. Do not add a second ack; but ensure the adapter's
  `send()` surfaces it (rule 3) so it is not swallowed.
- **`send_clarify` gets the same terse render.** Numbered choices survive (they are the reply
  vocabulary: "1", "2", or the option text); strip the "Reply with the number, the option
  text, or your own answer" hint to a single `reply: <number> or your own words`.
- **Explicitly out of scope:** building real button cards over LXMF, and disabling
  `destructive_slash_confirm`. Both are anti-goals (the former has no transport primitive, the
  latter removes protection with no benefit once the text path is confirmed working).

**Status:** DRAFT decision O-8 (below). The mechanism is now verified end to end from source
(`/new` → plain-text confirm → natural-language reply → resolution → result send). The
deliverable is `mesh_terse()` (adapter-side text transform for confirm/clarify bodies), the
`send()` failure-return + delivery-tracker WARNING for ack visibility, and a phone-exercise
check that `/resume`, `/status`, and the `/new` confirm→`yes` cycle each return visible text.

## 21. Session management and Telegram parity (peer rulings, 2026-09-30)

Peer rulings from the 2026-09-30 session-mapping review. Full findings with source references
live in `docs/findings-2026-09-30-session-management-and-telegram-parity.md`; this section
records only the decided requirements. Quoted text is the peer's. Planning round: no code was
changed writing this section; the code deltas listed under each item are deferred to the
implementation pass.

### 21.1 How sessions work under the adapter model (verified, with sources)

- The adapter owns no sessions; the gateway owns them entirely (§1, §7). Session identity is
  the gateway's `build_session_key` (`gateway/session.py:664`), laid out as
  `<ns>:<platform>:<chat_type>[:scope_id][:chat_id][:thread_id][:user]`.
- The adapter's `build_source()` (`plugin/adapter.py:386`) sets `chat_id=<destination hash>`,
  `chat_type="dm"`, `user_id=<source hash>`, so the lane key is
  `agent:main:reticulum:dm:<destination-hash>`, the same shape as Telegram's
  `agent:main:telegram:dm:<chat_id>`. One durable lane per destination hash.
- Session rows carry ids (`<date>_<time>_<suffix>` style) and an optional title. Title
  provenance (`hermes_state_titles.py`): user via `/title` > LLM auto-generated > derived; an
  auto-title never overwrites a user-set title. Auto-titling runs through
  `agent/title_generator.py`, fired at turn start by `agent/turn_context.py` for every
  platform except cron and subagent sessions (`_UNTITLED_PLATFORMS`).
- `/new` ends the row with `end_reason="session_reset"` and promotes a new session id under
  the same lane key (`session_recovery.py`); keys never rotate (§18.2).
- Resume works by id or exact title (`get_session_by_title`); `/resume` dispatch is
  gateway-side. Browsing a session list is a CLI/gateway surface; the mesh has no picker.
- Identity rotation by a peer changes its hash, changes the lane key, and loses the
  conversation; no migration exists (§4, §8). Accepted limitation.

### 21.2 Telegram-vs-mesh deltas (structural)

1. Lane freshness presentation only: the reset machinery is identical on both platforms;
   Telegram *looks* fresher because chat_id-keyed lanes plus platform presentation (§18.2).
2. Titles exist on both; Telegram surfaces them in browse UI, the mesh gets `/title` and
   resume-by-title.
3. No streaming, no message editing or deletion on the mesh (no transport primitives;
   `SUPPORTS_MESSAGE_EDITING=False`, §17.2).

### 21.3 Peer decisions (requirements)

1. **Destination addressing.** "The user should be able to message the agent by its
   destination hash." The agent's own destination is the address peers target (already the
   §4 mapping; now an explicit requirement).
2. **Auto-titles on the mesh.** Peer ruling: "the title gen should be auto." Mesh lanes get
   the LLM-generated session titles Telegram sessions get (`agent/title_generator.py`; the
   platform is not in `_UNTITLED_PLATFORMS`, so the mechanism applies). Titles must be
   surfaced to the peer where they matter: the `/new` ack (item 4) and command results
   (§20). `/title` remains the user-authority override.
3. **Automated rejection of unauthorized destinations.** Peer ruling: "rejected should be an
   automated rejection like the original rns bridge did... if they're not authorized, don't
   leave it to the model to try to figure it out and get prompt injected." The adapter
   replies to an out-of-allowlist inbound with a canned rejection string, the way the bridge
   did (`core/acl.py` deny → `cli.py` `handle_message` returning `⛔ Access not authorized.`,
   Hermes never reached). The gate is deterministic code before the model sees anything:
   allowlist check in the adapter (already present), rejection reply from the adapter, and no
   model involvement in the authorization decision at all. Supersedes §17.8's silent drop.
   **Security invariant:** an unauthorized peer's text must never enter a prompt, a system
   note, or a model-visible log lane; only the destination hash is logged.
4. **`/new` produces a discoverable new session.** Peer ruling: "all of the agent's previous
   memories wiped and a new session ID or name so that you can go back to it." The gateway
   already ends the row and promotes a fresh id under the same key; the adapter must surface
   the new session's id and title in the `/new` result ack (decides O-7, §15; §18.2's "no key
   churn" ruling stands).
   *Corrected 2026-11-04:* the ack is gateway-side, so the adapter surfaces nothing in it. The
   gateway's ack carries the title but not the id; the id is available on demand from `/status`.
   Card A5 dropped — if the id must appear in the ack itself, that is a gateway card.
5. **Tool-call visibility, CLI-like verbosity.** P-1 (step pusher) + P-7 (`/verbose`) are
   decided builds (§19.2). Tool emojis come from the shared `agent/tool_emoji.py` registry,
   identical to Telegram/CLI. `/verbose` defaults **on**, like the CLI default; off is the
   truncated recap form, like the CLI's truncation ("verbose on, should be like the cli
   default, otherwise truncated like the cli does").
   *Corrected 2026-11-04 (peer):* the step pusher is deleted — the gateway's progress lane is the
   pusher. Progress defaults **off** (`display_tier: "low"`), not on: a packet per tool call is
   airtime on LoRa, so the peer opts in with `/verbose`. See card A6's amendment.
6. **Command feedback.** Slash commands get visible results back like CLI/Telegram (spec'd in
   §20; the peer's list confirms §20's ruling).
7. **No streaming / no edits or deletions** on the mesh (RNS limits; already invariants).
8. **Cron over RNS.** Peer ruling: cron deliveries run in a new session carrying no context
   from the scheduling session, just the text, and must never absorb the peer's ongoing
   conversation. Gateway behaviour already matches (cron sessions live in their own lanes;
   `platform: "cron"` is excluded from auto-titling). **Delivery mechanism note:** the peer
   pointed at an existing community LXMF bot as the delivery vehicle ("there's an lxmf bot
   that someone made that we could have deliver to the user"). The Reticulum ecosystem lists
   `randogoth/lxmf-bot` (Telethon-style chatbot class) and `Quad4-Software/LXMFy` (bot
   framework). Note only: which bot, and whether deliveries ride the agent's main
   destination or a dedicated delivery destination, stay open; no lane design this round.
   Interacts with §15's `standalone_sender_fn` question (a cron process can attach to the
   running shared RNS instance if it is up).

### 21.4 Inference audit

The findings doc (§4 there) lists the four items that had been assistant inference on
2026-09-30 and the peer's ruling on each. Two survive as decided requirements (items 2, 4
above), two were already peer-designed (§4 identity map, §19.3 framing pending phone test).
Nothing in this section is assistant-inferred design: each item is a quoted peer ruling or a
source-verified observation.
