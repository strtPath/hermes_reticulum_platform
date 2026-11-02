<!-- Part of the adapter feature bundle: docs/specs/adapter/.
     Each card is one branch and one PR into `adapter`. Working sections live in plan.md.
-->

# Adapter implementation steps (split 2026-09-30)

The design spec (`docs/specs/adapter/spec.md`, §1-17) and the working decisions
that grew on top of it are split on purpose: the spec says *what the adapter is*, this file
says *what we build next*. Sections §18-21 of the old spec moved to `plan.md` intact (their text
governs; only their numbering changed into cards). Each card is one branch and one PR into
the `adapter` branch, so the review bot reviews one vertical change at a time.

Card anatomy: **Build** (one sentence), **Spec basis** (which design invariant it implements),
**Blocked by** (cards that must merge first), **Acceptance** (what proves it works; each item
is a Given/When/Then), **Status** (open / in-review / merged).

Peer rule that governs all cards: no spec content enters a card without the peer approving
it ("I want you to run everything by me"). A card whose acceptance depends on an open
decision (O-8, delivery bot choice) stays blocked, not guessed.

## Step index

| Card | Build | Blocked by | Acceptance (headline) | Status |
|---|---|---|---|---|
| A1 | Docs foundation: spec split, step cards, PII quarantine rule, findings docs, the registration `_bool_conv` fix | - | public sweep is clean; spec + cards reviewable | done |
| A2 | Automatic per-conversation context discovery; silence the dead step POST | A1 | resolved window == live n_ctx, no pin; log noise stops | closed (PR #14, merged 2026-10-12) |
| A3 | Automated ACL rejection (code gate, canned reply, zero model involvement) | A1 | unauthorized peer gets `⛔ Access not authorized.` and nothing else | closed (PR #16, merged 2026-10-24) |
| A4 | Command feedback verification + terse render + send-failure truth | A1 | `/status`, `/resume`, `/new`-confirm all visibly answered on the phone | open |
| A5 | `/new` ack names new session id + auto-title | A4 | peer can find the new session after `/new` | open |
| A6 | Tool-call visibility (step pusher) + `/verbose` (default on) | A4 | a 22-tool turn is no longer silent; `/verbose off` truncates like the CLI | open |
| A7 | YAML config hook (`apply_yaml_config_fn`), display name + announce interval as config keys | A1 | `config.yaml` keys work without env vars | open |
| A8 | Ship `approvals.timeout: 900` with LoRa rationale | A1 | mesh approvals survive LoRa latency | done |
| A9 | P-5 `/retry`, P-6 adapter-built `/status`, P-13 command audit | A4 | commands listed in §19.2 respond on the mesh | open |
| A10 | P-8 `/clarify` verification, adapter-side plumbing only if gateway lacks it | A4 | `/clarify` resolves or a findings doc proves the gateway gap | open |
| A11 | Per-profile mesh identity + ACL profile-scoping: one shared parser; ACL + identity read profile-scoped via `platform_gate_env` | A3 | two profiles on one box carry different identities + trust lists; no raw `os.getenv` in the A3 read path | closed (guard PR #21; isolation proven live 2026-10-28: `build_profile_secret_scope` on both homes returns distinct trust lists, both fail-closed — see `docs/mesh-bridge-findings-2026-10-28-a11-profile-isolation.md`) |
| A12 | Stop false `⛔ Access not authorized.` after a busy-session inline command: the base leaves `_gateway_accepted` False on the inline-dispatch paths, so the adapter's D7 deny signal fires for an allowlisted peer | A3 | `/stop`, `/new` and a clarify answer mid-turn get their own ack and no rejection; a genuine gateway deny still logs a desync line and sends the canned reply | proposed — awaiting peer ruling | 

## Cross-cutting constraint: no interactive UI over LXMF

LXMF carries text only — no buttons, no inline keyboards, no interaction prompts.
Anything that asks the peer to pick from options must be spec'd as a text exchange:
the adapter sends a numbered/plain-text prompt, the peer replies in text, a follow-up
message resolves it. Verified live 2026-10-28: `/new` needed `approvals.timeout` /
autoapprove to start a new conversation, and interactive approval/clarify flows cannot
render on the mesh. A4/A6/A10 must be designed against this (A10 in particular: a
`/clarify` prompt that waits for an unrenderable button is a stuck turn).

## Where the moved sections live

Sections 18-21 of the old spec moved to `plan.md` (this bundle), under their own headings:
**18 (context overflow)**, **19 (feature parity delta)**, **20 (command feedback)**,
**21 (session rulings)**. Cards cite them directly. The cards are the build units; the moved
sections remain the governing text they were written from.

Open decisions O-7 and O-8 stay where they were, in the design spec's §15 open-decision list
(`docs/specs/adapter/spec.md`); card A5 decides O-7 and card A4 closes O-8.

## Un-carded by peer decision

Parked, not forgotten: cron-over-RNS delivery vehicle (community LXMF bot note, §21.3 item 8
in `plan.md`), `standalone_sender_fn` design question, P-2 map,
P-3 media, P-4 NomadNet channel, P-9 gateway watchdog, RNS version pin (§14).

**Owner request 2026-10-28 — spec pass next:** inbound image support (P-14 media,
image-in half): a client attaches an image to a mesh DM; the adapter carries it
(base64 in-band vs the standing RNS `Resource` + LXMF hash-pointer design, §19.2),
decodes on inbound, and hands it to the model's vision path. The inbound path is
currently text-only — no image/media handling in `adapter.py`/`transport.py`.
Companion item: identity display-name resolution — `IdentityMap` (spec §4) exists
but is unseeded (`IdentityMap()` built empty; `set_name` has no live call path),
so peers surface as raw 32-hex hashes only. Spec either an `.env` seed
(hash→name) or announce-callback name learning, so a multi-identity ACL is
humanly distinguishable (each identity is already its own `chat_id`/session —
only the names are missing).

## Bridge archival (2026-10-28)

The standalone bridge moved to `src/hermes_reticulum/bridge/` and is archived:
kept for existing deployments, no new features. `core/` now holds only the shared
`acl.py` and `tool_emoji.py`; the adapter imports `acl.py` from there. The bridge
CLI entry point moved to `hermes_reticulum.bridge.cli:main`, and
`scripts/check_imports.py` excludes the new paths. The plugin in `plugin/` is the
direction of travel. The repository `.env` belongs to the bridge; the plugin reads
the Hermes profile's `.env`/`config.yaml`. See `src/hermes_reticulum/bridge/README.md`.

## Card A1: Docs foundation

**Build:** Split the spec (design §1-17 stays; working sections become these cards), add the
PII quarantine rule in this card, land the findings docs and roadmap banner, and the one registration
boolean fix.
**Spec basis:** §16 invariants; the findings doc `docs/findings-2026-09-30-session-management-
and-telegram-parity.md`.
**Blocked by:** nothing.
**Acceptance:**
- Given every tracked and untracked visible file, When the pre-push sweep runs (hash-shaped
  strings, host:port endpoints, numeric account/session ids, `-----BEGIN` headers), Then zero
  non-synthetic hits remain.
- Given a reader new to the repo, When they read spec §1-17 then this file, Then every open
  decision and every planned build is findable without reading 1,100 lines of history.

### The PII quarantine rule (card A1's security content)

Recurring failure mode: docs written from live log excerpts or live command output carry real
peer destination hashes, real session ids, and real account ids into files that outlive the
session. Two live leaks were found and redacted on 2026-09-30 (a numeric chat id quoted from a
gateway log line; a session id quoted as an example), and one remains in committed §19.2
(a hook docstring quoting a real peer hash in a "do not copy verbatim" example, now marked
synthetic).

Rules, all enforced *before writing*, not during the push sweep:

1. **Docs never quote raw identity values.** Identity values (32-hex destination hashes,
   display names, session row ids, account/chat ids) get a label at the point of writing:
   `<peer-A>`, `<session-id>`, `<chat_id>`. The mapping lives nowhere in the repo.
2. **Log excerpts are redacted on paste**, the same rule the gateway's own sanitizer applies
   to outbound text (destination hashes scrubbed at the display boundary, `redact.py`
   `_IDENT_PATTERNS`), extended to session ids and numeric account ids.
3. **Session ids count as fingerprint material.** A session id is 8 random hex characters;
   8-hex strings are fingerprintable and fall under the same 32-hex quarantine.
4. **Pre-push gate stays and grows**: the repo has no committed sweep script yet (the
   2026-09-15 quarantine ran ad-hoc). Card A1 adds a tracked `scripts/security-sweep.py`
   that walks every tracked and untracked visible file for: 32-hex identity values, other
   hash-shaped runs (8+ hex), host:port endpoints, `-----BEGIN` headers, and 9-11 digit
   numeric ids, with a synthetic-allowlist (`aabbccdd`, `deadbeef`, `0badc0de`,
   `abcd1234`, zero-filled placeholders). Non-synthetic hits exit non-zero, so no push
   happens until they are redacted. Plus a **pre-write habit**: when a doc quotes a log line
   or command output, sanitize first, then paste.
5. **Open decision (peer):** whether identity values also get scrubbed from *gateway-visible
   log lines* at the log boundary (`--redact-pii` exists but is opt-in), so docs quoted from
   logs are clean by construction. This touches the logging story and awaits the peer's
   ruling before it becomes a card.

## Card A2: Automatic context discovery + step-POST noise

**Build:** Leave `model.context_length` unset; the gateway's automatic discovery chain
(custom-endpoint `/models` probe, llama.cpp `/props` local probe, `model@base_url` disk
cache, live re-probe on cached entries) is the design — verified working on the live
endpoint (2026-10-08: installed resolver returned the live `n_ctx` exactly). Add a small
adapter-side verification: compare the resolved window against the endpoint-reported
`n_ctx` and log one warning line on disagreement, in a pure module. Operator ruling
2026-10-08: the adapter must find the context automatically per conversation, not rely on
a manual pin. Separately: stop the `mesh-tool-events` *hook*'s step POSTs to the deleted
control server by gating both `_post` call sites on a single "control server configured?"
predicate (`HERMES_MESH_CONTROL_URL` unset/empty/invalid ⇒ no-op). Do NOT touch the
`mesh-tool-gate` pre-exec plugin — different component (see `a2-spec.md` §4 sibling
invariants).
**Spec basis:** `a2-spec.md` v2 (root causes: silent probe-down to the 256K fallback; dead
POST flood); invariant: the adapter never asserts a context window it has not verified
against the serving endpoint — verification, not a number, is the adapter's job.
**Blocked by:** A1 (config docs land first).
**Acceptance:**
- Given `model.context_length` unset and a live llama.cpp endpoint, When a conversation's
  turn resolves its window, Then the resolved value equals the endpoint's live `n_ctx`
  (`/props` and `/v1/models` `meta.n_ctx`), and a turn sized near the window compresses
  instead of truncating.
- Given a stub endpoint reporting `n_ctx` far below the resolved window, When the adapter
  verification runs, Then exactly one disagreement warning appears (red-before: today no
  comparison exists, so none appears).
- Given the adapter model running (no bridge control server), When a guarded turn executes,
  Then zero `Connection refused` lines appear from step pushes.

## Card A3: Automated ACL rejection

**Build:** Inbound from a peer outside the allowlist gets the canned automated rejection
(`⛔ Access not authorized.`, the original bridge's `core/acl.py` + `cli.py` behaviour),
returned by adapter code before any model involvement.
**Spec basis:** peer ruling 2026-09-30 (§21.3 item 3 in `plan.md`): "if they're not
authorized, don't leave it to the model to try to figure it out and get prompt injected."
Security invariant: an unauthorized peer's text never enters a prompt, a system note, or a
model-visible log lane. Supersedes the silent-drop behaviour documented in §17.8.
**Blocked by:** A1.
**Acceptance:**
- Given a peer hash absent from the allowlist, When it sends any text, Then it receives
  exactly the canned rejection and no Hermes turn runs (gateway logs show no session touch).
- Given the rejection path, When its logs are swept, Then the unauthorized peer's message text
  appears nowhere in gateway-visible output.
- Open sub-question for the peer (carried from §15): the rejection is a liveness oracle by
  design (a stranger learns a destination exists). Peer ruling accepts this trade for UX
  clarity; recorded, not re-litigated.

## Card A4: Command feedback + terse render + send truth

**Build:** Phone-exercise `/status`, `/resume`, and the `/new` confirm→`yes` cycle; add
`mesh_terse()` for confirm/clarify bodies; make `send()` return `SendResult.failed` on LXMF
delivery failure and log WARNING from the delivery tracker.
**Spec basis:** old §20 (mechanism verified from source; gateway core untouched; ride the base
`send_slash_confirm` / `send_clarify` defaults).
**Blocked by:** A1.
**Acceptance:**
- Given the phone on the mesh, When `/status` is sent, Then a reply packet is observed on the
  peer side (not just a gateway "response ready" log line).
- Given `/new` on the mesh, When the confirm prompt arrives, Then it renders as one terse
  line (`Confirm /new? reply: yes / always / cancel`) plus the verbatim destructive-detail
  line, and a `yes` reply runs the reset.
- Given a send whose LXMF delivery fails, When the command handler returns, Then the send is
  reported failed and a WARNING names the lost delivery.

## Card A5: `/new` ack with session id + title

**Build:** `/new`'s result ack names the new session's id and auto-title.
**Spec basis:** peer ruling (§21.3 item 4 in `plan.md`): "a new session ID or name so that you
can go back to it"; decides O-7. The lane key stays stable (no key churn).
**Blocked by:** A4 (the ack path must be live first).
**Acceptance:** Given `/new` completes, When the ack arrives, Then it names the session id and
title, and `/resume` lists that session.

## Card A6: Tool visibility (P-1) + `/verbose` (P-7)

**Build:** Adapter-side step pusher consuming gateway tool events; tool emoji from the shared
`agent/tool_emoji.py` registry; `/verbose` per-session flag on the step pusher.
**Spec basis:** peer rulings: auto title gen (already gateway-side), "verbose on, should be
like the cli default, otherwise truncated like the cli does."
**Blocked by:** A4.
**Acceptance:**
- Given a turn with 22 tool calls, When it runs on the mesh with `/verbose` on (default),
  Then the peer sees tool-call progress like the CLI sees it.
- Given `/verbose off`, When a turn runs, Then recaps arrive in the truncated form.

## Card A7: YAML config hook

**Build:** Register `apply_yaml_config_fn` on the platform entry; `display_name` and
`announce_interval` become `config.yaml` keys under `platforms.reticulum`.
**Spec basis:** §12, §17.5 (env-only config is a known gap that must close before public
plugin shipping).
**Blocked by:** A1.
**Acceptance:** Given a fresh checkout with only `config.yaml` set (no env vars), When the
gateway starts, Then the adapter announces with the configured display name and cadence.
**Progress:** the transport now receives `PlatformConfig.extra` (connect() passes it), so a
setting that reaches the adapter is no longer dropped on the floor before the transport. The
`apply_yaml_config_fn` registration itself remains to be added.

## Card A8: Approvals 900s

**Build:** Ship `approvals.timeout: 900` in the plugin config with the LoRa-latency rationale.
**Spec basis:** §7 approval row, §16 invariant, §17.6 (without it, mesh approvals fail closed
at the gateway's 300s default).
**Blocked by:** A1.
**Acceptance:** Given an approval prompt on a multi-hop LoRa path, When the peer answers within
900s, Then the approval is honoured.

## Card A9: P-5 `/retry`, P-6 `/status` health block, P-13 command audit

**Build:** Adapter-side `/retry` for failed sends; adapter-built `/status` answer (RNS attach
state, announce cadence, peer pending-set depth); command audit entries.
**Spec basis:** §19.2 in `plan.md` (parity gap table); `/usage` rides the gateway-provided command.
**Blocked by:** A4.
**Acceptance:** Each command in this card's list gets a visible, correct answer on the phone
exercise.

## Card A10: P-8 `/clarify` verification

**Build:** Probe whether the gateway's `send_clarify` plumbing reaches plugin platforms; build
an adapter-side callback only if it does not.
**Spec basis:** §19.2 in `plan.md`, P-8 row ("gateway plumbing existence unverified; adapter-side callback if
absent").
**Blocked by:** A4.
**Acceptance:** Given `/clarify`-style output is possible on the mesh, When probed from source
or a live run, Then either it works or a findings doc names the gateway gap precisely.

## Card A11: Per-profile mesh identity + ACL profile-scoping

**Build:** (owner amendment 2026-10-24; full text in the main checkout at
`docs/owner-amendment-2026-10-24-acl-profile-scoping.md`.) Make the four
`HERMES_RETICULUM_*` ACL knobs reliably profile-scoped: one shared
`AccessControl.parse_allow_all` / `ALLOW_ALL_TRUTHY` (`{true,1,yes,on}`) used at
every authorization-decision point; the A3 read path goes through
`platform_gate_env` (multiplex-safe), never raw `os.getenv`; co-resident profiles can
each carry their own identity (`RETICULUM_DISPLAY_NAME`, already profile-scoped) and
their own trust list.
**Spec basis:** owner amendment 2026-10-24; spec.md:99-101 (never raw `os.getenv`);
spec.md:17.5 (config read via `get_scoped_secret`); a3-spec.md D1/I3 as amended
2026-10-24. Supersedes the earlier same-day "ACL .env-only" reading (the mesh leans
TCP; co-resident multi-profile bridges are the target topology, so a process-global
`.env` cannot own per-profile trust).
**Blocked by:** A3 (builds on the shipped gate; lands the P2-5 correction).
**Acceptance:**
- Given two Hermes profiles on one box, When each bridges different
  `HERMES_RETICULUM_ALLOWED_USERS` from its config.yaml, Then each profile's adapter
  admits only its own allowlist (no cross-profile leak).
- Given `HERMES_RETICULUM_ALLOW_ALL=on` set in process env and, separately, in a
  profile scope, When the adapter evaluates the same peer, Then both produce the SAME
  verdict (the by-source asymmetry is gone).
- Given the adapter running, When the A3 config read path is swept, Then no `os.getenv`
  appears as the deciding source for the four ACL knobs.
- Given `core/acl.py` imported without the gateway package, When `parse_allow_all` is
  called with a reader injected by the adapter, Then the parse resolves (core stays
  gateway-import-free; tests inject).

**Handoff (2026-10-24, carry into this session):**
- *Repro.* `uv sync --extra dev` then `uv pip install ruamel.yaml` (the local conftest
  needs it). Lint with `uvx ruff check src/ tests/` — CI installs unpinned `ruff>=0.4`
  (today 0.17.2, so UP032/UP035 are live) and runs ruff on `src/ tests/` only. Test the
  adapter with `uv run pytest tests/test_reticulum_adapter.py`. The full suite locally
  shows 2 benign `test_tool_emoji::TestParityWithInstalledGateway` drift failures CI does
  not hit (CI stubs the Hermes gateway).
- *Don't rebuild — shipped in A3 (PR #16):* the identity knobs
  `RETICULUM_DISPLAY_NAME` / `RETICULUM_ANNOUNCE_INTERVAL` are already profile-scoped via
  `_get_scoped_secret` (adapter ~L225); `_build_access_control` already injects the
  `platform_gate_env` reader (`AccessControl(get_env=...)`, `import` fallback → `os.getenv`);
  `ControlServer._acl_mode()` already shares `AccessControl.parse_allow_all`. A11 extends
  and verifies those under a real two-profile broker — it does not rebuild them.
- *Size this first.* Every acceptance criterion needs two Hermes profiles on one box
  (`profiles/<name>/config.yaml` per profile, `multiplex_profiles` broker). Nothing in the
  repo or the amendment notes shows an existing second profile or a multi-profile test
  harness — confirm/build that setup before the criteria are provable, and keep the A3
  PII rule in mind (no real peer hashes/display names in committed docs).
- The amendment's "still open" item on the transport knobs stays open here: should
  `platform_gate_env` also subsume `RETICULUM_DISPLAY_NAME` / `RETICULUM_ANNOUNCE_INTERVAL`,
  or keep them on `_get_scoped_secret`?
- *Lanes (2026-10-24):* canonical checkout `.../rns_hermes_endpoint_private`; `..._pr16` =
  the shipped A3 impl (merged); `..._pr16b` untouched/off-limits; `pr-13-review` still holds
  an unpushed Jev-doc commit (`c46d1d4`, move to dev pending). One card = one PR into `dev`,
  squash-merged (see #13-17). PII-scan every staged diff before commit; author `strtPath`.

**Status:** in-review. Guard shipped merged PR #21 (2026-10-24): regression test
`test_acl_knob_names_are_spelled_reticulum` pins canonical `RETICULUM` knob names and
asserts the misspelled `RETICUM` variant is never consulted (live-outage class guard).
Harness profile `mesh-test` exists on-device with a synthetic allowlist; no-leak and
parser/reader criteria covered by the suite. Remaining for close: ruling the transport-knob
question (keep on `_get_scoped_secret` vs fold into `platform_gate_env`), and a live
two-profile multiplex broker run.

**Delivery context (2026-10-26):** live single-profile adapter round-trip now confirmed
end-to-end after a per-platform display fix (`display.platforms.reticulum.tool_progress:
false` + `thinking_progress: false`); see
`docs/mesh-bridge-findings-2026-10-26-adapter-live-delivery-one-shot.md`. That unblocks the
live two-profile broker run this card still needs to close.

## Card A12: No false desync rejection on a busy-session inline dispatch

**Build:** Stop the adapter sending `⛔ Access not authorized.` to an allowlisted peer right
after that peer's own command ack. A command (`/stop`, `/new`, `/reset`, …) or a clarify answer
arriving while a turn is running is dispatched inline by the base's
`_handle_message_while_active`; those paths return without setting `event._gateway_accepted`,
so `deliver_inbound` reads the base's `False` default as a gateway authz deny (A3 4.5 D7) and
fires the canned rejection on an event the gateway actually handled.
**Spec basis:** A3 4.5's premise — "False when its own authz/ingress gate dropped the event,
True once the event is claimed for a session" — is false on the inline paths. An inline
dispatch *is* a claim. The D7 deny signal must stay reliable for genuine denies (A3 criterion
10) while not firing on a handled event. Findings:
`docs/mesh-bridge-findings-2026-11-01-false-desync-after-busy-command.md`.
**Blocked by:** A3 (merged). Not blocked by A4-A11.
**Acceptance:**
- Given an allowlisted peer with a turn running, When it sends `/stop`, Then it receives the
  stop ack and **no** `⛔ Access not authorized.` follows, and no `gateway desync` line is
  logged for that peer.
- Given the same peer sends `/new` (or `/reset`) mid-turn and answers the confirm prompt inline,
  When the prompt is answered, Then no desync line and no rejection are produced.
- Given a clarify prompt is pending and the agent is blocked, When the peer's text answer is
  dispatched inline, Then no desync line and no rejection are produced.
- Given the adapter admits a peer and the gateway genuinely denies the delivered event (authz
  mismatch), When the event returns with `_gateway_accepted` False and no reply was sent for it,
  Then the desync line is logged and the canned reply is sent — A3 criterion 10 still holds.
- Given the A3 suite plus a busy-session regression case in the CI stub (the stub currently
  covers only the fresh-inbound path), When the suite runs, Then all pass, including the new
  case that reproduces the false alarm red-before/green-after.
**Open decision for the peer:** which fix shape. The adapter-side discriminator (treat False as
a deny only when no reply was delivered for the event during the `handle_message` await) keeps
A3 criterion 10 intact and needs no gateway change; the gateway-side fix (set the flag after a
successful inline dispatch) is the correct one but the gateway is a separate install, so it
lands as an upstream report or a documented base-class override. Both are laid out in the
findings doc's "Fix options".
**Status:** proposed — awaiting peer ruling. No code before that ruling (AGENTS.md).

## Branch and PR workflow

- `dev` is the integration branch every card merges into (squash; see PRs #13-18).
  The legacy `adapter` integration branch and its `feat/reticulum-platform-adapter`
  predecessor were **deleted 2026-10-24** — their work was re-split into the card series
  and is fully contained in `dev`. Other agents will not find them if they look; branch
  from `dev`.
- One card per branch (`step/A3-acl-rejection` style), one PR, review bot feedback on the PR,
  fixes pushed to the same branch. Card acceptance above is the PR's "done" definition.
- No card merges while a blocked-by card is unmerged.
- Spec, code, and skill update together per card, except pure planning rounds (spec + skill
  only) - the standing peer rule.

---
