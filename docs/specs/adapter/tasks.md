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
| A4 | Command feedback: verify the gateway-handled commands on the mesh; send-failure truth as a WARNING | A1 | `/status`, `/resume`, `/new`→`yes` answered on the phone with zero adapter command code; failed LXMF delivery logs a WARNING | code in `dev` (merged 2026-11-21, PR #1; post-merge senior follow-ups same day); rows 1-3 phone exercise open |
| A5 | `/new` ack names new session id + auto-title — **dropped 2026-11-04 (peer)**; the ack is gateway-side and already carries the title | A4 | none; verification only | dropped |
| A6 | Tool-call visibility: declare the platform's display tier/overrides so the gateway's existing progress lane renders (low tier by default; mid-turn commentary kept on; `/verbose` is the gateway's command) | A4 | a 22-tool turn is no longer silent after the peer opts in with `/verbose`; mid-turn commentary reaches the peer; `/verbose off` truncates like the CLI | open (amended 2026-11-04) |
| A7 | YAML config hook (`apply_yaml_config_fn`), display name + announce interval as config keys | A1 | `config.yaml` keys work without env vars | open |
| A8 | Ship `approvals.timeout: 900` with LoRa rationale | A1 | mesh approvals survive LoRa latency | done |
| A9 | P-13 command audit — **dropped 2026-11-04 (peer)**; P-5/P-6 already superseded by gateway commands | A4 | none; no build | dropped |
| A10 | P-8 `/clarify` verification (source read done; live run outstanding) | A4 | `/clarify` resolves on the mesh or a findings doc proves the gateway gap | open (amended 2026-11-04) |
| A11 | Per-profile mesh identity + ACL profile-scoping: one shared parser; ACL + identity read profile-scoped via `platform_gate_env` | A3 | two profiles on one box carry different identities + trust lists; no raw `os.getenv` in the A3 read path | closed (guard PR #21; isolation proven live 2026-10-28: `build_profile_secret_scope` on both homes returns distinct trust lists, both fail-closed — see `docs/mesh-bridge-findings-2026-10-28-a11-profile-isolation.md`) |
| A12 | Stop false `⛔ Access not authorized.` after a busy-session inline command: the base leaves `_gateway_accepted` False on the inline-dispatch paths, so the adapter's D7 deny signal fires for an allowlisted peer | A3 | `/stop`, `/new` and a clarify answer mid-turn get their own ack and no rejection; a genuine gateway deny still logs a desync line and sends the canned reply | merged 2026-11-15 (PR #40, senior review 5/5 round 3); live `/stop` mesh confirmation still open |
| A13 | Retire the `platform_hint` line forbidding markdown, for a mesh that renders markdown and code blocks | - | the hint no longer forbids markdown; the bandwidth caveat survives | merged 2026-11-10 (PR #37) |
| A14 | A12 residue: inline paths that handle an event but send nothing (handler yields no text; the base swallows the inline-dispatch exception; queue-mode busy-text debounce parks the follow-up) still read as a D7 deny under A12's evidence rule | A12 | the upstream flag fix lands and the evidence rule retires, or an upstream-safe signal separates handled-but-silent from dropped; until then the documented silent-inline paths are known false-alarm surfaces | open, proposed 2026-11-14 |
| A15 | Real-base harness double-loads `gateway.platforms.event`, so `MessageType` comparisons inside `base.py` are dead in tests — a green-suite blind spot | - | one event module loaded, conftest identity assert, and the silently-untested base branches re-audited | open, proposed 2026-11-14 |
| A16 | `scripts/security-sweep.py` silently passes a nonexistent path argument, so `security-sweep.py origin/dev..HEAD` is a false green | - | nonexistent paths exit nonzero and a git-range-looking argument is honored or rejected loudly | code merged into dev 2026-11-21 (PR #2; same-day follow-up teaches the sweep to allow GitHub noreply author addresses) |
| A17 | One mesh identity per profile: per-profile default storage path and an identity collision refused once with a named error; `rnsd` and interfaces stay shared | two-profile test harness (A11 handoff) | two profiles on one box connect with distinct identities; the default profile's address does not change | open, proposed 2026-11-19 (D1, D2 ruled; harness first) |
| A18 | `docs/skills/repo-navigation.md` is stale: its code map lists five pre-move paths (`src/hermes_reticulum/{cli,core/*}.py`, now under `bridge/`), says "build cards A1-A10" while tasks.md runs to A17, and still calls `adapter` a live branch (deleted 2026-10-24) | - | every path in the navigation table resolves in-tree; card and branch references match the tree | open, proposed 2026-11-21 (PR #3 review, finding F3) |
| A19 | License close-out per ADR-0002: pyproject still declares ranges (`rns>=1.5.4,<2.0`, `lxmf>=1.1.1,<2.0`) where the ADR decided exact pins, and the Reticulum License's field-of-use terms appear nowhere a user actually meets them — README's License section says only "MIT", install.sh never shows them, and the PyPI wheels carry no terms text. Vendoring audit 2026-11-21: zero RNS/LXMF source blobs across every committed path and object — re-runnable as `git log --remotes --name-only --pretty=format:` (paths only) or `git rev-list --objects --remotes`, each piped to the vendoring names grep spelled out in the acceptance list below → 0 — and across every local ref; keep it that way. | - | pins match uv.lock under `uv lock --check` plus a test; suite at the pinned pair with only the two named known reds; README and install.sh state the terms verbatim, test-grepped; both vendoring nets in the acceptance list below (content grep and names grep) stay empty | merged 2026-11-23 (PR #5, squash e75e6b0; senior GO 4/5 round 2, greptile 5/5; card landed 2026-11-21 in PR #4): pins applied as rns==1.6.1 / lxmf==1.3.1, terms in README + install.sh, both vendoring nets empty |
| A20 | `ReticulumTransport.__init__` accepts `storage_path` but never uses it, so every transport test that passes the kwarg believes it isolated storage while `os.makedirs` writes the default path under the developer's `HOME` — CI-stub runs at `HOME=/nonexistent` fail 28 tests that have nothing to do with the code under test | - | the kwarg reaches `self.storage_path` (chain order pinned), a started transport creates nothing under `$HOME`, the transport file is 0-failure at `HOME=/nonexistent` | merged 2026-11-25 (PR #7, squash c1dec26; senior GO 4/5 first pass with conditions, GO 5/5 second pass; greptile 5/5, threads auto-resolved): fold applied as `extra` > kwarg > env > default; sibling unwired arguments filed as issue #8 |
| A21 | `install.sh` hardcodes `${HOME}/.hermes`, so every other Hermes home (each profile is its own home, and the multiplexed gateway loads plugins from each) keeps whatever shim and `venv_path.txt` it had: observed 2026-11-26, the default home connected on the release checkout while a second profile connected on the retired one, each logging a happy `connected` line | - | a named home receives the shim, both `venv_path.txt` roots, the gate copy and the config writes; an existing user value survives; no-flag run is identical on home-scoped writes; a read-only drift check (`--check`) names stale and marker-less homes; non-default homes skip the archived unit; docs name the per-home marker. Homes consistent here; mesh identities stay distinct in A17 — until that lands a second profile still collides on `gateway_identity` (`tasks.md:808-810` records the collision; the field is named at `:798`, `:821`) | open, proposed 2026-11-26; card landed 2026-11-27 (this PR; senior GO 4/5 fifth pass): build not started |

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
(`docs/specs/adapter/spec.md`); card A4 closes O-8. O-7's decided text stands, but the card that
would have implemented it (A5) was dropped 2026-11-04 — the ack turned out to be gateway-side.
See the A5 card record.

## Un-carded by peer decision

Parked, not forgotten: cron-over-RNS delivery vehicle (community LXMF bot note, §21.3 item 8
in `plan.md`), `standalone_sender_fn` design question, P-2 map,
P-3 media, P-4 NomadNet channel, P-9 gateway watchdog. (The RNS version pin,
long parked here as "§14", is carded now — A19; the §14 pointer was a
misreference, the licensing section is §11.)

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

## Card A4: Command feedback + send truth

**Build:** Phone-exercise `/status`, `/resume`, and the `/new` confirm→`yes` cycle (gateway
commands, no adapter code); make the delivery tracker log WARNING when a send is lost.
`mesh_terse()` for the confirm body is **dropped** — see the amendment below.
**Spec basis:** old §20 (mechanism verified from source; gateway core untouched; ride the base
`send_slash_confirm` / `send_clarify` defaults).
**Blocked by:** A1.
**Acceptance:**
- Given the phone on the mesh, When `/status` is sent, Then a reply packet is observed on the
  peer side (not just a gateway "response ready" log line).
- Given the phone on the mesh, When `/resume` is sent, Then the gateway's session listing / picker
  text arrives and the named session becomes current.
- Given the phone on the mesh, When `/new` is sent and the peer replies `yes`, Then the reset
  runs and the peer sees the gateway's ack (`gateway.reset.header_titled`, title only). The
  session id, if wanted, comes from `/status` — the dropped card A5 covered that.
- Given a send whose LXMF delivery fails, When the failure callback runs, Then the send is
  reported failed and a **WARNING** names the lost delivery.

**Amendment 2026-11-04 — command dispatch is gateway-side, so most of this card is verification.**

Verified against the installed gateway source:

- `MessageEvent.allow_gateway_control` defaults to `True` (`gateway/platforms/event.py:121`) and
  `MessageEvent.is_command()` is gated on it (`:178`). The adapter builds the event without
  setting the field (`src/hermes_reticulum/plugin/adapter.py:556`), so commands are allowed by
  default, and `adapter.deliver_inbound` deliberately delegates to the gateway's
  `handle_message` (`adapter.py:590`), which coerces and routes on the same flag
  (`gateway/platforms/base.py:3941`).
- `/status` (`hermes_cli/commands.py:170`, handler `gateway/slash_commands_status.py:242`),
  `/resume` (`hermes_cli/commands.py:182`, handler `gateway/slash_commands_session.py:1054`),
  `/new` (`hermes_cli/commands.py:87`), `/retry` (`:100`) and `/verbose` (`:208`) are all
  non-`cli_only`, so every one of them is dispatched for this platform with no adapter code.
- The confirm reply works as the card assumed: `_hm_slash_confirm_reply` runs on the inbound
  path (`gateway/run_inbound.py:1150`) and bare `yes` resolves to `once` because it is in
  `approval.inputs.confirm_once` (`locales/en.yaml:23`).

Consequence — `mesh_terse()` cannot do what the card asked. The adapter never composes the
confirm body: the gateway formats it (`gateway/run_busy.py:1578`) and, because the adapter does
not override `send_slash_confirm`, the base returns `SendResult(success=False, "Not supported")`
(`gateway/platforms/base.py:2820`) and the gateway replies with that same message text
(`gateway/run_busy.py:1673`). The adapter receives only the finished locale string, so a
terse re-render would mean parsing a translated, locale-dependent block to recover the detail
— brittle, and the detail line is the part the peer must actually read.

Measured cost of leaving it: 288 rendered characters for `/new` (`locales/en.yaml:794`,
counted 2026-11-04), i.e. one LXMF packet, well inside this platform's 1024-byte
`max_message_length`. Accepting the gateway's text as-is therefore costs one packet, not the
card's assumed multi-line flood. The locale's `**bold**` markdown was the remaining worry (a
client that renders it literally shows the markers); the ruling below settles that for the
clients in use.

What the peer actually sees today, for `/new`:

```
⚠️ **Confirm /new**

This clears the current conversation history.

Choose:
• **Approve Once** — proceed this time only
• **Always Approve** — proceed and silence this prompt permanently
• **Cancel** — keep current conversation

_Text fallback: reply `/approve`, `/always`, or `/cancel`._
```

Three dispositions were open; **ruled 2026-11-04 (peer): leave it as the Hermes default.** Reticulum
clients mostly render markdown and code blocks, so the `**`/`_` markers are not a defect on this
platform, and the menu text is the same one every other platform shows. Options 2 (a gateway
`detail` argument) and 3 (a plugin locale pack) are not taken; each would be its own card if
they are ever wanted.

**Follow-up defect this ruling exposes (proposed card A13, awaiting peer ruling):** the platform's
registration tells the model the opposite of the ruling above —
`src/hermes_reticulum/plugin/registration.py:147-151` passes a `platform_hint` reading "Messages
are plain text only — no markdown formatting". If clients render markdown, that hint suppresses
formatting the peer could actually read (code blocks in particular). Fixing the hint is one string
but it changes model behaviour on every mesh turn, so it is carded, not slipped into A4.

**Remaining code in this card:** the WARNING level. `_handle_outbound_failure` logged at INFO
and the FAILED *state receipt* path (`_handle_outbound_outcome`) logged the same event at INFO
as a success receipt would; both now log WARNING naming the lost delivery (destination + payload
snippet), with the DELIVERED path pinned below WARNING. Shipped on branch
`step/A4-send-warning` (tests `test_a4_01`-`test_a4_03`, red-before recorded in
`verification.md`); rows 1-3 remain a phone exercise, no adapter code. The 2026-11-18 phone
attempt is parked with its evidence in the A4 live-run note in `verification.md` (suspected
client-side slash-command interception masking the gateway dispatch; peer deferred the follow-up).

## Card A5: `/new` ack with session id + title — DROPPED 2026-11-04

**Status:** dropped by peer decision. No build, no branch, no PR. Kept as a record so the card is
not re-proposed from the old parity table.

The card assumed the adapter composes the `/new` ack. It does not — the gateway does
(`gateway/slash_commands_session.py:242`, `:283`). What the gateway's ack actually carries:

- title: yes — `gateway.reset.header_titled: "✨ New session started: {title}"` (`:283`), with the
  untitled fallback `gateway.reset.header_new: "✨ New session started!"` (`:242`).
- session id: **no**. Nothing in either string names it.

The id is available on demand: `/status` returns `gateway.status.session_id`
(`gateway/slash_commands_status.py:242-300`), and `/resume` lists titled sessions
(`gateway/slash_commands_session.py:1054-1071`). So the peer's stated goal — "a new session ID or
name so that you can go back to it" — is served by the gateway without adapter code.

O-7 (`docs/specs/adapter/spec.md:575`) stays decided: the ack should name the id and title. The
title half is implemented gateway-side; the id half is not. If the peer still wants the id in the
ack itself, that is a **gateway** card, not an adapter build — the adapter cannot reach the text.

## Card A6: Tool visibility (P-1) + `/verbose` (P-7)

**Build:** Declare the platform's display defaults (`display_tier` + `display_overrides`) on the
`PlatformEntry` so the gateway's existing tool-progress lane renders correctly for Reticulum;
add the `display.tool_progress_command` check/prompt to `install.sh`; verify emoji/truncation
parity. Do **not** build a step pusher — see the amendment.
**Spec basis:** peer rulings: auto title gen (already gateway-side), "verbose on, should be
like the cli default, otherwise truncated like the cli does."
**Blocked by:** A4.
**Acceptance:**
- Given a turn with 22 tool calls, When it runs on the mesh with the peer having run `/verbose`
  to turn progress on, Then the peer sees tool-call progress like the CLI sees it.
- Given `/verbose off`, When a turn runs, Then recaps arrive in the truncated form.
- Given the adapter's tool emoji table, When the gateway's own label table is compared, Then
  the same tool maps to the same emoji (`tests/test_tool_emoji.py:139
  TestParityWithInstalledGateway`).
- Given a `~/.hermes/config.yaml` with no `display.tool_progress_command`, When `install.sh` runs
  interactively, Then it asks once and writes the key only if the answer is yes.
- Given a config that already carries `display.tool_progress_command` (either value), When
  `install.sh` runs, Then no prompt appears and the existing value is unchanged.
- Given an install whose stdin is not a TTY, When `install.sh` runs, Then it does not block, does
  not write the key, and prints the YAML snippet to paste.

**Amendment 2026-11-04 — the progress lane already exists in the gateway, and it is the pusher.**

Verified against the installed gateway source:

- Tool progress is resolved per platform from `display.platforms.<platform>.tool_progress`
  (`gateway/run_turn.py:872`, `gateway/run_turn.py:912`) and delivered by the gateway's own
  progress renderer through **the adapter's ordinary `send()`**
  (`gateway/run_turn_progress.py:626`). `/verbose` cycles that same setting
  (`hermes_cli/commands.py:208`). Nothing needs to consume tool events in the adapter.
- What is actually missing is only the declaration: `ctx.register_platform(...)` in
  `src/hermes_reticulum/plugin/registration.py:134-158` passes no `display_tier` and no
  `display_overrides`, although `PlatformEntry` supports both
  (`gateway/platform_registry.py:117`, `:121`). With neither set, the resolver falls through to
  `_GLOBAL_DEFAULTS` (`gateway/display_config.py:18`): `tool_progress: "all"` and
  `tool_progress_grouping: "accumulate"`.
- `accumulate` sets `can_edit` true (`gateway/run_turn_progress.py:591`), so the gateway calls
  `edit_message`, which this adapter inherits unmodified from the base and which returns
  `SendResult(success=False, "Not supported")` (`gateway/platforms/base.py:2650`). The renderer
  then falls back to sending anew — the extra chatter and duplicate bubbles the 2026-10-26 live
  run suppressed with a *user-side* `display.platforms.reticulum.tool_progress: false`
  (`docs/mesh-bridge-findings-2026-10-26-adapter-live-delivery-one-shot.md`). A per-platform
  declaration fixes it at the source instead of in one operator's config.

**Ruled 2026-11-04 (peer): tool progress is `low` by default.** The registration declares
`display_tier: "low"` (full override block below), so the peer opts in with `/verbose` rather
than every mesh turn carrying progress packets.

What `_TIER_LOW` turns off (`gateway/display_config.py:45-48`, inherited from `_TIER_HIGH`):
`tool_progress: "off"`, `streaming: False`, `interim_assistant_messages: False`,
`long_running_notifications: False`, `busy_ack_detail: False`; `tool_preview_length` would
inherit `40` from the HIGH tier, which is why the override sets it to `0`. Command acks and turn
replies are unaffected.

**Ruled 2026-11-04 (peer), separately: keep `interim_assistant_messages` ON.** That is the
mid-turn commentary the model writes alongside a tool call — the "let me check X first" prose the
CLI shows while work is running. The agent emits it per tool round
(`agent/stream_delivery.py:231`, called from `agent/turn_tool_round.py:158` after the row is
persisted), and the gateway delivers each segment as its own message
(`gateway/run_turn_runner.py:322-346` → `stream_consumer.on_commentary` or `_send_status_text`).
It is genuinely useful for following a long turn, so the LOW tier's `False` is overridden back to
`True`:

```
display_tier: "low"
display_overrides: {"tool_progress_grouping": "separate",
                     "tool_preview_length": 0,
                     "interim_assistant_messages": True}
```

Accepted cost, recorded so nobody rediscoveres it as a bug: each commentary segment is its own
`send()`, so a chatty model on a 22-tool turn can add a packet per tool round on top of the final
reply. That is the trade the ruling takes — the peer sees progress instead of silence.

**Blocker found while recording the ruling — `/verbose` is gated off for messaging chats.**
`_handle_verbose_command` writes `display.platforms.<platform>.tool_progress` and persists it
(`gateway/slash_commands.py:975-1009`), but for a chat with no local live session it first
checks `display.tool_progress_command` (default **off**, `gateway/slash_commands.py:986`) and
returns the "not enabled" string when unset. So with the tier shipped and nothing else, a mesh
peer cannot turn progress on: A6's opt-in half is unreachable and row 1 below can only pass
after an operator-side config edit.

`display.tool_progress_command` is a top-level key, so a plugin cannot set it from its own
subtree — it lands the same way `approvals.timeout` does (A8): through `install.sh` / the
shipped config.

The gate is applied twice from the same key, so one flip satisfies both: the registry drops a
`cli_only` command unless its `gateway_config_gate` dotpath is truthy
(`hermes_cli/commands.py:208-209`, gate logic `:504-523`), and the handler re-checks it and
returns "not enabled" for a messaging chat (`gateway/slash_commands.py:986`). Default is `False`
(`hermes_cli/config_defaults.py:913`).

**Ruled 2026-11-04 (peer): ship the key through `install.sh`, and ask the installer.** The
installer checks `display.tool_progress_command` in `~/.hermes/config.yaml` first; if the key is
already present, its value is left alone (the same "an existing user value is left alone"
convention the script already applies to `approvals.timeout`). If the key is absent, the
installer prompts once, and only writes it when the answer is yes. Declining leaves the key
absent — the gateway default (`False`) stands and `/verbose` stays unavailable on the mesh.

Two implementation constraints found while reading `install.sh` — both belong to the build, not
to the design:

- The script has **three** config write paths and each `raise SystemExit(0)`s as soon as its own
  keys look satisfied: append-everything (`install.sh:221-236`), append-plugins-only when an
  `approvals:` block already exists (`:238-249`), and the YAML round-trip (`:253-307`, whose own
  early exit is `:283-285`). The append paths test only for `plugins:` and `approvals:`, so
  appending a top-level `display:` block into a file that already has one would duplicate the
  key — a YAML error. The display check has to join each branch's condition, not be added after
  the early exits.
- The script contains **no prompts today** (no `read`, no `input()`) and its documented
  invocation is `bash install.sh` from a checkout (`README.md:74`, `:101`), where stdin is the
  terminal — but a piped or CI install would hit EOF on a bare `input()`.
- The config editor is a Python here-document (`install.sh:197`, `<<'PYEOF'`), so **its stdin is
  the script body, not the terminal**: `sys.stdin.isatty()` inside that block is always false,
  and a bare `input()` there would consume the rest of the script rather than the user's answer.
  The interactivity check therefore belongs in bash before the here-document (`[ -t 0 ]`),
  passed in as argv/env, and the prompt itself must read `/dev/tty`; a missing `/dev/tty` means
  non-interactive. (Greptile raised this on #36; the correction is its finding.)

**Ruled 2026-11-04 (peer): a non-interactive install skips the prompt.** No answer means no key:
leave the default (`False`), and print the three-line YAML snippet for the user to paste if they
want `/verbose` later. Nothing is enabled unattended — turning a gateway command on without
anyone asking is the one outcome this rule exists to prevent.

**What the emoji half becomes:** the peer ruling asked the step push posts to use "the same
registry Telegram/CLI use". Under gateway rendering the emoji come from the gateway's own display
layer, so the requirement collapses into the parity check already in the tree
(`tests/test_tool_emoji.py:139`), not into adapter-side lookup code.

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

## Card A9: P-13 command audit — DROPPED 2026-11-04

**Status:** dropped by peer decision. No build, no branch, no PR. Kept as a record so the card is
not re-proposed from the old parity table.

P-5 (`/retry`) and P-6 (adapter-built `/status`) were already removed: both are gateway commands
(`hermes_cli/commands.py:100`, `:170`; handler `gateway/slash_commands_status.py:242`), non-
`cli_only`, so they answer on the mesh today.

P-13 itself does not earn a build, for three reasons recorded on 2026-11-04:

1. **Its premise is gone.** Dispatch is a staged router, not a table lookup: `_hm_dispatch_idle_commands`
   (`gateway/run_inbound.py:1155-1177`) resolves the event, tries the canonical handler, then
   quick/plugin commands, then a skill-slash rewrite, and finally admits an unrecognised slash
   command as a normal agent turn. The bridge needed the audit because a command could vanish;
   under this router it becomes a turn.
2. **Classifying "is this a command" in the adapter would duplicate gateway logic** and drift from
   it — the exact failure mode that made P-6 worth deleting. The gateway owns the definition
   (`MessageEvent.get_command`, `hermes_cli/commands.py`); an adapter-side copy is a second
   source of truth on a security-adjacent path.
3. **The security-relevant events are already logged.** A3 logs every ACL denial per peer, and the
   adapter logs gateway desyncs; the incidents in `docs/mesh-bridge-findings-*` are denial- and
   delivery-shaped, not "what command did the peer run" shaped.

Nothing exists gateway-side as a structured command record (the only `audit_log` in the tree is
dashboard auth, `hermes_cli/dashboard_auth/audit.py:96`; there is no `commands.log`). If an audit
is ever wanted, it belongs in a card that consumes the gateway's dispatch hook rather than
re-classifying inbound text in the adapter.

## Card A10: P-8 `/clarify` verification

**Build:** Verification only. The source read is done (amendment); what remains is a live mesh
exercise of a clarify prompt and its answer. No adapter plumbing is expected.
**Spec basis:** §19.2 in `plan.md`, P-8 row ("gateway plumbing existence unverified; adapter-side callback if
absent").
**Blocked by:** A4.
**Acceptance:** Given `/clarify`-style output is possible on the mesh, When probed from source
or a live run, Then either it works or a findings doc names the gateway gap precisely.

**Amendment 2026-11-04 — the source half is answered; no plumbing is needed.**

The base adapter's `send_clarify` already is the mesh-shaped implementation: with `choices` it
emits the question, a numbered option list, and a hint, then calls
`tools.clarify_gateway.mark_awaiting_text` so the next inbound message is captured as the answer;
without `choices` it sends the question alone (`gateway/platforms/base.py:2830-2855`). The turn
runner explicitly handles an adapter that has no native override and drives that base path
(`gateway/run_turn_runner_clarify_delivery.py:29-39`). The adapter overrides neither method, so
it gets the numbered-text path by construction — exactly what the cross-cutting "no interactive
UI over LXMF" constraint requires, with no button to wait on.

The answer-interception side is the same one the confirm cycle uses
(`_hm_pending_reply_intercepts` -> `_hm_clarify_reply`, `gateway/run_inbound.py:1140-1155`).

**Remaining work is the live run:** trigger a clarify from a mesh turn, answer with a number or
text, and record the result in `verification.md`. If the live run contradicts this source read,
the contradiction is a findings doc and a new card — not an adapter callback written on a guess.

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
  mismatch), When the event returns with `_gateway_accepted` False and no send to that peer was
  attempted during the call, Then the desync line is logged and the canned reply is sent — A3 criterion 10 still holds.
- Given an accepted inline dispatch whose reply send fails (LXMF delivery failure), When the
  event returns with `_gateway_accepted` False, Then no desync line and no rejection are
  produced — the marker records the send attempt, not its result.
- Given an accepted inline dispatch whose reply is still in flight when `handle_message`
  returns, When the event returns with `_gateway_accepted` False, Then no desync line and no
  rejection are produced.
- Given the A3 suite plus a busy-session regression case in the CI stub (the stub currently
  covers only the fresh-inbound path), When the suite runs, Then all pass, including the new
  case that reproduces the false alarm red-before/green-after.
**Fix-shape ruling (peer, 2026-11-11):** option 1 — the adapter-side evidence-based
discriminator. `_gateway_accepted is False` counts as a deny only when no send was *attempted*
for the event during the `handle_message` await; the marker is a context-local attempts list
installed around the await and ticked at `send()` entry. The deny read matches the recorded
destinations against the event's own peer — a send routed to a different destination is no
evidence about this event (review round 1, 2026-11-14). Option 2 rejected (half-fixes).
Option 3 (gateway sets the flag after a successful inline dispatch) is still the correct
long-term shape and lives in the upstream report
`docs/upstream-report-2026-11-11-gateway-inline-dispatch-accepted-flag.md`, not in this repo.
**Known limits (review 2026-11-14):** the evidence rule separates *handled-and-replied* from
*dropped*; it cannot separate *handled-but-silent* inline paths — a bypass/clarify handler
that yields no text (early return, `base.py:3492`), a swallowed inline-dispatch exception
(`base.py:4018-4019`, `:4038-4039`), or a queue-mode busy-text debounce — with the busy text
mode set to `queue`, follow-up text is parked by `_queue_text_debounce` and the base returns
without the flag (`base.py:4081-4085`; the runner defers to that path at `run_busy.py:977-982`)
— from a genuine deny. Those still read as a denial; the residue is carded as A14, not papered
over here. Queue mode is a deliberate config choice (`busy_input_mode: queue`, legacy
`busy_text_mode: queue` — `run_config_loaders.py:296-306`, wired at `run_adapters.py:1381`),
the obvious one on a slow link, so this clause is reachable by intent, not only by accident.
**Status:** merged 2026-11-15 — PR #40 (merge commit into `dev`). Red-before recorded in
`verification.md` (five cases fail on `dev`: the rejection fires after the inline ack); green
after in both gateway modes (real base and CI stub). Senior cold review round 1 (2026-11-14,
3/5, nine findings): round-1 commits close findings 1-7 and 9 (peer-scoped deny read, marker
context-scoping pinned by new tests, stub self-guard, parametrization honesty,
comment/docstring/verification corrections, upstream-report citations); A13's status paperwork
moved to its own docs branch (finding 8). Senior re-review round 2 (2026-11-14, 4/5, one
finding): all nine round-1 closures verified by repro and mutation; the residue enumeration
was short the queue-mode busy-text debounce path — completed here and in A14. Round 3
(2026-11-15): zero findings, 5/5 — merge bar met; all anchors verified against the installed
gateway, mutations re-run independently, suites green in both modes and on the merge with
dev. Of the reviewer's three non-blocking notes, the two A15-relevant ones (A15 blast
radius beyond MessageType; the count-check clause of A15 acceptance row 1 already passing
on the broken state) are recorded on the A15 card itself; the third — a3-spec 4.5 and
verification.md cite only the primary `base.py:4081-4085` anchor while the full config
chain lives in the A12 card and A14 — was judged in scope for those docs' purpose and left
as is. Full verdict text lives on the dev machine under
`~/.hermes/reviews/pr40-seniordev-round3-verdict.txt`. Live mesh confirmation is still
open: one `/stop` mid-turn over a real RNode session remains the outstanding acceptance
criterion.

## Card A14: Handled-but-silent inline paths still read as a D7 deny

**Build:** A12's evidence rule (False flag + no send to this peer = deny) cannot distinguish
an inline path that handled the event but produced no send from the gateway's authz drop:
a bypass/clarify handler that returns no text exits `_dispatch_inline_reply` before any send
(`base.py:3492`), and a handler exception is swallowed by the dispatch branches'
`except Exception` (`base.py:4018-4019`, `:4038-4039`). A third path joins them by config,
not accident: with the busy text mode set to `queue`, follow-up text mid-turn goes through
`_queue_text_debounce` and `_handle_message_while_active` returns without setting the flag
(`base.py:4081-4085`; the runner defers to the base path at `run_busy.py:977-982`; mode wiring
`run_config_loaders.py:296-306` -> `run_adapters.py:1381`) — queued, silent, flag False, and
the adapter fires the 2026-11-01 symptom again (senior review round 2, 2026-11-14).
Live traffic so far only used
commands that always reply, so the false alarm has not recurred — but the surface exists and
the docs must say so (senior review of A12, finding 1).
**Spec basis:** A3 4.5 / criterion 10 as refined by A12 (`a3-spec.md` 4.5).
**Blocked by:** A12 (it refines that card's rule). The upstream flag fix — option 3 in
`docs/upstream-report-2026-11-11-gateway-inline-dispatch-accepted-flag.md` — makes the
evidence rule unnecessary and this card closes by retiring it.
**Acceptance:**
- Given a busy-session inline path that handles the event and sends nothing, When the event
  returns with the flag False, Then the adapter does NOT log a desync line or send the canned
  reply — or the path is proven unreachable from this platform and the finding is documented
  as unreachable instead.
- Given the busy text mode is `queue` and a plain follow-up arrives mid-turn, When the
  debounce path queues it and the event returns with the flag False, Then the adapter does
  NOT log a desync line or send the canned reply — or the mode is proven unsettable for this
  platform and documented as unreachable.
- Given the upstream flag fix lands, When the adapter runs against that gateway, Then the
  evidence rule retires to the plain flag read, the A12 marker tests are archived with a
  pointer, and A3 criterion 10 stays green throughout.
**Status:** open, proposed 2026-11-14. No build until the upstream report or an
upstream-safe adapter signal settles the shape.

## Card A15: the real-base harness double-loads `gateway.platforms.event`

**Build:** conftest loads `gateway.platforms.base` before `gateway.platforms.event`
(conftest.py:104-112); `base.py:441`'s module-level import executes event.py once and conftest
then re-executes it under the same module name. Two enum classes share a repr but not
identity, so `base_mod.MessageType is MessageType` is False and every
`event.message_type == MessageType...` comparison inside base (1562, 1704-1735, 3702, 4076)
evaluates False in real-base runs — those branches are silently untested. Surfaced while
repro-ing the A14 queue-mode residue (senior review round 2, 2026-11-14): the repro needed a
forced `_is_queue_text_debounce_candidate` because the dead comparison routes every text event
down the else-branch. Same defect class as A12/A14's lesson: a green suite that is not
exercising what its docstring claims.
**Spec basis:** none — harness correctness; it undermines the real-base evidence mode every
adapter card's verification leans on.
**Acceptance:**
- Given real-base mode, When conftest finishes loading, Then exactly one
  `gateway.platforms.event` module object exists and a conftest assert pins
  `base.MessageType is MessageType`.
- Given that assert holds, When the real-base suite runs, Then the branches that were dead
  (photo/queue/merge routing in `_handle_message_while_active` and the sites above) are
  re-audited and any verification row whose premise leaned on a dead comparison is corrected.
**Status:** open, proposed 2026-11-14. Test-honesty only; no shipping-code change. A12's
real-base runs did not depend on the dead comparisons (round-2 review confirmed), but the
re-audit must land before their evidence rows are quoted as complete.
**Round-3 review notes (2026-11-15), for the builder:** the blast radius is wider than
MessageType — the same double-load also kills `ProcessingOutcome` identity at
`base.py:3434-3435` (the `{...SUCCESS: ...}.get(outcome)` lookup). Foreign members are
minted by modules that import `ProcessingOutcome` straight from `gateway.platforms.event` —
`gateway/session_ingress.py:8` and `gateway/session_producer_lifecycle.py:20` — and
`session_producer_lifecycle.py:59` walks them into
`_run_processing_hook('on_processing_complete', ...)`; `gateway/run_turn.py:25` imports it
from base, so that path is safe. Another symptom of the same split. The
parenthetical site list above also misses two `message_type == MessageType.*` comparisons —
`base.py:4090` (`merge_text=`) and `base.py:4132` (`MessageType.VOICE`) — add them to the
re-audit list. And row 1 of the acceptance above hides a trap: the "exactly one module
object" count clause already passes on the broken state — only one object is live at count
time, the first being collected once its classes keep the namespace alive — so a count-only
fix is green on the defect; the identity assert is the load-bearing half. Full verdict:
`~/.hermes/reviews/pr40-seniordev-round3-verdict.txt` on the dev machine.

## Card A16: security-sweep blesses nonexistent paths

**Build:** `scripts/security-sweep.py /nonexistent/file.md` prints the clean line and exits 0
— verified during the PR #43 review (2026-11-15). The naive positional form
`scripts/security-sweep.py origin/dev..HEAD` is therefore a green light that scanned nothing,
not an error. Make a nonexistent path argument exit nonzero naming the path, and either honor
a git-range-looking positional (`a..b`) as `--commits a..b` or reject it with usage.
**Spec basis:** AGENTS.md asks for verification evidence a second party can re-run; a scanner
that exits 0 while scanning nothing manufactures false evidence. The sweeps recorded in this
repo used `--commits` for commit ranges, and the `--untracked` and path-mode runs
recorded there all name files that exist, so their results stand — but path mode is
exactly where this bug bites, not a bystander.
**Acceptance:**
- Given a nonexistent path argument, When the sweep runs, Then it exits nonzero naming the
  path and prints no "clean" line.
- Given a positional matching `a..b`, When the sweep runs, Then it is treated as a commit
  range or rejected with usage text — never scanned as a literal path.
- Given an empty positional argument, When the sweep runs, Then it exits nonzero (or
  ignores it loudly) rather than reporting clean — the loop documented at
  `docs/PII-RULES.md:65` passes "$f", which is empty whenever a diff line is blank.
Fixing bullets 1-3 in a pass also updates the commit-time loop at
`docs/PII-RULES.md:64-66`: raw `git diff --cached --name-only` lists staged deletions, so
a nonzero nonexistent-path exit would have that loop cry "PII in: <file>" on every
`git rm`. Pipe through `--diff-filter=d` (or use an index read, where a deleted path is
simply absent) in the same commit.
Adjacent, same pass: the script docstring claims coverage of `~/...` paths, but the
HOMEPATH rule (`scripts/security-sweep.py:46`) only matches absolute `/home/<user>` /
`/Users/<user>` — correct the docstring or the rule.
**Status:** code merged into dev 2026-11-21 (PR #2; same-day follow-up allows GitHub noreply
author addresses in the sweep), proposed 2026-11-15 (from senior review of PR #43). Tooling only;
no shipping-code change.

## Card A13: `platform_hint` no longer forbids markdown

**Build:** Rewrite the `platform_hint` string (then inline at
`src/hermes_reticulum/plugin/registration.py:147-151`) so it stops telling the model "Messages
are plain text only — no markdown formatting", and keeps the parts that are still true
(bandwidth-driven concision, possible off-grid client). It now lives in the module constant
`_PLATFORM_HINT` (`:111-127`).
**Spec basis:** peer ruling 2026-11-04, recorded in the card A4 amendment: most Reticulum clients
render markdown and code blocks, so the default Hermes text can stay as-is (A4) and the hint
should not suppress formatting the peer can read.
**Blocked by:** nothing. Deliberately not folded into A4 — that card's diff stays one card.
**Acceptance:**
- Given a mesh turn that produces code or a list, When the reply is composed, Then nothing in the
  platform's hint forbids markdown, and fenced code arrives intact through the existing
  content-aware chunking.
- Given the same hint, When it is read, Then the bandwidth/concision instruction and the
  plain-text *fallback* caveat survive (a client that does not render markdown still gets
  something legible — state the expectation explicitly rather than assuming).
- Given a client known not to render markdown, When it receives a formatted reply, Then the
  degradation is recorded (a findings note naming the client), not discovered later as a bug.

**Progress:** the hint is now the module constant `_PLATFORM_HINT`
(`src/hermes_reticulum/plugin/registration.py:120-127`) and four tests pin it in
`tests/test_reticulum_registration_env.py` (red-before: the old string fails three of them).

**Status:** merged 2026-11-10 (PR #37, merge commit into `dev`). Criterion 3's named client arrived
2026-11-11: `reticulum-mobile-app` (github.com/thatSFguy/reticulum-mobile-app) renders no
markdown — native message bubbles, no renderer field — recorded in
`docs/mesh-bridge-findings-2026-11-11-nonrendering-client-reticulum-mobile.md`.

## Card A17: One mesh identity per profile

**Build:** Give every Hermes profile its own Reticulum identity by default. The
storage path resolves from the profile's `HERMES_HOME`
(`$HERMES_HOME/.reticulum-gateway/storage`) instead of the hardcoded
`~/.hermes/...`, so the default profile keeps today's path and every other
profile gets its own `gateway_identity`. A second adapter that resolves a path
already held in this process fails once with a named `IdentityInUse` error
naming both profiles and the remedies, instead of retrying for the life of the
process. Propagation policy stays per adapter: outbound node choice is
per-router state and each profile keeps its own, and propagation downloads are
not implemented today (spec §5), so nothing here fetches mail for either
identity. Only the RNS instance and its interfaces are shared, by peer ruling.
**Spec basis:** `docs/specs/adapter/a17-spec.md`. Card A11's amendment promised
co-resident profiles could each carry their own identity, and made the ACL and
display name profile-scoped, but the identity directory stayed global; the
observed failure is `Attempt to register an already registered destination` on a
30s→300s backoff that never ends (`transport.py:168-171` path default,
`transport.py:303-315` identity registration against the process singleton).
**Blocked by:** a two-profile test harness. A11's handoff (this file,
"Card A11", Handoff bullet 3) records that no multiplex fixture exists, that
A11's isolation evidence was a manual live check, and that every criterion
needing two profiles had to be proven on a box. Rows 1, 3, 4, 6 and 7 here need
two adapters in one process, and AGENTS.md wants re-runnable evidence, so the
harness is the first task in this card (or a card of its own, listed here).
Both rulings landed 2026-11-19: D1 keeps the `RETICULUM_DISPLAY_NAME` gate, D2
refuses a conflicting RNS instance setting.
**Acceptance:** (the same nine rows as the spec, in order)
1. Given two profiles on one box, neither setting a storage path, When both
   Reticulum adapters start, Then each has a distinct `gateway_identity` and
   destination hash, and both connect.
2. Given the default profile with nothing configured, When the adapter resolves
   its storage path, Then `Path(storage_path)` equals `Path.home() / ".hermes" /
   ".reticulum-gateway" / "storage"` and the pre-existing identity file is used,
   so the address does not change.
3. Given two adapters resolving the same storage path in one process, When the
   second starts, Then it raises `IdentityInUse` naming both profiles and the
   path, and the recovery layer schedules no retry.
4. Given `gateway status` after a conflict, When it is read, Then the profile is
   listed as a configuration conflict with the remedies, not a transport error.
5. Given a profile with an explicit `RETICULUM_STORAGE_PATH`, When it starts,
   Then the explicit path wins over the per-profile default.
6. Given two adapters in one process, When both start, Then each keeps its own
   propagation setting from its own profile and neither disables the other's.
7. Given a second adapter whose `rns_config_path` or `require_shared_instance`
   differs from the running instance, When it starts, Then it raises
   `RnsInstanceConflict` naming the owning profile and both settings, no retry is
   scheduled, and `gateway status` reports a configuration conflict.
8. Given any start, When the Reticulum adapter comes up, Then one INFO line
   states the instance owner and settings (`config`, `shared`, `owner`).
9. Given a non-default profile whose previous storage path was the shared
   default, When its gateway starts after the upgrade, Then one WARNING names
   the old path and the new storage directory, states the copy-the-directory and
   restart steps (ratchets included), and nothing is moved or deleted for it.

**Status:** open, proposed 2026-11-19, rulings landed same day. D1: keep the
`RETICULUM_DISPLAY_NAME` enablement gate (a new mesh profile opts in with one
line). D2: refuse a conflicting RNS instance setting with `RnsInstanceConflict`
and an INFO line naming the owner; a profile that needs its own RNS instance
runs its own gateway process. Peer also ruled that sharing `rnsd` and the
interfaces is fine, so the card only has to make the identities distinct.
Ready to build once the two-profile harness exists.

## Card A19: License close-out — exact pins, terms where users meet them

**Build:** Finish what ADR-0002 decided. `pyproject.toml` declares `rns` and
`lxmf` as ranges; the ADR ruled exact pins — "a range can resolve to a version
whose source nobody here has read" — and spec §11 completes the thought: "the
same exposure the vendoring argument rejects." The delta was never applied.
Pin both `==` the uv.lock resolved pair — at proposal time rns 1.6.1 /
lxmf 1.3.1 — a self-consistent pair, since lxmf 1.3.1 declares `rns>=1.6.1`
in its own PyPI metadata (checked 2026-11-21), so the exact pins cannot
contradict the dependency. Run the suite in both gateway modes at that pair,
and record the pair in the card status line so the release note names it. Then put the
Reticulum License where a user meets it. The README License section says only
"MIT"; the wheels carry `License: Reticulum License` in metadata with no
conditions text; upstream the terms live in each repo's LICENSE file and the
manual license page, and in the header of most RNS source files (77 of the
105 .py files the 1.6.1 wheel installs) but almost none of LXMF's (1 of the
10 files its 1.3.1 wheel installs) — measured in the live gateway venv,
2026-11-21 — and nowhere a downstream install meets them. (RNS also carries
third-party parts under other licenses: `vendor/` (i2plib among them),
pure25519, and `Interfaces/util/netinfo.py` under a 2014 Stefan C. Mueller
MIT header.) The license
is MIT-shaped with three conditions: no use in "any kind of system which
includes amongst its functions the ability to purposefully do harm to human
beings", no use in creating AI/ML training datasets, and the copyright and
permission notice travels with any copy. Canonical source:
https://reticulum.network/manual/license.html. Add a third-party
notice to the README quoting them, and make `install.sh` print the same notice
before its `pip install` step — on both the default-venv path and
`--venv PATH` (which installs this package, and with it rns/lxmf, into a
chosen existing venv rather than creating one).
**Spec basis:** `docs/adr/0002-pin-rns-lxmf-not-vendor.md` (Accepted) and the
licensing half of spec §11; the instance-sharing half of §11 covers why one
rnsd serves several adapters. The ADR's own "spec §14" implementation-delta
pointer is a misreference — §14 is error taxonomy — and the un-carded list in
this file kept the pin parked under the same wrong pointer until this card;
both corrected above.
**License posture, honestly stated:** the Reticulum License grants use subject
to its conditions; no clause names "dependency" or "transport" use as a
permitted category. Our reading — ours, not an upstream blessing — is that
installing rns/lxmf from PyPI at setup time and running them as the mesh
transport sits inside the grant, while vendoring their source into a
crawler-visible public repository is the exposure the dataset clause aims at.
Upstream does warn — but not where a pip install meets it: both PyPI
descriptions in the pinned pair (checked 2026-11-21) call the copyright and
license assertions that machine/LLM-generated derivatives of the projects
make "void grants", and warn that "all downstream projects using the
infringing derivatives, are directly and personally liable for the legal
consequences". The package metadata carries a single `License: Reticulum
License` field and no conditions text — the warning itself only rides along
in the embedded long description, which pip never prints. Nothing at
install time shows a user the conditions, which is why install-time
disclosure is part of this card rather than optional polish.
**State on this box (checked 2026-11-21):** the supported gateway path
imports RNS from the venv named in `~/.hermes/plugins/reticulum/venv_path.txt`
(Projects/rns_hermes_endpoint_private/venv), which already runs rns 1.6.1 and
lxmf 1.3.1 — the pin codifies the live state rather than changing it, and no
rns 1.5.4 exists on this box. The same fact cuts the other way: that venv is
the live adapter's import root, so an `install.sh` re-run here touches the
running gateway. Do not re-run install.sh on this box until the pinned pair
has had its live check on the RNode.
**Blocked by:** nothing.
**Acceptance:**
1. `uv lock --check` passes and a test asserts pyproject's pinned rns and
   lxmf versions equal the uv.lock resolved pair; no range specifiers left.
2. Suite run in real-base and CI-stub modes at the pinned pair. Name the two
   known reds so review does not chase them:
   `test_core.py::TestBridgeLiveness::test_sd_notify_sends_to_socket` and
   `test_tool_emoji.py::TestParityWithInstalledGateway::test_table_has_no_undeclared_extras`
   — both reproduce at dev in the same environment (measured 2026-11-21,
   2 failed / 647 passed / 4 skipped); any other failure must be proven
   pre-existing at base per the house rule.
3. README License section and install.sh both contain the dataset-clause
   sentence verbatim ("shall not be used, directly or indirectly, in the
   creation of an artificial intelligence…") and the license URL — asserted
   by a test that greps both files, not by review prose.
4. Still no vendored RNS/LXMF at the merge commit: `git grep -l -E 'This file
   is part of RNS|^# Reticulum License' -- ':!docs'` returns nothing, and
   `git ls-files | { grep -icE '(^|/)(rns|lxmf)[-/_.]|\.(whl|tar\.gz)$' || true; }`
   stays 0 — one net for vendored dirs, renamed modules and wheel/sdist
   archives, with the `|| true` keeping a zero count from reading as command
   failure.

**Status 2026-11-23:** merged in PR #5 (squash `e75e6b0`). Built on `step/A19-license-pins-and-terms`. `pyproject.toml`
pins `rns==1.6.1` / `lxmf==1.3.1` (the uv.lock pair, unchanged by `uv lock`), and
`tests/test_license_posture.py` holds the mechanical claims — pins equal the lock,
the three condition sentences plus the manual URL appear in README's License section
and in `install.sh` ahead of the install branch, the notice orders ahead of both pip
commands, and both vendoring nets stay empty. Measured at head with `TMPDIR=/tmp`:
real-base 660 passed / 4 skipped / 1 failed (the carded `test_tool_emoji` parity
red); CI-stub 656 passed / 9 skipped / 0 failed against base's 644 / 9 / 0, so the
delta is exactly the new tests. Against the card's acceptance 2, the second carded
red (`test_sd_notify_sends_to_socket`) only fails when `TMPDIR` is this profile's
long scratch dir — an AF_UNIX path-length artifact, not the tree — so the card's
"2 failed" line describes that environment. The stub recipe needs a writable empty
`HOME` because the transport tests pass a `storage_path=` kwarg that
`ReticulumTransport.__init__` drops (`plugin/transport.py:168-172`), so the default
expands from `HOME`; with `/nonexistent` that is 28 failures in
`test_reticulum_transport.py` at both base and head — a test defect to card, not an
effect of the pins. Evidence rows: `verification.md` § Card A19.

## Card A20: `ReticulumTransport` drops its own `storage_path` argument

**Build:** `__init__` accepts `storage_path` (`plugin/transport.py:142`) and never uses it:
`self.storage_path` is built from `extra`, then the scoped env reader, then the
`~/.hermes/.reticulum-gateway/storage` default (`plugin/transport.py:168-172`). A caller who
passes the kwarg believes storage is isolated and it is not. Production is unaffected — the
adapter passes settings through `extra` (seeded by `registration.py`) — but every transport
test that calls `.start()` with the kwarg (nine call sites in
`tests/test_reticulum_transport.py`: 357, 489, 610, 665, 798, 805, 820, 833, 843;
coordinates as of dev@`ba5b5c2`, where every line reference in this card was measured)
actually runs `os.makedirs` (`plugin/transport.py:266`) against the default path
expanded from the developer's `HOME`. That makes suite results depend on the environment: measured 2026-11-25
at dev@`ba5b5c2`, the documented CI-stub recipe with `HOME=/nonexistent` gives 28 failed /
27 passed in that file — `PermissionError` from `makedirs` under `/nonexistent` — while a
writable `HOME` is green. A19 spent two review rounds disagreeing over whether those numbers
reproduce (recorded in its verification row 2) before the A19 build filed this as GitHub
issue #6 and stayed out of scope.
**Spec basis:** none — API honesty and test honesty; same defect class as A15/A16, a green
suite not doing what it claims, and it poisons the CI-stub evidence mode every card leans on.
**Ruling:** the fold (option 1 from issue #6), chain order `extra` > kwarg > scoped env >
default: it keeps the documented constructor API, makes the nine existing test call sites
mean what they say, and leaves `extra` authoritative, so production cannot be surprised.
The sibling named parameters (`display_name`, `announce_interval_min`, `rns_config_path`)
are still accepted and dropped — same defect class, filed as issue #8 rather than widened
into this card. Senior review (2026-11-25, first pass) ruled to keep the fold: deletion
would rewrite the nine call sites and silently drop an advertised argument to buy
consistency with no behavioral gain.
**Acceptance:**
- Given `ReticulumTransport(storage_path=X)`, Then `self.storage_path == expand_path(X)` —
  the argument reaching the attribute is pinned by a test, not just demonstrated in the diff.
- Given the whole chain: `extra`'s `storage_path` beats the kwarg, and the kwarg beats the
  scoped env variable — chain order pinned by a test, since the fix defines it.
- Given a transport built with an isolated storage path and a monkeypatched `HOME`, when it
  `.start()`s, Then nothing is created under that `HOME` — red before (the makedirs hits the
  default under the fake home), green after.
- Given the CI-stub with `HOME=/nonexistent`, when the transport test file runs at head,
  Then 0 failures (base: 28). The full suite in real-base and CI-stub modes matches the base
  run except for this card's new tests.
- The comment at `tests/test_reticulum_transport.py:734` ("the `storage_path` kwarg is legacy
  and ignored") states the defect as fact — it becomes false with the fix and is corrected
  here, not left behind.
**Status:** merged 2026-11-25 (PR #7, squash c1dec26 into dev). Senior GO 4/5 on the
first pass with docs/test-hygiene conditions, GO 5/5 confirming they were applied;
greptile 5/5 with its one thread auto-resolved. Carded 2026-11-25 from GitHub issue #6
(owner directed the same day). Branch `step/A20-transport-storage-path` (deleted on
merge). Evidence rows: `verification.md` § Card A20.
## Card A21: `install.sh` installs into one hardcoded Hermes home, so every extra profile drifts

Source: live switch-over of the supported gateway path to the release checkout on
2026-11-26 (`install.sh --no-service` reported success; the second profile kept running the
retired bridge's code — see "Evidence of the gap").

**Build:** Thread the Hermes home through `install.sh` — a flag surface plus `HERMES_HOME` resolution —
so the shim copy, both `venv_path.txt` roots, the `mesh-tool-gate` copy, the config
enablement and the next-steps output land in the home the operator names, instead of only
`${HOME}/.hermes`. This means:

- `--hermes-home PATH` / `HERMES_HOME` resolution (D-1) feeding the plugin-dir, config and
  marker write paths — including `:205`'s `mkdir -p "${HOME}/.hermes"`, which must follow
  the selected home or be conditional, or a profile install manufactures the default home
  it was told not to touch;
- a new read-only `--check` subcommand: the drift report of D-4 (the one place the card
  adds a subcommand, ruled in deliberately);
- the service step is skipped when the target home is not the default one (D-6) — the
  archived bridge unit must not be enabled against a profile;
- `--global` mode currently writes `$VENV_DIR` into the marker (`install.sh:181`) although
  that mode never creates it (`:109-112` is not on that path), which under D-4(a) would
  make every `--global` home permanently red; the marker write lists only roots the run
  actually used, conditioned on `INSTALL_MODE == venv` — not on path existence: the default
  `${SCRIPT_DIR}/venv` (:22) exists on any box that ever took a default install, so an
  `-d` test would list a venv the global run never used, and the shim would prepend it
  (`__init__.py:101-106` — the A22 shadowing class) while `--check` reads green. The script
  already computes the right value (`BRIDGE_VENV=""` for global, `:338-344`);
- `--check` must branch out before the A19 license notice (`:86-102`) and the install
  branch, or every check prints twenty licensing lines; A19's ordering test pins the
  notice ahead of the `INSTALL_MODE` branch (`verification.md:256`), so the placement is
  chosen against that test, not guessed;
- the script's own help (`:6-15` usage banner, `:46-53` `--help` block) gains
  `--hermes-home` and `--check` — an undocumented flag surface is this card's defect in
  miniature;
- the next-steps text (`install.sh:455-456`): today it directs the operator to put
  `RETICULUM_*` and `HERMES_RETICULUM_*` in `~/.hermes/.env`, and for a profile install
  that is the wrong home — the profile's adapter hydrates secret sources from the profile
  home (`run_adapters.py:1095`), so an allowlist written by the installer's own
  instructions lands where nothing reads it and deny-by-default
  (`_shared.platform_gate_env`, `spec.md:238`) silently rejects every peer. One echo line;
  the same silent-drift failure this card exists to kill.

Not retargeted, and stated so the acceptance guard can name them: `${HOME}/.reticulum`
(mkdir + example config copy, `install.sh:138-147`) and `${HOME}/.lxmf/storage` (`:152`)
are OS-home RNS/LXMF directories, not Hermes-home artifacts — they belong to the shared
rnsd this repo's README supports. (A17's per-profile storage is a different path,
`$HERMES_HOME/.reticulum-gateway/storage`; no conflict.) The `${SCRIPT_DIR}/.env`
interaction (`:156-161`) stays checkout-scoped.

**Spec basis:** No spec section prescribes the installer's target home — checked, not assumed: §16 is the
adapter-invariant list (`spec.md:598-610`) and §17.5 is the env-vs-YAML config gap
(`spec.md:662-669`); neither is about install targets. The closest spec fact is §16's
invariant **"Profile-scoped env reads fail closed and never fall back to `os.environ`
under multiplex"** (`spec.md:604`) — profile isolation is spec'd behavior, and that is the
design pressure behind this fix.

The basis is therefore an observation with code citations, not spec prose: Hermes resolves
its home as context override → `HERMES_HOME` → platform default
(`hermes_constants.py:207`); a named profile's home is `<root>/profiles/<name>`
(`named_profile_home`, `hermes_constants.py:358`; the profiles-root check is
`_is_hermes_profiles_root`, `:336`); directory plugins are discovered per home at
`$HERMES_HOME/plugins/` (`plugin_loader.py:82-88` — `def` at :82, and it returns `None`
when the directory is missing, which is why the installer's `mkdir -p` at `:168` matters);
and the multiplexed gateway discovers plugins inside each profile's own runtime scope
(`gateway/run_adapters.py:1099-1102`; the dispatcher is `_start_secondary_profiles` at
`gateway/run_startup.py:1407`, comment at `:1416`). The shim's own contract is already
home-agnostic (`plugins/reticulum/__init__.py:41-42` resolves `venv_path.txt` from
`__file__`, `_roots()` `:45-62` reads the roots). Only the installer hardcodes the
destination. (Citation convention, as elsewhere in `tasks.md`: `install.sh`,
`plugins/reticulum/…`, `.gitignore` are this repo; `hermes_constants.py`,
`plugins/plugin_loader.py`, `gateway/…` are the Hermes core installation.)
Whether any of this should become a spec statement is the operator's call, not this
card's — it authors none.

**Blocked by:** Nothing hard. Sequencing: **A6 (open)** edits the same config-editor block
(`install.sh:215-326` — three write paths; five `raise SystemExit(0)` sites at `:254`,
`:267`, `:276`, `:284`, `:303`, of which `:276`, `:284` and `:303` write nothing; the
third write path at `:318-319` has no early exit), so whichever card lands second rebases
onto the other's branch conditions. A19's install-time notice text (`install.sh`, ahead of
the install branch) is untouched by this card. (A6's own cited install.sh line numbers are
stale against the current file; the rebase fixes A6's numbers rather than inheriting them.)

**Evidence of the gap:** Observed on this box, 2026-11-26 (PII rule respected: no paths outside `~/`, no hashes, no
hostnames, no session ids):

- `install.sh --no-service` from the release checkout finished green and rewrote the default
  home's shim (`~/.hermes/plugins/reticulum/`) with both roots naming the release checkout.
- A second profile's shim (`~/.hermes/profiles/<profile>/plugins/reticulum/`) still named the
  retired bridge checkout's `venv` and `src`, with a stale shim body one revision behind
  (no `reversed(_roots())`, no deps-path comment) and a stale `mesh-tool-gate`.
- Consequence: while the default profile connected on the release code, the second profile
  connected on the retired code — the gateway logged a happy `✓ reticulum connected
  (profile: …)` line for both. Nothing in the install output, the gateway log, or `hermes
  status` says "this home was not installed".
- Repair was by hand (copy the two plugin directories, rewrite `venv_path.txt`, restart).
  That hand-repair is the procedure this card replaces.
- Installer side, same session: `install.sh:167`
  `HERMES_PLUGINS_DIR="${HOME}/.hermes/plugins"`; `:204`
  `HERMES_CONFIG="${HOME}/.hermes/config.yaml"`; `:205` `mkdir -p "${HOME}/.hermes"`; `:456`
  the next-steps text names `~/.hermes/.env`. No branch anywhere in `install.sh` consults
  `HERMES_HOME`.

**Ruled:** recommended by peer, adopted by operator 2026-11-26; senior fifth-pass review
**GO 4/5** (2026-11-27).

D-1. **Flag surface.** `--hermes-home PATH` plus honouring `HERMES_HOME`; precedence
flag > env > default. No `--profile` sugar — resolving `<root>/profiles/<name>` in bash
duplicates `hermes_constants` root logic and invites drift. The documented profile
invocation is `--hermes-home <root>/profiles/<name>`. **Nonexistent target refused:** when
a *named* home (flag or env) does not exist, the installer refuses by name and exits
nonzero — `mkdir -p` for a named home blesses a typo as a green install no gateway ever
reads, the exact failure class this card exists to kill. Only a **resolved** default home —
the path equals `${HOME}/.hermes` whether the flag, the env or neither selected it, the same
resolved-value test D-6 uses — keeps today's create-on-install semantics (`:205`): on a fresh
box `HERMES_HOME=${HOME}/.hermes` creates the home (the default, not a typo), while
`HERMES_HOME=${HOME}/.hermes2` is refused. An existing but empty named directory is
accepted (a deliberately new home). **Profiles-root predicate, pinned:** the refusal
applies when the *named target itself* is a directory named `profiles` whose parent is a
Hermes root under the single root predicate used across this card (`_is_hermes_profiles_root`,
`hermes_constants.py:336-355` — marker files, a root named `.hermes`, the default root, or a
`profiles/.deleted` tombstone) — so `--hermes-home <root>/profiles` cannot install into the root. An ordinary home that
merely *contains* a `profiles/` subdirectory (every multiplex root home, including
today's default) is accepted; without this pinning, D-2's own default-home path would be
refused by its own clause.

D-2. **Default stays `${HOME}/.hermes`.** No flag, no env: byte-identical to today.

D-3. **One venv, many homes.** Statement-only — like D-8, it constrains design, not a
row. A home may name a shared venv + `src/`; the per-home artifact is the plugin
directory, not the interpreter. Coupling stated: a shared venv must satisfy every home's
gateway interpreter — which is exactly why the sibling finding (A22) matters.
One operational sequencing caveat: a second home installed against the shared venv re-runs
`pip install -e` into the venv the *first* home is live-importing (`install.sh:114-116`) —
A19's fence applies across homes, so order installs with that in mind.

D-4. **Sweep or single target.** Install stays single-target. `--check` is ruled in as a
**read-only** drift report with its own target: `--check [ROOT]`, `ROOT` defaulting to
`${HERMES_HOME:-${HOME}/.hermes}` — the *root* (a home plus its `profiles/`); honouring
`HERMES_HOME` matters because README.md:118 documents exactly that env for a dedicated
profile, and a check that ignored it would scan an unrelated root and exit green.
**`--check` combined with `--hermes-home` is refused by name** — the flag names a home to
write; letting it half-name a scan root while D-4's stated semantics ignore it would scan
homes the operator never asked about, possibly green. `--check` validates its ROOT: a
nonexistent ROOT exits nonzero naming it; a ROOT that exists but is no Hermes root under
the card's single root predicate (D-1: marker files, a root named `.hermes`, the default
root, or a `profiles/.deleted` tombstone) is reported
"not a Hermes root" and exits nonzero — a typo'd ROOT scanning nothing and printing the
not-installed line is the same false green, moved from the write path to the read path.
Limitations are printed in the check's own output — first: a home installed *outside* any
root (D-1 permits `--hermes-home <tmp>/alt`) is invisible to `--check` unless named as ROOT.
`--check` walks
`ROOT/plugins/reticulum/venv_path.txt` and every
`ROOT/profiles/*/plugins/reticulum/venv_path.txt` — dot-directories excluded, since
`profiles/.deleted` is a real directory (`hermes_constants.py:331`) and a tombstone
printing as a not-installed home would confuse the check's own output — and reports:
(a) a home whose marker names root paths that do not exist — drift, exit nonzero (the
Build's `--global` marker conditional is what keeps such a home from being permanently
red for the wrong reason);
(b) a home with no `plugins/reticulum/` directory at all — **not** drift: an informational
"adapter not installed here" line, exit 0, or every fresh box is permanently red;
(c) a home with `plugins/reticulum/` present but **no** `venv_path.txt` — the shim's own
contract needs the marker (`__init__.py:41-42` resolves it from `__file__`), so this is a
broken install: report it, exit nonzero.
No "foreign checkout" detection — there is no registry of legitimate checkouts, so
existence is the whole check. The other two limitations, also printed in the check's own
output:
when `HERMES_RETICULUM_VENV` (`plugins/reticulum/__init__.py:49-51`) overrides the marker
at runtime, the check cannot see that; and the marker attests to roots, not to the age of
the copied `mesh-tool-gate` — a fresh marker beside a stale gate reads green (a gate
`plugin.yaml` version compare is available — `src/hermes_reticulum/mesh-tool-gate/plugin.yaml`
carries `version: "0.3.0"` — but comparing copied-tree versions is A21-adjacent scope;
this card prints the limitation instead). Sweeping **writes** are out:
a re-run rewrites the venv the running gateway imports from (`install.sh:114-116`; A19's
note, `tasks.md:911-914`: the checkout venv "is the live adapter's import root, so an
`install.sh` re-run here touches the running gateway"), and the documented consequence of
the restart that follows is in our own docs — "A restart drops the live gateway session
(in-flight chats end); pick the moment" (`docs/skills/updating-the-adapter.md:71`).
Operators run installs outside live turns; this goes in the docs (D-7).

D-5. **Config-editor parity.** Same two writes per home (`plugins.enabled`,
`approvals.timeout: 900`), same "existing user value is left alone" convention. The editor
block is *already* parameterised on the config path — `:215` passes `"$HERMES_CONFIG"` as
`argv[1]`, `:221` reads it — so A21's delta there is the two HOME-hardcoded lines `:204`
(config path) and `:205` (`mkdir -p "${HOME}/.hermes"`, which must follow the selected
home or a profile install manufactures the default home), nothing more; the `:198-200`
comment above the heredoc names the old config path literally and should be updated with
the two lines. Joining the early-exit branches is A6's work, not this card's. A6's prompt stays
A6's.

D-6. **Archived bridge unit.** The rendered unit goes to `${HOME}/.config/systemd/user/`
(`install.sh:378-379`) — the OS home, never a Hermes home — so the live question is what
`--hermes-home <profile> --service` does, and as written today it would render and enable
the archived unit anyway. Ruled: when the resolved target home ≠ `${HOME}/.hermes`
("non-default" means the resolved value, not merely the flag's presence — which also
catches an operator who followed README.md:118 and exported `HERMES_HOME`), the installer
**skips the service step, `--service` given or not**, and prints the skip line naming the
reason (the bridge is archived and must not be enabled against a profile's mesh identity)
in auto mode too — silently dropping an explicit flag, or silently shipping a different
artifact set because of an env var, is the same silence this card kills.

D-7. **Docs wording.** README / QUICKSTART keep `~/.hermes` as the primary example and gain
a short "installing into a profile" section showing the flag. The docs that teach operators
where to look name the default home today — `docs/skills/updating-the-adapter.md:56` and
`:83` carry the marker path literally (`:10-11` split it across lines), `README.md:139`
names the default-home plugin dir, `QUICKSTART.md:42` states the installer installs into
`~/.hermes/plugins/` and edits `~/.hermes/config.yaml`, and `QUICKSTART.md:154`'s remedy
says "re-run `install.sh`" without naming a home. They gain "the
`plugins/reticulum/venv_path.txt` under the home you installed into (check with
`install.sh --check`)"-style wording and the remedy names the home. README.md:118 is
itself ambiguous between `HERMES_HOME` naming the multiplex root and naming one profile's
home — the docs pass disambiguates it, or an operator installs into the root and wonders
why the profile is still stale.
Plus one operator caution, lead first with the mutation and second with the restart: a
re-run while the gateway is live rewrites the venv the live adapter is importing
(`install.sh:114-116`; A19's fence, `tasks.md:911-914`), and the restart that follows ends
the live session — "A restart drops the live gateway session (in-flight chats end); pick
the moment" (`docs/skills/updating-the-adapter.md:71`). Do not assert in the docs what a
mesh peer sees beyond that: an interrupted turn leaves a fenced session, freed when its
300-second turn lease expires or its holder is found dead (`persistence/authority/coordination.py:477`; TTL default `agent/runtime_session_store.py:362`) and the
peer-visible path is A12/A14 territory — verify before shipping a stronger claim. Run
installs and `--check` outside live turns. A19's license notice text stays verbatim.

D-8. **Naming.** A21 lands alone; the interpreter finding becomes its own card (A22).
Tracker: both gaps are already filed upstream as strtPath/hermes_reticulum_platform issue
#11 — when these cards land, split #11 or reference-and-close it so the tracker does not
double-count. #11 also asks for `--profile` sugar, which D-1 explicitly declines; record
that in the split/close note so closing #11 does not read as ignoring the ask.

**Acceptance:** Each row re-runnable by a second party. **Every installer row** runs against a temp
`HOME` (never a real one) and passes `--venv <tmp>/venv --no-service`. Exactly two
exceptions, each stated in its own row: **row 10** passes `--service` instead of
`--no-service` and still passes `--venv <tmp>/venv`; it additionally stubs `systemctl`
on `PATH` (`systemctl --user` reaches the operator's real user manager regardless of
`HOME` redirection — `install.sh:373-374`, `:422-432` — so an unguarded second-party run
mutates the live gateway and can re-enable the deliberately archived bridge unit).
**Row 12** tests `--global` mode and therefore takes **no** `--venv` flag (`--venv` sets
`INSTALL_MODE=venv` at `:29-33` and `--global` sets it back at `:34-37`; a run carrying
both is order-dependent and tests the wrong mode) — it instead stubs `pip` on `PATH`, or
the run reaches `:128`'s real `pip install --break-system-packages -e "$SCRIPT_DIR"`.
Anything about the **default** venv location is read statically from `:22`/`:110`, never
executed (`pip install -e` into `${SCRIPT_DIR}/venv` is this box's live import root —
A19's fence, `tasks.md:911-914`).

1. Given a temp root `<tmp>/.hermes/` with `plugins/` already created (so an untouched-directory
   assertion proves something about a directory that existed) and `profiles/p2/config.yaml`
   whose `plugins.enabled` is empty, When `install.sh --no-service --hermes-home
   <tmp>/.hermes/profiles/p2 --venv <tmp>/venv` runs, Then under that profile home exactly:
   `plugins/reticulum/` equal to the checkout's **minus** `venv_path.txt`
   (`diff -r --exclude=venv_path.txt --exclude=__pycache__` — the marker is written into the
   destination at `install.sh:180-183` and is gitignored (`.gitignore:57`), `cp -r` copies
   any `__pycache__` (`:177`), so whole-directory byte identity is unachievable; assert the
   marker separately: two lines, `<tmp>/venv` then `<checkout>/src`);
   `plugins/mesh-tool-gate/`; and `<tmp>/.hermes/plugins/` — the default home's plugin
   directory, a *different* home from the target — untouched (before/after `find -newer`
   snapshot).
2. Given the same temp home, When the run finishes, Then that home's `config.yaml` parses as
   YAML with exactly one top-level `plugins:` key, both plugin names in `plugins.enabled`, and
   `approvals.timeout: 900`.
3. Given a profile config that already sets `approvals.timeout: 300` and lists an unrelated
   plugin, When the run finishes, Then both survive **at the value level** (the
   `plugins:`-present path round-trips through `yaml.safe_dump` at `:318-319`, so comments
   and formatting do not survive by design — assert parsed values, not bytes), no duplicate
   top-level key appears, and the exit code is 0 (assert with system `python3`, not the venv's
   PyYAML — per the sibling finding that venv's C extensions can be foreign-ABI).
4. Given no `--hermes-home` flag and `HERMES_HOME` unset, When the run executes with `HOME` pointed at a temp
   dir (and `--venv <tmp>/venv --no-service` per the preamble), Then every **home-scoped**
   write (plugin dirs, markers, `config.yaml`) lands under `<tmp>/.hermes/…` with the same
   file set and content rules as today (D-2 back-compat). The default venv location is a
   **static read** of `:22`/`:110`, never an execution (`python -m venv ${SCRIPT_DIR}/venv`
   at `:109-112` and the `source` at `:114` run before any `pip` stub could matter); same
   for the `${SCRIPT_DIR}/.env` interaction (`:156-161`).
5. Given `HERMES_HOME=<tmp>/alt` (an existing dir), When the run executes with no
   `--hermes-home` flag, Then the target is `<tmp>/alt` — D-1 ruled flag > env > default,
   so the outcome is assertable, not disjunctive — and no `<tmp>/.hermes` path is created
   (the `:205` mkdir followed the selected home). And given `HERMES_HOME=${HOME}/.hermes`
   on a fresh box, the default home is created, not refused (D-1's resolved-value rule).
   And given `--hermes-home <tmp>/no-such-dir`, the run exits nonzero naming
   the path and creates nothing (D-1 refusal). And a home that merely contains a `profiles/`
   subdirectory is accepted (D-1 predicate pinned), while `--hermes-home <root>/profiles`
   under a Hermes root is refused by name. And given both `--hermes-home <tmp>/flag-home`
   and `HERMES_HOME=<tmp>/env-home` (both existing), the **flag** target is the one
   installed into (D-1 precedence exercised, not just named).
6. Given homes whose fixtures are each **built by a real install run into that home** (so a
   `config.yaml` exists and no fixture trips the "not a Hermes root" rule before any drift
   report), then **mutated per case**: one whose `plugins/reticulum/venv_path.txt` names a
   root that does not exist, When
   `--check` runs, Then it exits nonzero, names that home and the stale root, and writes
   nothing; a home with no `plugins/reticulum/` directory is reported as not-installed and
   does not make the exit code nonzero; a home with `plugins/reticulum/` but no marker is
   reported and exits nonzero (D-4(c)); `--check` with `HERMES_HOME` set and no `ROOT`
   argument scans the `HERMES_HOME` root (D-4 default); a nonexistent `ROOT` exits nonzero
   naming it; an existing `ROOT` failing the card's root predicate is
   reported "not a Hermes root" and exits nonzero; `--check --hermes-home <anything>` exits
   nonzero naming the combination; the output prints all three stated limitations —
   outside-ROOT invisibility, the `HERMES_RETICULUM_VENV` override, and marker-attests-roots-
   not-gate-age; the `profiles/.deleted` tombstone produces no output line (D-4 walk).
   Remediation is a separate install run naming that home.
7. Given the new tests, When the CI pytest job runs, Then they pass in a `HOME`-redirected
   fixture — and fail if `install.sh` writes outside the explicit allow-set: the selected
   home, `$SCRIPT_DIR` (default venv and `.env` are checkout-scoped, `:22`/`:110`/
   `:156-161`), the explicit `--venv` target, `${HOME}/.reticulum` and
   `${HOME}/.lxmf/storage` (`:138-147`, `:152` — OS-home RNS/LXMF artifacts, not
   Hermes-home artifacts, per Build). Not `${HOME}` wholesale: a stray write into the
   *default* home during a profile install must fail the guard it advertises. A guard that
   forbade the checkout paths would contradict the script under test. Note the guard's
   boundary honestly: `$SCRIPT_DIR` in the allow-set contains the default venv by design
   (home-scope proof, not a net), so row 7 is **not** the safety net for the venv-mutation
   class — the preamble's flag rules are. If the guard matrix includes a `--global` run, it
   stubs `pip` there per the preamble's row-12 exception.
8. Given a profile-home install, Then the next-steps output names the **selected** home —
   its `.env` pointer paths `<selected home>/.env`, not `~/.hermes/.env` (a grep for
   `/home/`/`/Users/` cannot catch that defect), and same for the `RETICULUM_*` guidance
   (`install.sh:455-456`).
9. Given the diff, When `python3 scripts/security-sweep.py --untracked` runs, Then exit 0;
   and the diff adds no literal absolute home path (`/home/…`, `/Users/…`) to the script —
   runtime echoes of `$VENV_DIR`/`$RETICULUM_PLUGIN_DST`/`$UNIT_DST`/`$HERMES_CONFIG`
   (`:107`, `:184`, `:253`, `:442`) are fine and expected.
10. Given `--hermes-home <tmp>/.hermes/profiles/p2 --service --venv <tmp>/venv` with
    `HOME=<tmp>` and `systemctl` stubbed on `PATH`, Then no unit file exists under
    `<tmp>/.config/systemd/` (any path), the stub was **never invoked**, and the output
    contains the named skip line (D-6; same skip line expected in auto mode — assert it
    there too). And the negative half: `--hermes-home ${HOME}/.hermes --service
    --venv <tmp>/venv` (the **resolved default** named via the flag) with the same stub →
    the stub **is** invoked and no skip line prints — D-6 keys the skip on the resolved
    path, not on flag presence.
11. Given the docs diff, Then README/QUICKSTART carry the "installing into a profile"
    section; `docs/skills/updating-the-adapter.md:56`/`:83` and the split path at `:10-11`
    no longer name only the default home; `README.md:139` and `QUICKSTART.md:42` are
    generalised; `QUICKSTART.md:154`'s remedy names the home; `README.md:118` is
    disambiguated (root vs one profile's home); and the live-turn caution leads with the
    venv mutation (`install.sh:114-116`), restart second, without unverified peer-visible
    claims (D-7).
12. Given `--global --no-service` with `HOME` redirected, **no `--venv` flag, and `pip`
    stubbed on `PATH`** (per the preamble exception; the stub records its args — without
    it, `:128` performs a real editable install outside HOME; `--no-service` stated here
    too because in `--global` the target is the resolved default, so D-6's skip cannot
    fire and an omitted flag reaches the real user manager at `:422-432`), Then the marker
    lists only the roots
    that mode actually used — `src`, no venv line (`INSTALL_MODE`-conditioned, `:338-344`
    model) — and `--check` on that home exits green; no mode other than venv can put a
    venv root in the marker.
13. Given `install.sh --help` and the usage banner, Then both list `--hermes-home` and
    `--check` (`:6-15`, `:46-53`).

**Out of scope:** - Per-profile mesh identity and default storage path (A17), ACL profile scoping (A11), the
  config editor's prompt behaviour (A6). The archived bridge's code is frozen; only its
  install-time enablement is fenced here (D-6).
- Anything about which checkout an operator *should* run; this card only removes the reason a
  home silently keeps an older one.
- A connect-time log line naming the plugin home / import root (`gateway/run_adapters.py:1384`
  already logs the profile name; one addition naming the import root would have made tonight
  self-diagnosing). Candidate follow-up card — the cheapest permanent fix for "nothing says
  this home wasn't installed", but not this card's scope.

**Sibling finding — not this card (candidate A22):** `install.sh:66` prefers `python3.13 python3.12 python3.11 python3` when creating the venv, so
the venv can be a different **minor** than the gateway interpreter. The shim then prepends that
venv's `site-packages` to the gateway's `sys.path` (`plugins/reticulum/__init__.py:99-106`,
`sys.path.insert` at `:105`), so
wrong-ABI C extensions in it shadow the gateway's own (`_yaml.cpython-311-*.so`,
`_cffi_backend.cpython-311-*.so` under a 3.14 gateway on this box — PyYAML degrades to its pure
parser, cffi fails after the shim is loaded). Observed 2026-11-26 as an install-time 3.11 venv
under a 3.14 gateway; rebuilding the venv on the gateway's interpreter fixed it.

Suggested shape if carded: choose the interpreter from the Hermes installation the target home
belongs to (or refuse a mismatch by name), assert the venv's `sys.version_info[:2]` equals the
gateway's in a test, and never prepend a foreign-ABI `site-packages`. Card it separately per the
no-scope-expansion rule.

**Status:** open, proposed 2026-11-26 — peer recommendation adopted by the operator; passed
senior review **GO 4/5** (fifth pass, 2026-11-27) with zero blocking issues and that pass's
eight non-blocking nits folded in; carded into `tasks.md` 2026-11-27 (this PR); build not
started. Line references to `tasks.md` inside this card are coordinates as of dev@`faa63b5`,
where they were measured. Evidence rows: `verification.md` § Card A21.


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
