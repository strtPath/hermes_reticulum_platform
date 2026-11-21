# Architecture Decision Records

Decisions that should survive the feature that produced them. Each ADR records a choice
already settled in the specs or findings, the options considered, and the consequence. If
you find an ADR that no longer matches the code, the ADR is a record of a past decision:
supersede it with a new ADR rather than editing history.

Format: `NNNN-short-title.md`. Status values: `Accepted`, `Superseded by NNNN`, `Deprecated`.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-reticulum-as-gateway-platform-adapter.md) | Reticulum ships as a gateway platform adapter, not a CLI-driving bridge | Accepted |
| [0002](0002-pin-rns-lxmf-not-vendor.md) | Pin RNS/LXMF versions; do not vendor source | Accepted |
| [0003](0003-preserve-900s-approval-window.md) | Route approvals through the gateway, preserving the 900s window | Accepted |
| [0004](0004-declare-capability-flags-explicitly.md) | Adapter capability flags must be declared explicitly | Accepted |

Sources for these decisions: `docs/specs/adapter/spec.md` (design), `docs/specs/adapter/plan.md`
(working decisions), and the dated findings docs under `docs/`.
