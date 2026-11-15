<!-- Part of the adapter feature bundle: docs/specs/adapter/.
     One row per acceptance criterion per card. A card is not done until every
     row is filled with evidence a second party can re-run. The verifier (a
     separate agent or the reviewer) fills the Result and Gap columns; the
     implementer fills Evidence. Do not write "done" in a cell without a
     command, a file:line, or a test id.
-->

# Adapter verification

Requirement-by-requirement traceability. One section per card. Copy the card's
**Acceptance** items from `tasks.md` into rows here when the card opens, and fill the
row when the work is claimed done.

Rule: a failing or missing criterion is a spec or implementation defect, not something to
explain away. A row that cannot be tested is itself a finding about the acceptance list.

## Template (per card)

| # | Acceptance criterion (from tasks.md) | Evidence (command / test id / file:line) | Result | Gap |
|---|---|---|---|---|
| 1 |  |  |  |  |
| 2 |  |  |  |  |

Result values: `PASS`, `FAIL`, `BLOCKED`, `NOT-TESTED`. A card closes only when every row
is `PASS` or is explicitly accepted by the peer as `BLOCKED` with a reason.

---

## Card A1: Docs foundation

| # | Acceptance criterion | Evidence | Result | Gap |
|---|---|---|---|---|
| 1 | Public sweep is clean over tracked and untracked files | `python3 scripts/security-sweep.py --untracked` exits 0 (run at merge commit efef313) | PASS |  |
| 2 | Design spec and build cards are reviewable as a split set | `docs/specs/adapter/{spec,plan,tasks,verification}.md` exist; spec is §1-17 | PASS |  |
| 3 | The registration `_bool_conv` fix is present and covered | `src/hermes_reticulum/plugin/registration.py:32` `_bool_conv`, used at :40 for `RETICULUM_REQUIRE_SHARED_INSTANCE`; `tests/test_reticulum_registration_env.py` re-runs green | PASS |  |

Merged in PR #13 (squash `efef313`, base `dev`).

## Card A2: Context pin + step-POST noise

| # | Acceptance criterion | Evidence | Result | Gap |
|---|---|---|---|---|
| 1 | Overflow turns stop |  |  |  |
| 2 | Dead step-POST log noise stops |  |  |  |

## Card A3: Automated ACL rejection

Spec: `docs/specs/adapter/a3-spec.md` (the implementing ruling record).

| # | Acceptance criterion | Evidence | Result | Gap |
|---|---|---|---|---|
| 1 | Unauthorized peer gets `⛔ Access not authorized.` and nothing else | `test_a3_01_unlisted_peer_receives_canned_rejection` | pass | - |
| 2 | Rejection is a deterministic gate before the model (no prompt-injection path) | `test_a3_02_rejection_logs_never_carry_peer_message_text` | pass | - |
| 3 | Cooldown: repeat denies inside 300 s do not re-send; the reply returns after expiry | `test_a3_06_cooldown_suppresses_second_rejection_then_resumes` | pass | - |
| 4 | Closed default: unset/empty gate denies, allow-all only by explicit opt-in | `test_a3_03_unset_allowlist_denies_everyone_with_no_reply`, `test_a3_04_empty_allowlist_string_also_suppresses`, `test_a3_05_allow_all_admits_everyone_with_zero_rejections` | pass | - |
| 5 | Blocklist outranks allow-all; a blocklist deny is a full deny | `test_a3_08_blocklist_outranks_allowlist`, `test_a3_09_blocklist_deny_in_open_mode_is_full_deny` | pass | - |
| 6 | Malformed inbound is dropped before ACL evaluation (ordering rule) | `test_a3_10_malformed_inbound_stays_silent_drop` | pass | - |
| 7 | Cooldown storage is expiry-ordered and bounded by the window | `test_a3_12_cooldown_map_stays_bounded_after_cleanup_tick` | pass | - |
| 8 | Deny flood does not raise per-allowed-message cost | `test_a3_13_deny_flood_does_not_grow_allowed_message_cost` | pass | - |
| 9 | Dedicated cleanup task runs while the adapter is idle; cancelled on disconnect | `test_a3_14_cleanup_task_prunes_while_adapter_is_idle` | pass | - |
| 10 | Gateway deny desync: adapter logs the desync line and sends the canned reply | `test_a3_15_gateway_deny_of_admitted_peer_gets_canned_reply`, `test_a3_16_gateway_deny_respects_cooldown`, `test_a3_17_gateway_deny_in_empty_allowlist_boot_is_silent` | pass | - |

## Card A4: Command feedback + send truth (amended 2026-11-04)

Rows 1-3 are commands the gateway dispatches (see the card amendment); they need a phone
exercise, not adapter code. Row 4 is the only code change.

| # | Acceptance criterion | Evidence | Result | Gap |
|---|---|---|---|---|
| 1 | `/status` answered on the phone (reply packet observed peer-side) | gateway handler `gateway/slash_commands_status.py:242`; dispatch path common to every command | NOT-TESTED | no live mesh exercise yet |
| 2 | `/resume` answered on the phone; named session becomes current | gateway handler `gateway/slash_commands_session.py:1054` | NOT-TESTED | no live mesh exercise yet |
| 3 | `/new` + `yes` reply runs the reset | `_hm_slash_confirm_reply` wired at `gateway/run_inbound.py:1150`; `yes` in `approval.inputs.confirm_once` (`locales/en.yaml:23`) | NOT-TESTED | no live mesh exercise yet |
| 4 | Failed LXMF delivery reported failed and logged at WARNING naming the lost delivery |  | NOT-TESTED | code change not made yet: `src/hermes_reticulum/plugin/adapter.py` logs this at INFO |

Dropped from the card 2026-11-04: `mesh_terse()` for the confirm body. The adapter receives the
gateway's finished locale string and cannot re-render it without parsing a translated block;
measured cost of the gateway text is 288 chars, one LXMF packet. **Ruled the same day: leave the
Hermes default text** — Reticulum clients mostly render markdown, so the markers are not a
defect. See the card A4 amendment in `tasks.md`.

Row 3's `yes` reply is the one to watch in the live run: if the peer sends `/new` while a turn is
running, that is the busy-session inline-dispatch path where the base can leave
`_gateway_accepted` `False` — the false-rejection bug carded as A12 (2026-11-11; merged
2026-11-15, PR #40).

## Card A5: `/new` ack with session id + title — dropped 2026-11-04

No rows: the card was dropped by peer decision before implementation. The ack is composed
gateway-side (`gateway/slash_commands_session.py:242`, `:283`) and already carries the title
(`gateway.reset.header_titled`); the session id is reachable on demand from `/status`
(`gateway.status.session_id`). If the peer later wants the id in the ack itself, that is a
gateway card — see the card record in `tasks.md`.

## Card A6: Tool visibility + `/verbose` (amended 2026-11-04)

| # | Acceptance criterion | Evidence | Result | Gap |
|---|---|---|---|---|
| 1 | A 22-tool turn is no longer silent after the peer runs `/verbose` | gateway progress lane delivers through `adapter.send()` (`gateway/run_turn_progress.py:626`) | NOT-TESTED | tier ruled `low` 2026-11-04 (progress off by default, peer opts in); no `display_tier`/`display_overrides` declared yet in `registration.py:134-158` |
| 2 | `/verbose off` truncates like the CLI | `/verbose` is the gateway command over `display.platforms.<platform>.tool_progress` (`hermes_cli/commands.py:208`, `gateway/run_turn.py:872`) | NOT-TESTED | key ruled 2026-11-04 (ship `display.tool_progress_command` via `install.sh`); the installer check/prompt is not written yet. Double-gated on the same key: registry `gateway_config_gate` (`hermes_cli/commands.py:504-523`) and handler (`gateway/slash_commands.py:986`) |
| 3 | Tool emoji match the gateway's own labels | `tests/test_tool_emoji.py:139` `TestParityWithInstalledGateway` | NOT-TESTED | test exists; needs a green run recorded against the current gateway build |
| 4 | Mid-turn commentary still reaches the peer (LOW tier overridden back on) | `display_overrides` `interim_assistant_messages: True` over `_TIER_LOW`'s `False` (`gateway/display_config.py:45-48`); delivered per segment at `gateway/run_turn_runner.py:322-346` | NOT-TESTED | override not written yet; no live run |
| 5 | A fresh config gets the key only after the installer says yes | `install.sh` config write paths (`:221-249` append paths, `:253-307` YAML round-trip) | NOT-TESTED | not written yet; the non-interactive branch is ruled (skip, no key, print snippet) but unbuilt |
| 6 | An existing `display.tool_progress_command` is never overwritten and never re-asked | same three write paths; the `approvals.timeout` precedent leaves user values alone | NOT-TESTED | not written yet; the append paths must also test for an existing `display:` key or they duplicate it |
| 7 | A non-TTY install neither blocks nor enables the key | ruled 2026-11-04: no answer, no key; print the three-line YAML snippet | NOT-TESTED | not written yet; the Python block's stdin is the heredoc, so `isatty()` there is always false — the guard belongs in bash (`[ -t 0 ]`) and the prompt must read `/dev/tty` |

## Card A7: YAML config hook

| # | Acceptance criterion | Evidence | Result | Gap |
|---|---|---|---|---|
| 1 | `config.yaml` keys work without env vars |  |  |  |

## Card A8: Approvals 900s

| # | Acceptance criterion | Evidence | Result | Gap |
|---|---|---|---|---|
| 1 | Mesh approvals survive LoRa latency | Value ships in `plugins/reticulum/config.yaml` (`timeout: 900`); `install.sh` applies it to the user's `config.yaml` (fast + merge paths, existing value preserved); adapter tests green (`tests/test_reticulum_adapter.py`, 37 passed) | BLOCKED | Wire check on a live mesh (approval arriving >300s after the prompt) not yet run. Mechanism is in place and installer-verified; the end-to-end latency behaviour is unproven. |

Merged in PR #13 (squash `efef313`, base `dev`). The gateway reads `approvals.timeout` from
top-level config, which a plugin cannot set from its own subtree, so the value lands via
`install.sh`; see spec §17.6 and ADR-0003.

## Card A9: P-13 command audit — dropped 2026-11-04

No rows: the card was dropped by peer decision before implementation (reasoning in the card
record, `tasks.md`). P-5 `/retry` and P-6 adapter-built `/status` were already superseded by
gateway commands (`hermes_cli/commands.py:100`, `:170`). See `plan.md` §19.2a.

## Card A10: P-8 `/clarify` verification (amended 2026-11-04)

| # | Acceptance criterion | Evidence | Result | Gap |
|---|---|---|---|---|
| 1 | `/clarify` resolves on the mesh, or a findings doc proves the gateway gap | source read: `gateway/platforms/base.py:2830-2855` (numbered text + `mark_awaiting_text`), `gateway/run_turn_runner_clarify_delivery.py:29-39` (base path driven for a non-overriding adapter) | NOT-TESTED | source half answered; the live mesh exercise has not been run |

## Card A12: No false desync rejection on a busy-session inline dispatch

| # | Acceptance criterion | Evidence | Result | Gap |
|---|---|---|---|---|
| 1 | `/stop` mid-turn: stop ack only, no rejection, no desync line | `test_a12_01_inline_command_ack_gets_no_false_rejection[/stop]` | PASS | live confirmation outstanding |
| 2 | `/new` (or `/reset`) mid-turn + inline confirm answer: no desync, no rejection | `test_a12_01[/new]` (the command ack); the confirm-style bypass command on the REAL base is pinned by `test_a12_06` (`/approve`) | PASS | live confirmation outstanding |
| 3 | Pending clarify, text answer dispatched inline: no desync, no rejection | `test_a12_01[yes]` (stub pipeline; same code path as the other parametrized rows — see red-before note) plus `test_a12_06` (clarify answer routed into the real `_dispatch_inline_reply`) | PASS | live confirmation outstanding |
| 4 | Genuine gateway deny still logs the desync line and sends the canned reply (A3 criterion 10 intact) | `test_a12_04_real_gate_drop_still_denies`; the A3 D7 suite (`test_a3_15/16/17`) still green | PASS | - |
| 5 | Accepted inline dispatch whose send FAILED: no desync, no rejection (marker records the attempt) | `test_a12_02_inline_ack_send_failure_is_not_a_deny` | PASS | - |
| 6 | Accepted inline dispatch with the reply still in flight at return: no desync, no rejection | `test_a12_03_inline_reply_in_flight_at_return_is_not_a_deny` | PASS | - |
| 7 | CI stub extended to the busy-session path | `test_a12_05_ci_stub_busy_session_path` (stub mode; skipped against the real base); the conftest stub models the no-claim inline path, and the test self-guards: it records the flag at handler entry and asserts the handler was reached UNCLAIMED, so the shape it depends on cannot silently vanish. NOT red-before (pre-fix `dev`'s stub had no busy branch for it to be wrong about — review finding 4); it is a shape-preservation guard | PASS | - |
| 8 | The marker's assumption holds on the REAL base: its command/confirm/clarify inline paths enter this adapter's `send()` before returning | `test_a12_06_real_base_inline_dispatch_enters_send` (`/model`, `/approve`, clarify text; skipped in stub mode) — no seam replaces `handle_message`; busy is armed at the session-key lookup and the rest runs the real `_handle_message_while_active` → `_dispatch_inline_reply` → `_send_with_retry` → `send()` chain | PASS | - |
| 9 | A send routed to a DIFFERENT peer during the await must not silence a genuine deny | `test_a12_07_send_to_other_peer_does_not_mask_a_deny` — red under the pre-round-1 peer-blind read, green with the peer-matched read (mutation-verified on the branch, 2026-11-14) | PASS | - |
| 10 | A send from a turn already in flight (task created before this event's delivery) must not tick the marker | `test_a12_08_concurrent_turn_send_does_not_mask_a_deny` — red when the ContextVar is swapped for a plain instance attribute, green as shipped (mutation-verified) | PASS | - |
| 11 | The attempts marker is installed fresh around the await, reset after, never leaking into an outer marker | `test_a12_09_marker_installed_for_await_and_reset_after` — red under the same instance-attribute swap | PASS | - |

Red-before, recorded on `dev` at 2026-11-11: rows 1-3 and 5-6 fail — the inline ack is
followed by `⛔ Access not authorized.` and the `gateway desync` WARNING line, exactly the
live 2026-11-01 sequence. The five red cases are `test_a12_01` parametrized over three
command shapes plus rows 5-6: one inline-dispatch code path, deliberately parametrized over
the documented trigger shapes — a12_01 now asserts the parametrized text is what actually
reaches the dispatcher, so the parametrization cannot overstate coverage (review finding 5);
row 4 passes before and after, pinning criterion 10. Row 7 was recorded red-before in error —
it is green against pre-fix `dev` (the stub there had no busy branch at all); the round-1
self-guard closes that vacuity (review finding 4). Rows 9-11 are mutation-pinned on the
branch rather than dev-red, verified 2026-11-14: row 9 red under the peer-blind read, rows
10-11 red under a plain instance-attribute swap of the marker (the ContextVar is now
load-bearing *and* tested).
Green after in both modes: real base 79 passed / 1 skipped, CI stub 77 passed / 3 skipped.
Stub mode is forced with `env HOME=/nonexistent HERMES_GATEWAY_ROOT=/nonexistent
HERMES_VENV_PATH=/nonexistent` — unsetting one variable is not enough, the conftest also
falls back to the default Hermes checkout and venv. Full suite: 591 passed / 4 skipped; the
only failure (`test_tool_emoji` gateway-parity row) is pre-existing drift on `dev`, unrelated
to A12.
Discriminator: `deliver_inbound` installs a fresh attempts list into the `_SEND_ATTEMPTS`
ContextVar around the `handle_message` await; `send()` ticks it at entry with the
destination. A deny is read only when the flag is False AND the event's own peer is absent
from the attempts list. Handled-but-silent inline paths (no-text handler, swallowed inline
exception, queue-mode busy-text debounce at `base.py:4081-4085`) stay indistinguishable
from a deny — known residue, card A14.

## Card A13: `platform_hint` no longer forbids markdown

| # | Acceptance criterion | Evidence | Result | Gap |
|---|---|---|---|---|
| 1 | Nothing in the hint forbids markdown, and fenced code arrives intact through chunking | `_PLATFORM_HINT` (`src/hermes_reticulum/plugin/registration.py:120-127`); `test_platform_hint_no_longer_forbids_markdown`, `test_platform_hint_states_that_clients_render_markdown`; code integrity across chunks: `tests/test_reticulum_delivery.py:496` `test_code_block_preserved_across_chunk_boundary` | PASS | - |
| 2 | The bandwidth/concision instruction and the plain-text fallback caveat survive | `test_platform_hint_keeps_the_bandwidth_caveat`, `test_platform_hint_covers_clients_without_markdown_rendering` | PASS | - |
| 3 | A client known not to render markdown is recorded as a findings note when seen | `reticulum-mobile-app` (github.com/thatSFguy/reticulum-mobile-app): native Kotlin/Swift bubbles, no markdown rendering and no renderer-field support (repo code search for `FIELD_RENDERER`: 0 hits; evidence block in the note); recorded in `docs/mesh-bridge-findings-2026-11-11-nonrendering-client-reticulum-mobile.md` | PASS | - |

Red-before, recorded: the previous string ("Messages are plain text only — no markdown
formatting") fails rows 1 and 2 of the assertion set; only the bandwidth check passes.
