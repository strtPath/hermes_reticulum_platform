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
| 1 | `/status` answered on the phone (reply packet observed peer-side) | gateway handler `gateway/slash_commands_status.py:242`; dispatch path common to every command | NOT-TESTED | live run blocked: suspected client-side command interception — see the live-run note below |
| 2 | `/resume` answered on the phone; named session becomes current | gateway handler `gateway/slash_commands_session.py:1054` | NOT-TESTED | same live-run blocker as row 1 |
| 3 | `/new` + `yes` reply runs the reset | `_hm_slash_confirm_reply` wired at `gateway/run_inbound.py:1150`; `yes` in `approval.inputs.confirm_once` (`locales/en.yaml:23`) | NOT-TESTED | same live-run blocker as row 1 |
| 4 | Failed LXMF delivery reported failed and logged at WARNING naming the lost delivery | `test_a4_01_failed_callback_warns_naming_the_lost_delivery` (failed callback), `test_a4_02_failed_state_receipt_warns_naming_the_lost_delivery` (FAILED state receipt), `test_a4_03_delivered_receipt_stays_below_warning` (loss-only guard: DELIVERED stays below WARNING) | PASS | - |

Dropped from the card 2026-11-04: `mesh_terse()` for the confirm body. The adapter receives the
gateway's finished locale string and cannot re-render it without parsing a translated block;
measured cost of the gateway text is 288 chars, one LXMF packet. **Ruled the same day: leave the
Hermes default text** — Reticulum clients mostly render markdown, so the markers are not a
defect. See the card A4 amendment in `tasks.md`.

Row 3's `yes` reply is the one to watch in the live run: if the peer sends `/new` while a turn is
running, that is the busy-session inline-dispatch path where the base can leave
`_gateway_accepted` `False` — the false-rejection bug carded as A12 (2026-11-11; merged
2026-11-15, PR #40).

Red-before, recorded at the branch base (`dev` @ b321a24): row 4 fails — the failure callback
logged INFO, so a `caplog` filtered at WARNING saw nothing (a4_01/a4_02 red, a4_03 green).
The live surface is the transport's failed callback (`_handle_outbound_failure`); LXMF's
delivery callback fires only for DELIVERED/SENT/PAPER (checked against 1.3.0 and 1.3.1), so a
FAILED *state receipt* (`_handle_outbound_outcome`) is defensive depth, not a second observed
surface — an earlier draft of this card claimed two live surfaces. Both branches now log a
WARNING naming the lost delivery (destination + payload snippet); the DELIVERED path stays
below WARNING, pinned by `test_a4_03`. Post-merge senior review (2026-11-21) narrowed the
outcome branch: FAILED/REJECTED/CANCELLED get the loss WARNING, an unrecognised state (PAPER,
future states) logs INFO as what it is — a success must never be called "lost"
(`test_a4_04_unrecognised_state_is_info_not_a_loss`) — and the payload-attribution fallback in
the failure WARNING admits it is guessing (`test_a4_05_unattributed_failure_admits_the_guess`).
Green after in both modes (adapter + delivery files): real base 152 passed / 1 skipped,
CI-stub 150 passed / 3 skipped.

**Live-run note (2026-11-18, deferred by peer to address later):** the peer reports sending a
bare `/status` at 20:48 and seeing a reply — but the gateway logged **zero trace** of it: no
`[Reticulum] Sending command '/status' response` line (the log holds exactly two command lines
ever, both `/stop`, 2026-11-01 and 2026-11-10), no inbound, no send — while ordinary chat text
from the same client landed at 20:49:49. Chat-path delivery is intact, so the suspected shape is
**client-side slash-command interception**: the client answers `/status` locally and the packet
never reaches the mesh. A client-observed "reply" alone is therefore a false positive for rows
1-3. Re-run must use a client that does not intercept slash commands — or a payload the client
cannot fabricate, like `/resume`'s session listing — with the gateway
`Sending command` line as the corroborating trace.

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
| 1 | `config.yaml` keys work without env vars | Unit-level, both halves of the acceptance: `test_yaml_display_name_satisfies_the_enablement_gate` starts with no `RETICULUM_*` env, `_env_enablement()` closed, calls the hook with only a YAML block, and the gate opens with the configured name — the adapter would register. `test_seeded_extra_reaches_the_transport` builds `ReticulumTransport(extra=seeded)` from the hook's return alone: `display_name == "Mesh Relay"`, `announce_interval_min == 20.0`, so the configured name and cadence land on the transport that announces. The autouse `_clean_env` fixture guarantees no env leaks in | PASS | live row open: gateway start with YAML-only config not yet run on the box; deployment keeps the `.env` path |

Red-before, recorded at branch base dev@`91f97f0` with the new tests added and
`registration.py` untouched: 7 failed / 16 passed in the file — every new test
fails with `AttributeError` on `_YAML_BRIDGE`/`_apply_yaml_config`, and
`test_register_passes_the_yaml_hook` fails because the entry carries no
`apply_yaml_config_fn`. Green after: file 23/23 real-base; 22 passed + 1 skipped
CI-stub (the register test skips where `gateway.platform_registry` is
deliberately absent). Full suite: real-base 673 → 680 passed / 4 skipped, delta
exactly the 7 new tests, same single known `test_tool_emoji` parity red at base
and head. CI-stub (fresh writable `$HOME`, scrubbed env): 669 → 675 passed /
9 → 10 skipped. The stub `$HOME` gains only the known alive-writer and step-log
paths, no `reticulum-gateway` storage dir.

Test-honesty note: the conftest `_shared` stub gained a hand-written
`apply_yaml_bridge` mirror, and the mirror documents where it is deliberately
simpler than the real setter (no operator-source detection, no bridge markers,
no scoped loads under stub mode). Without the mirror, CI-stub mode would score
the hook green without running it — the A12/A15 lesson, applied pre-emptively.
D1 is pinned rather than assumed: `test_announce_interval_alone_does_not_enable_the_platform`
bridges a cadence key with no name and asserts the gate stays closed.

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

## Card A16: security-sweep blesses nonexistent paths

| # | Acceptance criterion | Evidence | Result | Gap |
|---|---|---|---|---|
| 1 | Nonexistent path argument exits nonzero naming the path, no "clean" line | `test_a16_01_nonexistent_path_is_an_error_not_a_pass` (exit 2, `error: no such file: <path>` on stderr, clean line absent) | PASS | - |
| 2 | Positional matching `a..b` is honored as a commit range, never scanned as a literal path | `test_a16_02_dot_range_positional_is_honored_as_commits` (bare `HEAD~2..HEAD` finds the value a later commit redacted, as `--commits` would); `RANGEISH.fullmatch` + not-exists guard keeps real dotted paths in path mode | PASS | - |
| 3 | Empty positional exits nonzero rather than reporting clean | `test_a16_03_empty_positional_is_not_reported_clean` (exit 2, "refusing to clean" on stderr) | PASS | - |
| 4 | Commit-time loop does not cry PII on staged deletions | `docs/PII-RULES.md` loop now `git diff --cached --name-only --diff-filter=d` with the rationale note | PASS | - |
| 5 | Docstring no longer claims `~/...` coverage the rule lacks | docstring homepath bullet states `~/...` is NOT scanned; rule (`HOMEPATH`) unchanged | PASS | - |

Same-class hardening beyond the card's bullets: `--staged` with a path absent from
the index exits 2 naming it (`test_a16_04_staged_mode_missing_index_path_is_an_error`);
a failing `git rev-list` for a routed range exits 2 with the git error instead of a
traceback, and a routed positional that resolves neither way says so in those words.
A directory argument fails as `not a file`; a bare `..` is a path error, not a range
(`test_a16_08_bare_dotdot_is_a_path_error_not_a_range`); a range and named paths
together scan both instead of silently dropping the paths
(`test_a16_07_range_and_path_together_scan_both`). The error contract the docs
advertise is pinned in the tests: exit 2, message on stderr. Path mode still scans
normally (`test_a16_05_existing_paths_still_scan_normally`). No caller depended on
the silent skip: every call site (`.githooks/pre-push`, `.github/workflows/ci.yml`)
uses no-args or explicit `--commits` form. The commit-deletion fix also reaches the
second copy of the loop, in `docs/skills/pii-check-before-commit-and-pr.md`.

Red-before, recorded at branch base dev@b321a24: `test_a16_01`-`test_a16_04` fail
(the sweep prints clean and exits 0 for each), `test_a16_05` green. Green after,
both modes: `tests/test_security_sweep.py` 15 passed; full suite at the final rebased
tree 646 passed / 4 skipped / 1 failed (`test_tool_emoji` parity - pre-existing on
dev, verified in the A4 pass, unrelated).
Post-merge follow-up (same day): the first push to main tripped the gate on its own
author metadata - 14 findings, all the numeric prefix of the repo's standardised
GitHub noreply address (`<digits>+<user>@users.noreply.github.com`) reading as
`shorthex`. That address is public author metadata, so the sweep now blanks the
whole address shape before pattern matching; bare digit runs stay caught
(`test_noreply_author_identity_is_not_a_finding`,
`test_same_digits_bare_in_a_message_are_still_caught`).

## Card A19: License close-out — exact pins, terms where users meet them

| # | Acceptance criterion | Evidence | Result | Gap |
|---|---|---|---|---|
| 1 | `uv lock --check` passes; a test asserts pyproject's pinned rns/lxmf equal the uv.lock pair; no range specifiers left | `uv lock --check` exits 0 at head; `test_pin_is_exact_not_a_range` (one case per dependency, fullmatch `rns==X.Y.Z`) and `test_pin_matches_uv_lock` read pyproject and uv.lock via `tomllib`, so both files are the oracle rather than a copied constant. `uv lock` regenerated only the root `requires-dist` metadata lines (2 insertions, 2 deletions); the resolved pair is unchanged at rns 1.6.1 / lxmf 1.3.1 | PASS | - |
| 2 | Suite run in real-base and CI-stub modes at the pinned pair, with the known reds named | Real-base at head, `TMPDIR=/tmp PYTHONPATH=$HOME/.hermes/hermes-agent/venv/lib/python3.11/site-packages .venv/bin/python -m pytest tests/ -q`: 660 passed / 4 skipped / 1 failed — the carded `test_tool_emoji.py::TestParityWithInstalledGateway::test_table_has_no_undeclared_extras` (the installed gateway registers browser_*/yb_* tools the bridge emoji table does not). `TMPDIR` matters and is recorded here because it bit a reviewer: with this profile's long scratch `TMPDIR`, `test_core.py::TestBridgeLiveness::test_sd_notify_sends_to_socket` also fails, on an AF_UNIX 108-byte `sun_path` limit, not on anything in the tree — so the card's "2 failed" line describes that long-TMPDIR environment, and the sd_notify test passes under `TMPDIR=/tmp`. CI-stub at head: `env -u PYTHONPATH HOME=/tmp/a19-home HERMES_GATEWAY_ROOT=/nonexistent HERMES_VENV_PATH=/nonexistent TMPDIR=/tmp .venv/bin/python -m pytest tests/ -q` → 656 passed / 9 skipped / 0 failed; the same command against a detached worktree of base `73b2ab5` → 644 passed / 9 skipped / 0 failed, so the delta is exactly this card's 12 new tests, all passing. `HOME` must be an existing writable empty directory. It is the one stub variable whose value changes results on this box, and the reason is a test defect rather than the pins: `tests/test_reticulum_transport.py` builds each transport with `storage_path=`, but `ReticulumTransport.__init__` reads only `extra`, the scoped env reader, and the `~/.hermes/.reticulum-gateway/storage` default (`plugin/transport.py:168-172`) — the kwarg is dropped, so the default expands from `HOME` and `plugin/transport.py:266` calls `os.makedirs("/nonexistent")`. Measured 2026-11-23: `HOME=/nonexistent` gives 28 failures in that file at both base and head, while `HOME=/tmp/a19-home` gives 656/9/0. The reviewer measured 0 failures under `/nonexistent`, so treat that variant as environment-dependent and use the writable-HOME command above; the dropped kwarg is worth its own card. (Mystery closed by Card A20: the reviewer's shell almost certainly exported `RETICULUM_STORAGE_PATH` — this box runs the adapter — which satisfies the chain so the default never expands; scrubbing `-u RETICULUM_STORAGE_PATH -u RETICULUM_DISPLAY_NAME` makes 28/27 reproduce exactly, and A20 makes the file immune to the variable at head.) | PASS | - |
| 3 | README License section and install.sh both contain the dataset-clause sentence verbatim and the license URL, asserted by a test that greps both files | `test_readme_states_the_conditions_verbatim` scopes to the `## License` section only; `test_installer_states_the_conditions_verbatim` greps the whole installer; both assert all three clauses (harm, dataset, notice) as single lines plus `https://reticulum.network/manual/license.html`, copied from the manual page rather than typed from memory. `test_installer_notice_precedes_the_install_branch` pins the notice ahead of the `INSTALL_MODE` branch line — the boundary that actually covers the default `./venv`, `--venv PATH` and `--global` paths; `test_installer_notice_precedes_the_pip_step` additionally orders it ahead of both pip commands | PASS | - |
| 4 | Still no vendored RNS/LXMF at the merge commit (content net and names net both empty) | At head the content net (`git grep` for `This file is part of RNS` or a line opening `# Reticulum License`) prints nothing outside the two card documents that quote the pattern, and the names net over `git ls-files` for vendored rns/lxmf dirs, wheel and sdist archives prints 0; history forms (`git log --remotes --name-only --pretty=format:`, `git rev-list --objects --remotes`) are also 0. Both are pinned as tests (`test_no_vendored_source_by_content`, `test_no_vendored_source_by_name`), which `git grep` for the content net and read `git ls-files` for the names net. The content net refuses to read as clean when the search itself fails: it accepts `git grep` exits 0 and 1 and raises with stderr on anything else (`test_content_net_reports_a_broken_search_instead_of_passing`), because empty stdout from a broken index would otherwise look like an empty tree. Two corrections came out of review: a comment block in `install.sh` that began a line with `# Reticulum License` matched the net, so the notice comment keeps that phrase mid-line; and the test's original `docs/` exclusion was narrowed to an explicit two-file `PATTERN_QUOTERS` set, so a blob dropped in any other doc would still be found | PASS | - |

Red-before at branch base dev@73b2ab5, measured with the final 12-case file: 9 failed /
3 passed (the two vendoring nets plus the broken-search guard pass at base by design;
the pin rows, the terms rows, the two pip-order rows and the branch-order row fail).
Green after: 12 passed.

The README's third-party paragraph and this row both had to be corrected against the
installed 1.6.1 wheel. First claim, wrong: `pure25519` was described as "only an
attribution string inside `Ed25519.py`" — it is a five-module vendored package at
`RNS/Cryptography/pure25519/` (MIT, 2015 Brian Warner and other contributors),
shipped in the wheel and imported at `Ed25519.py:32`, so the README now names it.
Second claim, wrong: the README pointed at "the upstream LICENSE files in the
installed packages" — neither wheel ships one (`find` for `*licen*` under `RNS/`,
`LXMF/` and both dist-info directories returns nothing; the metadata carries only
`License: Reticulum License`). The README now says where the terms actually reach an
installed copy: the per-file headers (`RNS/__init__.py`, `LXMF/Utilities/lxmd.py`),
that metadata field, and the credits list in the RNS package description on PyPI.
Third, smaller: the copyright range is not one range — RNS headers read 2016-2026,
LXMF's read 2020-2025 — so the README states each instead of applying RNS's to both.

## Card A20: `ReticulumTransport` drops its own `storage_path` argument

| # | Acceptance criterion | Evidence | Result | Gap |
|---|---|---|---|---|
| 1 | The `storage_path` kwarg reaches `self.storage_path`, pinned by a test | `test_storage_path_kwarg_reaches_the_attribute` asserts the attribute equals `transport_module.expand_path(X)` — the same helper the fix uses, so normalization cannot mask a dropped value | PASS | - |
| 2 | Chain order pinned: `extra` beats kwarg, kwarg beats scoped env | `test_chain_order_extra_beats_kwarg_beats_env` sets all three sources at once and checks the two pairings; the scoped reader is stubbed to `os.environ` through the module-level `scoped_env` fixture shared with `TestConstruction`, so the env half of the chain is exercised without profile state | PASS | - |
| 3 | A started transport with an isolated storage path creates nothing under `$HOME` | `test_started_transport_creates_nothing_under_home` monkeypatches `HOME` to a fresh dir, starts, and asserts the dir is still empty (`list(fake_home.iterdir()) == []`) — the assertion says what the acceptance says, not just that one known child is missing. Red-before: run against the base `transport.py` (fix stashed) at branch base, it fails — the makedirs lands in the fake home. Independently confirmed at the suite level: the head CI-stub full run against a fresh empty `$HOME` leaves 0 paths matching `reticulum-gateway` anywhere under it | PASS | - |
| 4 | Transport file at `HOME=/nonexistent`: 0 failures at head (base: 28); suite matches base except new tests | Red-before measured at dev@`ba5b5c2`: that file gives 28 failed / 27 passed under `env -u PYTHONPATH -u RETICULUM_STORAGE_PATH -u RETICULUM_DISPLAY_NAME HOME=/nonexistent HERMES_GATEWAY_ROOT=/nonexistent HERMES_VENV_PATH=/nonexistent TMPDIR=/tmp .venv/bin/python -m pytest tests/test_reticulum_transport.py -q` — the two `-u` scrubs are mandatory for the base number, an ambient `RETICULUM_STORAGE_PATH` (this box runs the adapter) satisfies the chain and hides all 28; the same command at head gives 58 passed (the file's 55 plus 3 new). Real-base full suite: base 660 passed / 4 skipped / 1 failed (the carded `test_tool_emoji` parity red), head 663 / 4 / 1 — delta exactly the 3 new tests, same known red. CI-stub (scrubbed) with fresh writable `$HOME`: base 656 / 9 / 0, head 659 / 9 / 0 | PASS | - |
| 5 | The stale comment stating the defect is corrected | `tests/test_reticulum_transport.py` `TestRatchetQuarantine._make` comment ("the `storage_path` kwarg is legacy and ignored") replaced with a pointer to the pinned channel tests; the test keeps exercising the `extra` channel on purpose | PASS | - |
| 6 | The docstring claims only what the constructor does (Greptile P2 on PR #7) | 0156f64 rewrites the precedence sentence to speak only of `storage_path` (`extra` > named argument > env reader > defaults) instead of promising the order for all named arguments, which is false: `display_name`, `announce_interval_min` and `rns_config_path` are still accepted and dropped, and `require_shared_instance` deliberately takes the argument over `extra`. Those three are the same defect class, filed as issue #8 rather than widened into this card | PASS | - |

Bonus row (not an acceptance condition, recorded because it retires a standing
environment caveat): the **full** suite at head under the CI-stub with
`HOME=/nonexistent` now gives 659 passed / 9 skipped / 0 failed — A19's row 2 had
to insist `HOME` be an existing writable directory precisely because of this
defect, and that constraint is gone at head. Other writes still appear under a
stub `$HOME` (`~/.hermes/.reticulum-turn-alive`, a bridge-era step log) but none
of them fail on a nonexistent `HOME`, so no follow-up is carded for them; they
were observed at the base run too.

Red-before, recorded at branch base dev@`ba5b5c2`: the final 3-case file run
against the base `transport.py` (fix stashed) gives 3 failed — the kwarg test,
the chain test, and the home-isolation test. Green after: 58 passed at
`HOME=/nonexistent` for the whole transport file; 3/3 pass in every mode.

## Card A21: `install.sh` installs into one hardcoded Hermes home, so every extra profile drifts

| # | Acceptance criterion | Evidence | Result | Gap |
|---|---|---|---|---|
| 1 | plugin copies (marker/`__pycache__` excluded from dir-diff, marker asserted separately) land in the named home; pre-existing default-home `plugins/` untouched |  |  |  |
| 2 | `plugins.enabled` + `approvals.timeout: 900` written to the named home |  |  |  |
| 3 | an existing `approvals.timeout` and unrelated entries survive (value-level); no duplicate keys |  |  |  |
| 4 | no-flag run identical on home-scoped writes; default venv a static read, never executed |  |  |  |
| 5 | precedence flag > env > default (flag+env sub-case); nonexistent named home refused; env install creates no default home; env-named default created on a fresh box; profiles/ home accepted, `<root>/profiles` refused |  |  |  |
| 6 | `--check`: stale roots nonzero; missing marker nonzero; not-installed informational; `HERMES_HOME` as ROOT default; bad ROOT nonzero; `--hermes-home` combination refused; all three limitations printed; fixtures built by install runs, mutated per case |  |  |  |
| 7 | CI guard: writes outside selected home ∪ `$SCRIPT_DIR` ∪ explicit `--venv` ∪ `${HOME}/.reticulum` ∪ `${HOME}/.lxmf/storage` fail the test |  |  |  |
| 8 | next-steps names the selected home (`.env` pointer, `RETICULUM_*` guidance) |  |  |  |
| 9 | PII sweep exit 0; no literal absolute home path added to the script |  |  |  |
| 10 | non-default home skips the service step (`--service --venv <tmp>/venv`, stub `systemctl` never invoked); skip line in auto and explicit mode; resolved-default-via-flag run does **not** skip (stub invoked) |  |  |  |
| 11 | docs: profile section; default-home-only references generalised (incl. QUICKSTART:42, README:118 disambiguated); caution ordered, no unverified peer claims |  |  |  |
| 12 | `--global --no-service` (no `--venv`, stub `pip`): marker lists only roots that mode used (no venv line); `--check` green; venv root impossible outside venv mode |  |  |  |
| 13 | `--help` and usage banner list `--hermes-home` and `--check` |  |  |  |

Status: **open, proposed 2026-11-26** — peer recommendation adopted by the operator; passed
senior review **GO 4/5** (fifth pass, 2026-11-27) with zero blocking issues; that pass's
eight non-blocking nits are folded in; the operator's ruling gates landing it in `tasks.md`.
