# A3 Spec: Automated ACL rejection

Card: A3 (`tasks.md` "Card A3"). Branch: `step/A3-acl-rejection`.
Drafted 2026-10-11 with the user via grilling rounds; every ruling below is the user's.
Governing precedent: `spec.md` section 17.8 (silent-drop superseded by the 2026-09-30
ruling) and `plan.md` section 21.3 item 3.

The decided ruling, restated inline (from spec.md 17.8, decision text 21.3): the
adapter replies with an automated rejection instead of dropping — the canned literal
`⛔ Access not authorized.` on the original bridge path (`core/acl.py` denies, the caller
returns the literal reply text, Hermes is never reached). Authorization is a code-level
allowlist decision and must never be delegated to the model, so an unauthorized peer
cannot prompt-inject its way into a turn.

## 1. Problem

Today an inbound from a peer outside the allowlist is silently dropped in
`_dispatch_inbound` (adapter.py, allowlist check around line 440): a debug log, no reply.
Section 17.8 ruled that behavior superseded. The original bridge answered unauthorized
peers with the canned literal `⛔ Access not authorized.` (`core/acl.py` deny -> `cli.py`
reply, Hermes never reached). A3 restores that behavior on the adapter path with a hard
guarantee: zero model involvement.

Two gates exist today and the card never said how they relate:

1. Adapter gate: `_dispatch_inbound` -> `_allowlist()` / `_in_allowlist()`, reads
   `HERMES_RETICULUM_ALLOWED_USERS` via `platform_gate_env`, drops on deny.
2. Gateway authz gate: platform registration passes
   `allowed_users_env="HERMES_RETICULUM_ALLOWED_USERS"` and
   `allow_all_env="HERMES_RETICULUM_ALLOW_ALL"` (registration.py:154-155), so the
   gateway's own authz layer re-checks the same envs after `deliver_inbound`.

A third reader exists on the standalone/CLI path: `core/acl.py` `AccessControl`, which
already implements fail-closed semantics (allow-all is opt-in via
`HERMES_RETICULUM_ALLOW_ALL`, default is allowlist/closed) and blocklist priority.

## 2. Rulings (user decisions, this draft)

- D1 Single gate. Exactly one gate lets a peer talk to the LLM: the user's hash in
  Hermes' .env (`HERMES_RETICULUM_ALLOWED_USERS`). That env is the single source of
  truth. The gateway's generic per-platform allowed-users list is NOT a second, separate
  gate; the gateway authz layer reads the same env values (it already does, via the
  registration fields). No peer must pass two independent lists.
- D2 Adapter owns the rejection send. The adapter denies AND the adapter sends the
  canned reply. Gateway authz stays defense-in-depth only (see D7).
- D3 Fail closed. Empty or unset `HERMES_RETICULUM_ALLOWED_USERS` means nobody talks to
  the LLM until a hash is added. Allow-all only by explicit opt-in
  `HERMES_RETICULUM_ALLOW_ALL=true`. This aligns the adapter with `core/acl.py` and the
  control server's acl mode (which already defaults to closed); the adapter's current
  "None allowlist -> allow-all -> defer to gateway" behavior is removed.
- D4 Throttle. Per-destination rejection cooldown, default 5 minutes, env
  `HERMES_RETICULUM_REJECT_COOLDOWN_S`. During cooldown, later messages from the same
  peer are still denied but silently suppressed: log the deny, send nothing. The
  throttle is a LoRa congestion guard first, a nuisance guard second.
- D5 Host-side logging. Denials log the full 32-hex source hash, host-local only: the
  operator needs it to diagnose a mistyped allowlist. It never crosses to the peer, never
  enters a prompt, a system note, or any model-visible lane. Peer-visible surface stays
  hash-free; the canned reply carries no identity.
- D6 Misconfigured boot. Boot in closed mode with an empty allowlist: loud startup
  warning on the host; denied peers get nothing (no "operator misconfigured" hint leaks
  to strangers).
- D7 Defense-in-depth edge. If a peer passes the adapter gate but the gateway authz layer
  still drops it (two layers out of sync: a config bug, not a policy), the peer receives
  the canned rejection, and the host log names it as a gateway-side deny so the desync is
  visible as a bug signal.
- D8 Malformed inbound unchanged. No parseable source hash -> silent drop as today
  (there is no identity to reply to). The rejection reply fires only for a real source
  hash that is present and unauthorized.

## 3. Invariants

- I1 (security, from the card): an unauthorized peer's message text never enters a
  prompt, a system note, or a model-visible log lane. Only the destination hash is
  logged (host-local).
- I2 (zero model involvement): the denial path calls no gateway handler, no session, no
  LLM. The reply is a literal string constant, not model output.
- I3 (single truth): exactly one authorization decision is made per inbound from exactly
  one source of truth (`HERMES_RETICULUM_ALLOWED_USERS` / `HERMES_RETICULUM_ALLOW_ALL` in
  Hermes' .env). The gateway-side re-check must agree with the adapter gate by
  construction (same envs); disagreement is a bug, logged as such (D7).
- I4 (bandwidth): at most one rejection per cooldown window per destination.

## 4. Design

### 4.1 Decision point

Replace the drop branch in `_dispatch_inbound` with:

```
deny(source_hash):
    log full hash (host-local)
    if misconfigured_boot (closed mode, empty allowlist):
        suppress reply entirely            # D6: strangers get nothing
    elif cooldown allows:
        send canned REJECTION_TEXT via the transport seam
    else:
        suppress reply (already logged)
    return  # never call deliver_inbound
```

D6 precedence rule: the empty-allowlist suppression overrides the cooldown and the
canned reply. While the boot is misconfigured (closed mode with no entries), every
deny logs host-side and sends nothing to any peer. The canned reply only fires for a
configured gate (allowlist non-empty, or allow-all open mode).

`REJECTION_TEXT = "⛔ Access not authorized."` as a module constant.

Current vs target (every divergence below is a required code change on this card):

| Concern | Current behavior (evidence) | Target behavior (ruling) |
|---|---|---|
| Allowlist miss | Silent drop, debug log only: `_dispatch_inbound` allowlist branch (adapter.py:440-447) | Canned rejection send + host-local full-hash log (D2/D5) |
| Allow-all default | `_allowlist()` returns None on unset/empty env -> allow-all, defers to gateway (adapter.py:828-840) | Closed default; allow-all only via explicit `HERMES_RETICULUM_ALLOW_ALL=true` (D3) |
| ACL dialects | Three readers: adapter `_allowlist()`/`_in_allowlist()`, `core/acl.py` `AccessControl`, gateway authz via registration (registration.py:154-155) | Adapter gate reuses `AccessControl`; gateway re-check reads the same envs (D1, section 4.1) |
| Rejection rate | No throttle exists (no reply at all today) | Per-destination cooldown, default 300s (D4) |
| Empty-allowlist boot | No warning exists | Loud host-side startup warning (D6) |

The adapter gate becomes the authoritative gate (D1/D2). It must implement the same
semantics as `core/acl.py`: blocklist priority, then allow-all opt-in, then allowlist,
default closed. Preferred implementation: reuse `AccessControl` from `core/acl.py`
behind the adapter gate instead of the hand-rolled `_allowlist()`/`_in_allowlist()`
pair, so the repo has one ACL implementation, not three dialects. (`_allowlist()`
currently returns None on unset env -> allow-all; that path is deleted per D3.)
`platform_gate_env` multiplex-safety is kept: the gate reads envs the same way the
registration does.

### 4.2 Cooldown

`dict[source_hash, monotonic_deadline]` in adapter memory. On deny: if now >= deadline
(or unseen), send and set deadline = now + cooldown; else suppress. Cooldown seconds
from `HERMES_RETICULUM_REJECT_COOLDOWN_S` (default 300; invalid values clamp like the
existing gate-timeout validation). No persistence: a restart resets the map, which is
acceptable (worst case one extra rejection after restart).

Memory bound: expired entries are pruned; they do not live until restart. Pruning is
scheduled, NOT a full scan per deny (a scan per deny would make flooded denied peers
add work to `_drain_loop`, which handles allowed messages on the same task). Required
shape: deadlines ordered by expiry (e.g. a heap of (deadline, hash) beside the map, or
an expiry-ordered dict) plus a periodic prune on the adapter's existing event/heartbeat
cadence, popping entries whose deadline has passed. A deny then costs: one map lookup,
possibly one heap push. The map is bounded by peers denied within the last cooldown
window, not by total distinct peers seen since boot. Unit tests: (a) deny from N
distinct synthetic hashes spread beyond the window, assert the map size stays bounded;
(b) a flood of denies from many hashes does not grow per-message deny cost observed by
an allowed message on the same drain task.

### 4.3 Rejection send path

The rejection goes directly through the adapter's outbound transport seam (the same
path `send()` uses for content), NOT through `deliver_inbound` -> gateway -> turn ->
`send_reply`. It must not create a gateway session, must not touch session history, and
must not be recorded as a conversation turn. It may be recorded in outbound outcome
bookkeeping so a failed rejection send is visible in logs (non-fatal).

### 4.4 Startup warning (D6)

At adapter connect (or registration env validation): if closed mode and the allowlist is
empty, `logger.warning` naming the env var to set. Peers get nothing.

### 4.5 Gateway-side (D7)

No change to gateway authz itself (it already reads the same envs via the registration
fields). The current adapter code has no post-delivery signal at all: `deliver_inbound`
(adapter.py:465) hands the event to the gateway handler and returns without inspecting
the outcome, so a gateway-side deny of an adapter-admitted peer is invisible today.
Required change on this card: the adapter inspects the outcome of `deliver_inbound`
instead of discarding it. If the gateway handler reports a deny for a peer the adapter
admitted, the adapter logs a desync line naming the gateway deny AND sends D7's canned
reply to that peer through the transport seam (4.3), subject to the same cooldown and
the D6 suppression rule. Mechanism: the gateway handler's result is already returned to
the adapter and currently dropped; the implementation reads that result (or, if the
result shape cannot carry a deny, wraps the call with a gateway hook that reports the
authz outcome). The exact mechanism is an implementation choice; the observable effect,
criterion 10, is not optional.

## 5. Acceptance criteria (Given/When/Then)

1. Given `HERMES_RETICULUM_ALLOWED_USERS` lists hash A, When peer B (absent) sends any
   text, Then B receives exactly `⛔ Access not authorized.` and no Hermes turn runs
   (no session activity, no model call).
2. Given the rejection path, When its logs are swept, Then the unauthorized peer's
   message text appears nowhere in gateway-visible output; only the source hash appears,
   host-local.
3. Given `HERMES_RETICULUM_ALLOWED_USERS` unset or empty and ALLOW_ALL not true, When
   any peer sends, Then the peer is denied (closed default), the peer receives NO reply
   (D6 suppression precedes the canned reply), and the host startup log warns that the
   allowlist is empty.
4. Given `HERMES_RETICULUM_ALLOW_ALL=true` and no blocklist entry matching, When any
   peer sends, Then the peer is admitted (open mode is explicit opt-in only) and zero
   rejection sends occur.
5. Given peer B was rejected at time t, When B sends again at t+1min, Then the deny is
   logged and no second rejection is sent; at t+5min+ a rejection is sent again.
6. Given a blocklisted hash (when blocklist support is wired into the adapter gate),
   When it sends, Then it is denied even if also present in the allowlist, and the deny
   carries the canned reply under the cooldown rules even when
   `HERMES_RETICULUM_ALLOW_ALL=true` (blocklist priority outranks allow-all).
7. Given an inbound with no parseable source hash, When it arrives, Then it is dropped
   silently as today (no reply).
8. Given allowlist matching with uppercase/colon-formatted hashes in the env, When the
   comparison runs, Then it matches case- and colon-tolerantly (existing behavior kept).
9. Existing tests (`test_allowlisted_peer_is_admitted`,
   `test_allowlist_matching_is_case_and_colon_tolerant`) stay green; any test relying on
   allow-all-on-unset-env behavior is re-expressed against the new closed default.
10. Given the adapter admits a peer and the gateway authz then denies the delivered
    event, When the delivery outcome is inspected, Then the adapter logs a desync line
    naming the gateway deny and sends the canned reply to that peer via the transport
    seam (cooldown and D6 suppression apply).

## 6. Out of scope (this card)

- Peer-facing wording beyond the canned literal (no explanations, no hints).
- Blocklist UI / management commands.
- NomadNet-side rendering of the rejection.
- A8 live wire check (separate verification item).

## 7. Verification plan

- Unit tests in `tests/test_reticulum_adapter.py` for criteria 1-9 using the synthetic
  hashes already used in the suite (`aabbccdd`, `deadbeef`, `0badc0de` pattern).
- A log-sweep test: denial-path logs contain no message text (criterion 2), mirroring
  the card's acceptance line.
- `scripts/check_imports.py` stays green (rns/lxmf stay out of pure-logic modules).
- Live check (manual, after merge): send from a non-allowlisted Sideband client, confirm
  the canned reply and the host-side log line; confirm the allowlisted client is
  unaffected.

## 8. Docs and skill updates shipped with this card

- `tasks.md`: A3 row status -> done on merge; A2 row status -> closed (loose end from
  the #14 merge; docs travel with the branch).
- `spec.md` section 17.8: point at this spec as the implementing ruling record.
- `verification.md`: A3 row added.
- Skill `rns-hermes-endpoint-bridge`: gate semantics (single .env gate, closed default,
  cooldown, D7 desync signal) folded into the ACL reference.

## 9. Review amendments (2026-10-12 subagent spec review, applied)

- R1 (MAJOR, applied in 4.1): a current-vs-target table was added so no reader mistakes
  the spec's rejection semantics for existing behavior. Every row is a required change.
- R2 (MAJOR, applied in 4.6): the allow-all interaction with rejection is now explicit.
- R3 (MINOR, applied in 1): the superseding ruling from spec.md 17.8/21.3 is restated
  inline so a cold reader of this file alone sees the decided behavior.
- R4 (MINOR, applied in 4.7): ordering rule for malformed vs unauthorized inbound.
- R5 (NIT, applied in 4.8): observable effects of a rejection are enumerated.

Review amendments (2026-10-12 greptile pass on PR #15, applied):

- R6 (P2, applied in 4.1 + acceptance 3): D6 precedence made explicit - empty-allowlist
  boot suppresses the canned reply for all peers; the reply only fires for a configured
  gate.
- R7 (P2, applied in 4.6): open-mode wording corrected - blocklist priority outranks
  allow-all, so a blocklist deny in open mode is a full deny (canned reply + cooldown).
- R8 (P2, applied in 4.5): the gateway desync signal is now a required adapter-side
  change, not a hoped-for existing log line; `deliver_inbound` has no post-delivery
  outcome signal today (verified at adapter.py:465).
- R9 (P2, applied in 4.2): cooldown map prunes expired entries on each deny so memory
  is bounded by the cooldown window, not by distinct peers seen since boot.

### 4.6 Allow-all interaction with rejection (settles the reviewer's open question)

`HERMES_RETICULUM_ALLOW_ALL=true` admits every peer that is not blocklisted, so the
only rejection that can fire in open mode is a blocklist deny: blocklist priority
outranks allow-all, exactly as in `core/acl.py` (acceptance criterion 6). A blocklist
deny in open mode is a full deny under section 4.1: canned reply via the transport
seam, host-side log, cooldown applies to it like any other rejection. Consequences:
- The cooldown map stays empty in open mode unless a blocklisted peer sends (the only
  deny source).
- The startup warning (D6) checks closed mode only; open mode logs an informational
  line naming allow-all as active (public-instance operators should see it in the log).
- Acceptance: criterion 4 (open mode admits non-blocklisted peers, zero rejection
  sends) and criterion 6 (a blocklist deny in open mode is a full deny carrying the
  canned reply).

### 4.7 Ordering: malformed check before ACL check (settles the reviewer's open question)

Order in `_dispatch_inbound` stays: parse/normalize first, then ACL. Rationale:
- No parseable source hash means no destination to reply to (D8), regardless of who the
  peer is; a malformed inbound from an allowlisted peer must also stay a silent drop.
- The canned rejection fires only for a well-formed inbound with a real source hash that
  is unauthorized.
- Chunked inbound: the ACL gate runs per inbound LXMF delivery like today (a chunk is an
  inbound event). Rejection is identity-based, not content-based, so a rejected peer's
  chunks each hit the gate; the cooldown map makes that at most one reply per window.
- Test naming: extend `tests/test_reticulum_adapter.py` allowlist tests (the
  `test_allowlisted_peer_is_admitted` group) with the rejection cases; malformed-drop
  tests stay untouched as regression guards for D8.

### 4.8 Observable effects of a rejection (enumerated)

| Effect | Surface | Test hook |
|---|---|---|
| Canned reply `⛔ Access not authorized.` | Peer-visible, LXMF status reply via transport seam | unit: assert outbound send payload |
| Host log line with full source hash + deny reason | Host-local log only | unit: caplog sweep (criterion 2) |
| Cooldown suppression log line (deny, no send) | Host-local log only | unit: second send within window asserts no outbound |
| No gateway session, no model call | Absence of `deliver_inbound` call | unit: spy on deliver_inbound |
| Control server `/status` acl mode | `closed`/`open` as today | existing `tests/test_control_server.py` acl tests stay green |
