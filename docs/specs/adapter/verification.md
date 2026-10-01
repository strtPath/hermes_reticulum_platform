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

| # | Acceptance criterion | Evidence | Result | Gap |
|---|---|---|---|---|
| 1 | Unauthorized peer gets `⛔ Access not authorized.` and nothing else |  |  |  |
| 2 | Rejection is a deterministic gate before the model (no prompt-injection path) |  |  |  |

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
