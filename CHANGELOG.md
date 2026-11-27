# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.3.1] — 2026-11-27

### Added
- **Exact dependency pins and license placement**: the installer pins
  `rns`/`lxmf` to exact versions, and the Reticulum License text is placed
  where users meet it — README terms and install-time disclosure (card A19).

### Fixed
- **`storage_path` is honoured**: the transport config argument now folds into
  the chain that resolves the storage location; previously it was accepted and
  ignored (card A20, PR #7).
- **Cold-link courtesy**: path probes abort on disconnect and route
  re-requests are capped to announced peers, ending endless re-asking of
  unreachable peers (PRs #9, #10).
- **Docs orientation**: stale claims naming the archived standalone bridge as
  the supported path were corrected across `AGENTS.md`, the bridge README, and
  module docstrings (PR #3).

## [0.3.0] — 2026-11-20

### Added
- **Gateway adapter as the primary mesh path**: the Reticulum/LXMF delivery
  now runs in-process inside the Hermes gateway — no standalone bridge daemon,
  no central messaging server. The legacy standalone bridge module is archived
  and no longer the supported path.
- **Deny-by-default mesh ACL with structured rejections**: unknown senders are
  refused with an explicit reason; the allow list is explicit config (card A3).
- **Markdown renderer field + plain-text fallback**: LXMF clients advertise
  rendering support, and non-rendering clients get a platform hint instead of
  raw markdown (card A13, PRs #37, #38).
- **Word-boundary-aware chunking**: long outbound replies split at word
  boundaries rather than mid-word (PR #41).
- **False-desync detection fixed** (card A12, PR #40) and per-profile secret
  isolation documented and verified on a live two-profile broker
  (enforcement lives in Hermes' secret scope; card A11).
- **PII sweep gate**: `scripts/security-sweep.py` runs in CI over the pushed
  tree *and* the commit range of every push/PR, with a matching pre-push hook
  (PR #32) and a written policy in `docs/PII-RULES.md`.

### Changed
- **BREAKING**: ACL environment prefixes renamed `RETICUM_*` → `RETICULUM_*`;
  update your service environment when upgrading.
- Control server binds loopback-only and requires a token.
- Version metadata aligned across package and plugin manifests.

### Deprecated
- The standalone bridge service as a supported install path: archived, no
  longer the documented way to run the mesh path (still ships, bug fixes only).

## [0.2.0] — 2026-09-19

### Added
- **Tool gate reliability**: split the *deny* and *timeout* block messages, so an
  unanswered approval prompt tells the model it may continue on safe tools
  instead of wedging every subsequent tool call.
- **Startup gate-timeout validation**: the bridge fails loudly at boot if
  `MESH_GATE_TIMEOUT` and `HERMES_MESH_APPROVAL_TIMEOUT` are mis-ordered or
  non-positive (rejects `0`, negatives, `NaN`, infinity).
- **Jev pre-gate triage**: optional automatic pre-approval of routine/gated
  tools (`MESH_GATE_TRIAGE=allow_benign`), with a documented confidence floor.
- **Tool emoji parity**: mesh tool output uses the same emojis as the gateway's
  tool annotations.
- **Periodic re-announce**: configurable announce cadence (`RETICULUM_ANNOUNCE_INTERVAL`)
  and an `/announce` command, hardened against invalid values.
- **Service installer**: `install.sh` can auto-render the systemd user service
  for the current user.
- **Liveness marker refresh**: keeps long `-q` turns alive past the idle timeout
  by refreshing the marker on real tool activity.
- The control server's `/status` now reports its effective `approval_timeout_s`,
  and the gateway plugin validates its timeout against it at load.

### Changed
- Raised runtime floors to the current releases: `rns>=1.5.4,<2.0` and
  `lxmf>=1.1.1,<2.0`.
- README deployment docs now spell out the required three-layer gate-timeout
  ordering (`approval <= gate < hook_callback < 600`) and warn that the shipped
  defaults are not safe to run as-is.

### Fixed
- Gate wedge when an approval prompt was left unanswered while AFK — the hook
  wrapper could fire before the control-server deny clock, failing closed every
  tool for the rest of the turn.
- Rejected invalid timeout values that previously parsed as floats but were
  semantically meaningless (e.g. negative or non-finite gate timeouts).

[0.3.0]: https://github.com/strtPath/hermes_reticulum_platform/releases/tag/v0.3.0
