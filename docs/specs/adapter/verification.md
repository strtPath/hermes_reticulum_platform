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

## Card A4: Command feedback + terse render + send truth

| # | Acceptance criterion | Evidence | Result | Gap |
|---|---|---|---|---|
| 1 | `/status`, `/resume`, `/new`-confirm visibly answered on the phone |  |  |  |

## Card A5: `/new` ack with session id + title

| # | Acceptance criterion | Evidence | Result | Gap |
|---|---|---|---|---|
| 1 | Peer can find the new session after `/new` |  |  |  |

## Card A6: Tool visibility + `/verbose`

| # | Acceptance criterion | Evidence | Result | Gap |
|---|---|---|---|---|
| 1 | A 22-tool turn is no longer silent |  |  |  |
| 2 | `/verbose off` truncates like the CLI |  |  |  |

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

## Card A9: P-5 `/retry`, P-6 `/status`, P-13 command audit

| # | Acceptance criterion | Evidence | Result | Gap |
|---|---|---|---|---|
| 1 | Commands listed in plan.md §19.2 respond on the mesh |  |  |  |

## Card A10: P-8 `/clarify` verification

| # | Acceptance criterion | Evidence | Result | Gap |
|---|---|---|---|---|
| 1 | `/clarify` resolves, or a findings doc proves the gateway gap |  |  |  |
