# Session management and Telegram parity on the mesh (2026-09-30)

Findings from the 2026-09-30 session-mapping review. All claims below carry source
references (gateway source read live at `~/.hermes/hermes-agent`, adapter source at
`src/hermes_reticulum/`). Decisions are the peer's, recorded verbatim where quoted.

## 1. How sessions are managed under the adapter model

- The adapter manages no sessions. It deletes the bridge's session layer entirely
  (adapter spec §1, §7: "Session adoption heuristics | Deleted. The gateway owns sessions.").
- Session identity comes from the gateway's `build_session_key`
  (`gateway/session.py:664`), laid out as
  `<ns>:<platform>:<chat_type>[:scope_id][:chat_id][:thread_id][:user]`.
  The Reticulum adapter's `build_source()` (`plugin/adapter.py:386`) sets
  `chat_id = <destination hash that received the message>`, `chat_type="dm"`,
  `user_id = <source hash>`, so the key is
  `agent:main:reticulum:dm:<destination-hash>`. One durable lane per destination hash,
  same shape as Telegram's `agent:main:telegram:dm:<chat_id>`.
- Session *rows* carry ids (`<date>_<time>_<suffix>` style) and an optional title.
  Title provenance lives in `hermes_state_titles.py`: three sources with a rank order
  (user via `/title` > LLM auto-generated > derived); auto-titles never overwrite a
  user-set title (`set_auto_title` returns False when a higher-authority title holds
  the row). Auto-titling runs through `agent/title_generator.py` (auxiliary side-LLM task).
- `/new` ends the row with `end_reason="session_reset"` and promotes a new session id
  under the same key (`session_recovery.py::_promote_session_reset`). The key never
  rotates; reset truncates history inside the lane (spec §18.2 ruling, unchanged).
- Resume works by id or exact title (`get_session_by_title`, `hermes_state_titles.py`);
  `/resume` dispatch is gateway-side, so a mesh peer with a titled session can name it.
  Browsing the session list is a CLI/gateway-surface action; the mesh has no picker.
- Key rotation hazard (unchanged, §4/§8): a peer that regenerates its identity changes
  its hash, the lane key changes, and the gateway has no chat-id migration. Accepted limitation.

## 2. Where Telegram and Reticulum actually differ (structural, not cosmetic)

1. Lane freshness presentation. Telegram's chat_id-keyed lanes plus platform presentation
   make a post-`/new` session feel new. Reticulum's lane name never moves. Mechanism is
   identical on both platforms (same reset machinery); only presentation differs (§18.2).
2. Titles exist on both, but Telegram surfaces them in `/sessions` browse UI; the mesh
   gets `/title` (set/show) and resume-by-title, not browse.
3. Telegram auto-titles sessions via the LLM title generator on gateway surfaces.
   Whether the mesh lane gets an auto-title is an open question (not an adapter code
   path; the generator is shared agent infrastructure). Recorded as open, not assumed.
4. No streaming, no message editing/deletion on the mesh (transport has no primitives).
   Already adapter reality (`SUPPORTS_MESSAGE_EDITING=False`, §17.2).

## 3. Peer decisions (2026-09-30, recorded as requirements)

The peer stated these as what the spec must reflect. Quoted points are the peer's.

- **Destination addressing:** "The user should be able to message the agent by its
  destination hash." The agent's own destination is the address the peer targets.
- **Source verification:** "The user's destination hash should be verified and any other
  destination hash should be rejected." Inbound from a hash outside the allowlist is
  rejected, not silently dropped. (Current code drops silently, §17.8; this decision
  upgrades silent drop to an explicit rejection with a logged reason.)
- **Command feedback:** slash commands get visual confirmation back the same way the
  Telegram gateway and CLI do (already spec'd as §20; the peer's list confirms §20's
  ruling: terse plain-text confirm + result ack, no button cards).
- **Tool-call visibility with a verbosity toggle:** tool calls either arrive live as they
  happen (like Telegram/CLI) or as a brief recap/compressed version, with the detailed
  verbose form toggling like the CLI's `/verbose`. Maps onto §19.2: P-1 (step pusher) +
  P-7 (`/verbose` per-session flag). The toggle must behave like the CLI's toggle.
- **`/new` creates a whole new session:** "all of the agent's previous memories wiped and
  a new session ID or name so that you can go back to it and find the session that you've
  resumed or started." Meaning: the old lane's history is not carried into the new
  session; the new session has a discoverable id/title so it can be resumed later.
  (Gateway behaviour already matches: fresh row under the lane key, old row ended and
  exportable; the discoverability half depends on titles, see §3 open items.)
- **Tool emojis match Telegram/CLI:** the emoji that names each tool must be the same
  registry the Telegram gateway and CLI use, so the peer can tell which tool ran.
  (`agent/tool_emoji.py` is the shared registry; §19.2 P-1 ports it unchanged.)
- **No text streaming on the mesh** (RNS doesn't support it) and **no message
  deletions/edits** (same reason). Already adapter invariants.
- **Cron over RNS:** cron jobs can be scheduled and delivered via RNS **in a new
  session**, carrying no context from the session that scheduled them, "just the text
  like the Telegram gateway doesn[t carry context]." If a cron fires mid-conversation,
  the user's next messages go to the session they were in, never to the cron session.
  (Gateway behaviour: cron deliveries live in their own session lanes for exactly this
  role-alternation reason, `agent/AGENTS.md`; the adapter must not route cron replies
  back into the peer's DM lane.)

## 4. What was inference, and what is now decided (audit trail)

The peer's rule: no spec content is authored from inference without peer sign-off.
Four items were flagged as assistant inference on 2026-09-30; the peer's rulings:

| # | Inferred item | Peer ruling |
|---|---|---|
| 1 | §4 identity map (chat_name = adapter identity map; no thread_id; user_id = source hash, Signal-style) | Confirmed as the peer's design (was already §4). Stays. |
| 2 | §18.2 O-7 synthetic reset ack as the presentation fix | Superseded by §3 decision: `/new` produces a new discoverable session id/name. O-7 re-framed: the marker is the new session's id/title surfaced in the reset ack, not a synthetic "session reset" toast. |
| 3 | §19.3 "commands inherited for free" (`/title` works, `/sessions` browse gap) | Confirmed as framing, pending phone test (unchanged; §19.3 already says test from the phone). |
| 4 | Resume-by-title-over-mesh works | Confirmed as a requirement (§3 `/new` point: the new session must be findable/resumable). Mechanism: titles via `/title` (and auto-titles if the generator fires for this platform, open item). | **Resolved 2026-09-30:** auto-titles ruled on. The title generator is shared infrastructure fired at turn start by `agent/turn_context.py` for every platform except cron/subagent (`_UNTITLED_PLATFORMS`), so mesh lanes are in scope by design. |

Working rule adopted for all future spec work: design proposals are presented to the
peer for ruling BEFORE landing in the spec; the spec only records decided rulings,
observed behaviour with source evidence, or open questions explicitly labelled as such.

## 5. Spec edits (applied 2026-09-30, planning round: spec only, no code)

- New §21 (session management and Telegram parity): §1-§2 findings above, §3 decisions
  as requirements, §4 audit trail. Written.
- §15 O-7: re-framed per decision 2 (reset ack names the new session id/title). Written.
- §17.8 (silent ACL drop): ruled to an explicit automated rejection (item 3 below).
  Spec amendment written; the code delta (rejection reply in `adapter.py handle_message`)
  is deferred to the implementation pass.
- §19.2 P-1/P-7: status changed to "decided 2026-09-30: build" with the CLI-like
  `/verbose` toggle (default on). Written.
- §19.4 build order: P-1+P-7 remain first; cron-over-RNS added as its own parity item
  with the LXMF-bot delivery note. Written.

## 6. Peer rulings (2026-09-30, all four open items answered)

1. Cron delivery lane: "there's an lxmf bot that someone made that we could have deliver
   to the user, but let's just note that." Noted as the delivery mechanism to explore
   (community candidates: `randogoth/lxmf-bot`, `Quad4-Software/LXMFy`). Lane design
   (main destination vs dedicated) stays open; no design this round.
2. Auto-titles on the mesh: "the title gen should be auto." Ruled: LLM auto-titles on,
   like Telegram sessions.
3. Rejection shape for unverified destinations: "rejected should be an automated rejection
   like the original rns bridge did. If they're not authorized, don't leave it to the model
   to try to figure it out and get prompt injected." Ruled: automated canned rejection from
   code, deterministic gate before the model, no model judgment in authorization.
4. `/verbose` default: "on, should be like the cli default, otherwise truncated like the
   cli does." Ruled: default on; off = CLI-style truncated recap.
