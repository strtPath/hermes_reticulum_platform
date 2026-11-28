<!-- Part of the adapter feature bundle: docs/specs/adapter/.
     spec.md     - the design (what the adapter IS)          [this file]
     plan.md     - working decisions (§18-21, numbering kept)
     tasks.md    - build cards A1-A10, one PR each
     verification.md - acceptance criteria -> evidence table
     Router: ../../AGENTS.md
-->

---
title: Reticulum Platform Adapter — Design Spec
level: sub-spec
parent: spec-rns-hermes-endpoint.md
subsystem: platform-adapter
supersedes: spec-feasibility-reticulum-platform.md (feasibility stage, now settled)
status: DRAFT
---

# Reticulum as a Gateway Platform Adapter: Design

## Scope

States how the mesh bridge becomes a Hermes gateway platform adapter, what the adapter owns,
what the gateway owns, and what gets deleted. This is the design document; the feasibility
document answered whether it was possible and is superseded by this one on all design
questions.

Parent: `spec-rns-hermes-endpoint.md`. Evidence: `_notes-reticulum-fit.md`,
`_notes-delivery-confirmation.md`.

Status: DRAFT, 2026-09-24; revised 2026-09-29 against the implemented branch
(`feat/reticulum-platform-adapter`, `plugins/reticulum` + `src/hermes_reticulum/plugin/`).
Section 17 records the implementation deltas that this revision folds in.

## 1. The decision, and why this shape

The bridge currently spawns `hermes chat -q` children and reimplements session handling,
tool-call streaming, clarify, and approvals outside the gateway. Every one of those is
something the gateway already implements properly for its other platforms.

The adapter form is chosen because it deletes that machinery rather than fixing it. The
decision rests on evidence gathered 2026-09-24 and it holds: see section 3.

This is a plugin, not a core contribution. `ADDING_A_PLATFORM.md` states the plugin path
"requires zero changes to core Hermes code", and out-of-tree registration is the documented
recommendation for community platforms. Nothing here needs a PR into Hermes.

## 2. What the adapter is

A plugin directory with `plugin.yaml` and `adapter.py`, inheriting `BasePlatformAdapter`, and
registering through `ctx.register_platform()` in a `register(ctx)` entry point. The plugin
system then provides, without adapter code: adapter creation, config parsing, user
authorization, cron delivery, `send_message` routing, system prompt hints, and status display.

**Required surface.** Four abstract methods and the typing hook:

- `connect(is_reconnect=False) -> bool`
- `disconnect()`
- `send(chat_id, content, reply_to=None, metadata=None) -> SendResult`
- `get_chat_info(chat_id) -> dict` with at least `name` and `type`
- `send_typing(chat_id)` — inherited default is acceptable

**Optional media methods** (`send_image`, `send_document`, `send_voice`, `send_video`) have
default stubs that return failure. LXMF supports attachments as raw payloads, so these could
be implemented later. Out of scope for the first version (section 9).

**Capability flags to set:**

| Flag | Value | Reason |
|---|---|---|
| `supports_async_delivery` | `True` | The mesh can start a fresh turn after a previous one ended. Correct, and the constraint is only that the message actually arrives. |
| `splits_long_messages` | `True` | LXMF over radio benefits from chunking. The gateway otherwise truncates at `MAX_MESSAGE_LENGTH` (4096). |
| `supports_status_text` | leave `False` | No typing-indicator concept on LXMF; the gateway handles absence. |
| `supports_code_blocks` | `True` | Reticulum clients now render markdown, including fenced code blocks. Verified live on Sideband: a fenced block arrives as a rendered block, not raw fences. |
| `REQUIRES_EDIT_FINALIZE` | leave `False` | No edit support; `edit_message` returns `success=False` and callers send anew. |
| `SUPPORTS_MESSAGE_EDITING` | `False` | Added during implementation (commit 6134de5): the gateway reads this flag and streams every token delta as a separate message for adapters without edit support. LXMF has no edit API, so leaving it default-`True` turned one reply into a burst of partial messages. This flag does not appear in the gateway's documented capability table; it is read via `getattr` with a `True` default, which is why it bites silently. |

**Hooks to implement** (all optional, all needed here):

- `env_enablement_fn` — seed `PlatformConfig.extra` and the home channel from env before
  construction, so env-only setups appear in `hermes gateway status`. Build from a
  `(ENV_VAR, extra_key, conv)` table via `_shared.seed_extra_from_env`.
- `apply_yaml_config_fn` — own the YAML schema for this platform rather than growing core
  config boilerplate. Return `_shared.apply_yaml_bridge(platform_cfg, TABLE)`. Registered
  on card A7; see section 17.5 for what that closed and what it did not.
- `is_connected` — implemented as `_validate_config(config)` returning `True` (every field
  has a default), matching the IRC reference plugin's contract. This replaced an earlier
  version that read a runtime flag nothing ever sets, which made the gate permanently
  `False` and the adapter never constructed.
- `cron_deliver_env_var` — names the home-channel env var so `deliver=reticulum` cron jobs
  route without editing the scheduler's hardcoded sets.
- `standalone_sender_fn` — required for cron jobs that run out-of-process; without it a
  `deliver=reticulum` job fires but the send returns `No live adapter`. **Not registered
  yet** (see section 17); this is a known gap, not a silent one, and section 15 still holds
  the design question (a separate process needs its own RNS, which can attach to the shared
  instance only if one is running).

**Every env read goes through `_shared.get_scoped_secret`.** Never `os.getenv` directly: under
`gateway.multiplex_profiles` a raw read returns the default profile's value, which is a
silent allowlist leak. Import the shared reader, never re-implement it.

## 3. The delivery finding, settled

The feasibility document left one gate open: does this deployment deliver direct or through a
propagation node. It is answered, and the answer favours the adapter.

Measured from the running bridge's journal over 14 days:

- 2,269 `state=delivered`, 10 `state=timeout`, 0 propagated.
- That is 99.4% direct delivery with first-hop cryptographic acknowledgement.

So the delivery mapping is a translation, not a design problem. `PacketReceipt.DELIVERED` and
`LXMessage.FAILED` map onto `SendResult.success` honestly.

The caveat matters for a public plugin and must not be lost: this deployment tracks direct
because its peers are reachable. A plugin shipped for general mesh use will meet propagation
nodes, offline peers, and store-and-forward measured in days. The adapter must therefore
handle both, and section 6 states what it does in each case.

## 4. Identity mapping

| Gateway concept | Reticulum value |
|---|---|
| `chat_id` | LXMF destination hash |
| `user_id` | same hash for a 1:1 DM (Signal does the same) |
| `chat_type` | `dm` |
| `chat_name` | adapter-owned hash to display-name map |
| `thread_id` | none; single-threaded per peer |

Names are adapter-side because the gateway keeps no hash-to-name mapping and RNS has no
contact directory. The adapter maintains one, seeded from config, and `get_chat_info` is not
on a hot path, so staleness is tolerable.

**Key rotation breaks session continuity.** If a peer rotates its identity the destination
hash changes, the session key changes, and the gateway has no chat-id migration mechanism. The
adapter can hold an old-to-new hash map, but the gateway will not follow it. Rare, and it
silently loses a conversation when it happens. Named as an accepted limitation, section 8.

## 5. Transport bridging

RNS calls back from its own event-loop thread, not asyncio. The standard pattern applies:

1. `connect()` starts the RNS thread (and the LXMF router).
2. RNS callbacks push onto a queue.
3. An asyncio task drains the queue and calls `self.handle_message(event)`.

The drain task must be cancelled on `disconnect()`, and the RNS thread joined. No existing
in-tree adapter runs a persistent transport thread, so this is new ground for the codebase
rather than a copied shape. `api_server.py` uses `threading.Thread` for a one-shot task, which
is a precedent but not the same thing.

**`connect()` semantics are strained and this is accepted.** The gateway reads `True` as "the
platform is live and will push inbound". For Reticulum, `True` can only mean "the local RNS
instance is running"; whether any given peer is reachable is separate and changes
continuously. The reconnect watcher keys off connect failure, so a silent peer never triggers
it. The adapter returns `True` on RNS up and reports peer reachability through a separate
surface (section 7), because the gateway has no vocabulary for connected-but-unreachable.

`connect()` must be idempotent. A reconnect calls `transport.start()` again; RNS is a
strict process singleton (the constructor raises "Attempt to reinitialise Reticulum, when
it was already running" on a second call, and `RNS.exit()` hard-exits the process), so
`start()` re-uses the live instance via `RNS.Reticulum.get_instance()` and re-initialises
the LXMF layer (router, delivery identity) — the part a failed first connect may have left
unstarted. Zero-byte LXMF ratchet files (an unclean-shutdown artifact) are quarantined
before the router is built so a reconnect after an unclean kill can succeed.

## 6. Delivery handling — the part that does not map

`SendResult` is binary. The gateway treats acceptance as delivery, records an obligation in
its SQLite ledger before `send()` (`record_obligation`), and finalises on return
(`mark_delivered` / `mark_failed`). There is no queued or pending state.

**Direct delivery.** `send()` registers a delivery callback on the LXMF message, awaits the
bounded outcome, and returns `success=True` on `DELIVERED` or `success=False` with an error
string on `FAILED`. The bound is five attempts at `DELIVERY_RETRY_WAIT` (10s) plus receipt
timeouts, which is seconds to low minutes. This is honest and complete.

**Propagated delivery.** Node acceptance fires a callback with `state = SENT`, not
`DELIVERED`. No per-message signal reaches the sender afterwards, ever. The adapter must
therefore:

- Return `success=True` on node acceptance, because the gateway offers no alternative, and
- Record the message in its own pending set, and
- Surface unconfirmed messages through the adapter's own status, since the gateway's ledger
  will have marked them delivered and will never correct itself.

This is the one place where bridge machinery survives the pivot, and it survives because it
solves a real problem peculiar to radio. It is not a workaround for a missing gateway feature.

**The ledger is wrong in both directions, and the pending set is the record that is not.**
The propagated case above is the ledger claiming delivery that never happened. The opposite
error is live too, and it is the one that loses messages:

- `MAX_ATTEMPTS = 3` and `STALE_AFTER_SECONDS = 24 * 60 * 60` bound every row
  (`gateway/delivery_ledger.py:33-34`). A row that exhausts its attempts, or sits past the
  cutoff, is marked `abandoned` and never sent.
- `_claim_pending_obligations` (`gateway/run_startup.py:313`) claims only rows whose platform
  appears in `deliverable_platforms` / `deliverable_targets`, built from the adapters connected
  **at boot**. A mesh adapter that is not up yet is skipped: its rows spend no attempt and are
  simply not claimed. The sweep is also what clears the session's `resume_pending` flag, so the
  turn is not re-run either. The reply is then abandoned at the 24h cutoff with one log line.

On a platform that is always up this is correct. On a mesh, a node being off is normal. So a
`retryable=True` result from a disconnected adapter can produce a reply that the gateway holds,
declines to redeliver, and then discards.

Consequence for the design: the adapter's pending set is not a mirror of the ledger, it is the
only delivery record that stays true when the ledger does not. Two requirements follow.

- The pending set must survive `disconnect()`/`connect()` on the same adapter instance. The
  reconnect path calls `_safe_adapter_disconnect` before reconnecting
  (`gateway/run_adapters.py:1245`, `1275`), so anything rebuilt in `connect()` is lost exactly
  when it is needed.
- The pending set must be the surface a user can inspect to answer "did that message actually
  go". Section 7 already routes reachability there; delivery confirmation belongs beside it.

This does not license fighting the ledger. The adapter never writes to it and never re-sends on
its own initiative. It keeps its own truth and reports it.

**No silent queue at the packet layer.** `Packet.send()` returns `False` immediately when no
interface can carry the packet. The adapter maps that to
`success=False, retryable=True, error_kind=transient`, which lets the base class's retry logic
and the ledger's sweep behave correctly.

**`_send_with_retry` caveat.** The base class does not retry timeouts ("may have delivered").
A long direct send that times out therefore returns failure without retry, which is the right
call and should not be overridden.

## 7. What replaces each bridge subsystem

| Bridge component | Disposition |
|---|---|
| Session adoption heuristics | Deleted. The gateway owns sessions. |
| Step watcher polling `state.db` | Deleted. The gateway emits tool events directly. |
| Clarify gate and answer capture | Deleted. The gateway sets `agent.clarify_callback` in-process where a real user exists. |
| Approval control server | Deleted as a component. Its **behaviour** moves to the gateway's own approval path, which is strictly richer: a shared prompt template (`_format_exec_approval`), a plain-text fallback for adapters without buttons (correct for LXMF), and natural-language acceptance ("yes", "ok", "confirm", "y", 👍) routed to the canonical `/approve` handlers, not just the slash forms. **The 900s window is the part that must not be lost**: it exists for LoRa hop latency, dropped connections, and AFK users before deny-by-default, and the gateway's default `approvals.timeout` is 300s. Ship 900 as this platform's documented default and say why. |
| ACL | **Replaced, not removed.** The plugin auth hooks cover the same ground: `allowed_users_env` / `allow_all_env` on `ctx.register_platform`, read through `_shared.platform_gate_env` (multiplex-safe). The mesh's existing model carries over one-for-one: an allowlist by destination hash, allow-all off by default, and 32-hex validation. Document that the allowlist holds **LXMF destination hashes**, since that is what users will paste in. |
| Downlink queue and ack tracker | **Split.** The chunking carries over: the 368-byte per-block content budget, the per-part `[tag i/N]` prefixes, and the UTF-8 boundary truncation are hard-won mesh knowledge and section 14 keeps them. The budget constant and the prefixing live in `delivery.chunk_for_send`, which the adapter's `send()` calls and then sends one packet per part. The queue does not carry over. The gateway owns outbound ordering and retry; a second scheduler in the adapter would race it, which is the same mistake section 11 exists to prevent for RNS shared-instance attachment. What survives of the ack tracker is the propagation-delivery tracker (section 6). |
| Slash commands | Deleted as a *bridge-local* dispatcher. The gateway's command set applies (`/new`, `/stop`, `/approve`, `/deny`, `/status`, `/help`, `/model`), and the base platform's `is_command()` (text starting with `/`, `allow_gateway_control=True`) means the Reticulum adapter needs no command code of its own. Mesh-only bridge commands (`/status`-style introspection, `/tools`, `/hold`, `/go`, `/announce <minutes>`) do not carry over. |
| Liveness guard / SIGTERM handling | Deleted. No child process to guard. |
| Preflight checks | Deleted for the bridge; RNS start-up failure surfaces through `connect()`. |
| `/status`, `/tools`, `/verbose`, `/steps`, `/hold`, `/go` | Deleted. Not in scope for the adapter; revisit only if a mesh user asks. |
| `/approve`, `/deny` | **Retained**, but served by the gateway's handlers rather than the bridge's. |

Commands that the gateway already provides and mesh users keep: `/new`, `/stop`, `/approve`,
`/deny`, `/status`, `/help`, `/model`.

Approach taken to the two rows above: nothing is deleted that the gateway cannot do at least
as well. Where the bridge holds a hard-won value (the 900s approval window), that value moves
into gateway configuration rather than being discarded with the component that held it.

Peers reachability, which the gateway cannot express, is reported in `send()` failures and in
the adapter's own `get_chat_info` metadata rather than invented as a gateway state.

## 8. Accepted limitations

- **Peer reachability is invisible to the gateway.** It will believe the platform is live
  while no peer can be reached. Reported adapter-side, not gateway-side.
- **Key rotation breaks a conversation.** No migration mechanism exists.
- **Propagated delivery is unconfirmable.** The adapter tracks it; the gateway's ledger will
  disagree, and the adapter must not fight that.
- **No media in v1.** Attachments are out of scope.
- **`chat_type` is always `dm`.** LXMF has no group concept in standard use. A group would
  need a distinct adapter shape and is out of scope.

## 9. Out of scope

Do not build these in the first version, and do not gold-plate toward them:

- Media send/receive over LXMF.
- Group or channel support.
- Propagation-node administration commands.
- Migration of existing bridge sessions into gateway sessions. The adapter starts fresh.
- Any change to Hermes core. If a hook is missing, widen the generic plugin surface in the
  adapter's own repo first and raise it upstream separately.
- `/whoami` style ACL introspection; the plugin auth hooks cover authorization (section 7).
- Vendoring RNS/LXMF source in-tree; see section 11 for the licence reasoning.

## 10. Test seams

The adapter is testable without a live mesh if the transport is behind a seam:

- **Transport interface**: a protocol with `send_to(destination_hash, payload)`,
  `register_delivery_callback`, `register_failed_callback`, and a `start()/stop()` pair. The
  real implementation wraps LXMF; tests use a fake.
- **Identity mapping**: `get_chat_info` returns name and type for a known hash, a fallback for
  an unknown one, and never raises.
- **Send result mapping**: `DELIVERED` to `success=True`; `FAILED` to `success=False` with
  `error_kind`; `SENT` (propagated) to `success=True` plus an entry in the pending set; packet
  refusal to `retryable=True`.
- **Ledger independence**: a `retryable=True` result recorded while the adapter is down is
  still present in the adapter's pending set after `disconnect()` then `connect()` on the same
  instance, and is still reported as unconfirmed. This is the case the gateway's boot sweep
  skips (`run_startup.py:313`) and abandons at 24h, so the adapter's own record is the only
  assertion that can fail here.
- **Inbound bridging**: a callback pushed from a foreign thread reaches `handle_message`
  exactly once, and the drain task ends cleanly on `disconnect()`.
- **Lifecycle**: `connect()` twice is idempotent; `disconnect()` then `connect()` works;
  `disconnect()` with no connect does not raise.

Tests exercise the real delivery path with a fake transport, per the gateway's own guidance.
No test asserts on platform lists or command counts.

## 11. Packaging: dependency delivery and instance sharing

Two separate concerns. The first draft invented a manual switch for the second one, which RNS
already performs on its own.

**Dependency delivery: version pin, not vendored source.**

`pyproject.toml` declares `rns` and `lxmf` as pinned versions, currently as ranges
(`rns>=1.5.4,<2.0`, `lxmf>=1.1.1,<2.0`). Tighten these to exact pins for releases so that a
published version of the adapter always runs the RNS/LXMF it was tested against, and upgrade
by bumping the pin and cutting a release. A range is a small hole in the reasoning below: it
can resolve to a version whose source nobody here has read, which is the same exposure the
vendoring argument rejects. Exact pins close it.

The alternative, copying RNS/LXMF source into this repository, is rejected on licence
grounds. Both are under the Reticulum License, which is MIT-shaped but carries two added
conditions: no use in systems able to harm humans, and this one:

> The Software shall not be used, directly or indirectly, in the creation of an artificial
> intelligence, machine learning or language model training dataset, including but not
> limited to any use that contributes to the training or development of such a model or
> algorithm.

Running RNS/LXMF as a dependency is fine. Vendoring their source in-tree is also permitted,
but it places their code inside a public repository that crawlers may take for training
corpora, and that use is the one the licence forbids. We cannot control who crawls a public
repository, so the exposure is not ours to manage. A version pin in `pyproject.toml` contains
none of their code, satisfies reproducibility, and gives users a clean upgrade path: bump the
pin, release, users `pip install -U`.

**Instance sharing: RNS handles it. The adapter does not switch anything.**

RNS has first-class shared-instance support and figures this out itself. `Reticulum.__init__`
accepts `require_shared_instance` and `shared_instance_type`, and `__start_local_interface`
tries to become the shared instance first and falls back to attaching as a client when one is
already running (Reticulum.py:397-451):

- It attempts a `LocalServerInterface` on the local socket (398-419). Success means this
  process IS the shared instance.
- If that raises, it falls back to a `LocalClientInterface` and sets
  `is_connected_to_shared_instance = True` (420-441). This is the path a second program takes
  when `rnsd` or another app already owns the socket. It logs "Connected to locally available
  Reticulum instance".
- If neither works it becomes a standalone instance (443-448).

So a bridge process and an `rnsd`, or the bridge and NomadNet, already share one RNS instance
today without either being told to. The observable states are `is_shared_instance`,
`is_connected_to_shared_instance`, and `is_standalone_instance`, and RNS logs which one
applied.

**The shared instance creates an implicit version coupling, and the adapter must report it.**
Which process owns the shared instance is decided by start order, not by configuration: each
process tries to become the shared instance first and the one that wins owns the interface for
everyone attached. If the adapter and a co-resident `rnsd` run different RNS versions, the
first process up defines the interface semantics for both, and nothing warns. The failure looks
like a random protocol bug weeks later.

Two requirements follow, and both are cheap:

- Pin the exact RNS version (the packaging rule above), and document that any other RNS program
  sharing the instance must match that pin. This is a deployment constraint, not something the
  adapter can enforce at runtime.
- Log the RNS version and the resolved sharing state together, in the one line section 11
  already requires for "which RNS am I talking to". Answering that question without the version
  is answering half of it.

The adapter therefore does **not** implement attach-or-own logic, and this section does not
ask it to. It constructs `RNS.Reticulum(...)` with whatever flags its config supplies and
lets RNS resolve sharing. What the adapter must do is narrower:

- Pass `require_shared_instance` through from config when a user wants to guarantee they are
  attaching to an existing instance rather than starting another. With it set and no shared
  instance available, RNS raises rather than silently standing alone, which is the correct
  loud failure.
- Report which state RNS landed in, in status output and logs, so "which RNS am I talking to"
  is answerable without reading RNS logs.
- Not fight the sharing model. Two programs on one instance is normal and supported, not a
  condition to detect or avoid.

That corrects a real error in the previous draft: it specified a config-driven manual switch
for behaviour RNS already owns, which would have produced two mechanisms fighting over the
same decision.

## 12. Announces, display name, and discoverability

All three mechanisms exist already and belong to the adapter, because they are RNS/LXMF
concerns rather than gateway ones. The gateway has no concept of announcing and should not
learn one.

**Display name.** Users name their agent on the mesh, and this already works. The name
travels as LXMF `app_data` attached to the announce, and clients decode it via
`display_name_from_app_data` (LXMF.py:151-172) to show a human-readable contact. So the name
a user sets is what peers see in their contact lists, independent of the destination hash.

Configuration carries over: `RETICULUM_DISPLAY_NAME` becomes a documented `config.yaml` key
alongside the rest (section 2's `apply_yaml_config_fn`), not an `.env` value. Behaviour
settings belong in YAML; `.env` is for secrets only.

**Name changes at runtime are possible and worth keeping.** `Destination.set_default_app_data`
accepts a *callable* as well as bytes (Destination.py:678-686), and the callable is invoked per
announce (Destination.py:290-296). So a name change can take effect on the next announce
without a restart. Worth exposing, since an agent's name is exactly the sort of thing a user
changes after deployment.

**Announce interval.** `RETICULUM_ANNOUNCE_INTERVAL` carries over as a `config.yaml` key,
default **60 minutes**. The reasoning must carry over with it, because it is not arbitrary:
RNS drops destinations from other nodes' path tables over time, so a long-lived agent that
never re-announces becomes undiscoverable. `0` disables periodic re-announce, which is a
legitimate choice for a node that only ever receives.

The 60-minute default is deliberate rather than inherited. The bridge currently defaults to 30;
one hour is the community norm and avoids adding announce traffic to nodes that are already
busy carrying their own. Announcing too often is a social problem on a shared medium, not just
a local one: every announce is heard by every node in range.

**The interval floor is load-bearing and must be preserved.** The bridge clamps to
`MIN_ANNOUNCE_INTERVAL_MIN = 1.0` minute and rejects invalid values loudly at startup rather
than running at the wrong cadence. Below the floor the re-announce becomes a spam loop that
burns both the bridge and the mesh. A reimplementation that drops this guard reintroduces a
known-bad condition, so it goes in the spec rather than the adapter author's discretion.
Invalid values must fail at startup, not silently fall back.

**What does not carry over: the `/announce <minutes>` mesh command.** The gateway owns command
dispatch. A live cadence override becomes a config change plus a re-announce, not a slash
command, unless a real need for mid-session changes appears. Named here so the loss is a
decision rather than an oversight.

**Announcement beyond the interval.** Two things the adapter should pass through rather than
reimplement:

- `Destination.announce()` itself (`app_data`, `path_response`, `attached_interface`, `tag`)
  for a manual announce at startup, which the bridge already performs.
- A one-off announce whenever the display name changes, so peers see the new name without
  waiting a full interval.

Announce timers are not shared with the gateway, and nothing about this section interacts with
the gateway's own scheduling.

## 13. RNS restart while the adapter is running

Behaviour here is RNS's, not ours, and it is worth stating because the failure mode is abrupt
rather than slow.

**The adapter does not poll for rnsd and should not.** There is no reattach loop to write,
because RNS already runs one inside the interface.

**What actually happens.** A connected client owns a `LocalClientInterface`. When the shared
instance's socket closes, the interface logs "Socket was closed, attempting to reconnect..."
and calls `reconnect()` (LocalInterface.py:259-265, 285-291). That method loops while offline,
retrying every `RECONNECT_WAIT = 8` seconds (LocalInterface.py:54, 153-181), and on success logs
"Reconnected socket" and fires `RNS.Transport.shared_connection_reappeared()` (line 180). So a
restarted rnsd is picked up automatically, within about eight seconds, with no adapter code.

**The edge that matters.** The retry loop is conditional on the interface being torn down
*recoverably*. `teardown()` takes the destructive branch when
`is_connected_to_shared_instance` is set: it logs "Permanently lost connection to local shared
RNS instance. Exiting now." at CRITICAL and calls `RNS.exit()` (LocalInterface.py:344-348).
That kills the whole process, adapter included, rather than reconnecting.

So the honest answers are: a transient socket drop recovers on its own within seconds, and a
hard teardown takes the process down with it. Which branch runs depends on how the connection
was lost, which is not something the adapter controls.

**What this means for the adapter.** It cannot make the hard-exit path recoverable, so it
should not pretend to. Two consequences:

- The gateway's reconnect watcher will see the platform drop when the process exits. That is
  the correct signal; `connect(is_reconnect=True)` is idempotent and re-uses the live RNS
  instance (a strict process singleton), re-initialising the LXMF layer, per section 5.
- If the adapter runs under a process supervisor, the supervisor restarting it is the recovery
  path for the hard-exit case. This is worth documenting for users rather than leaving them to
  discover it as "the agent vanished when I restarted rnsd".

**Do not add polling, retry, or supervision logic to the adapter for this.** RNS owns both the
reconnect and the exit, and a second mechanism would race the first. The adapter's job is to
be restartable and to log clearly which state it ended in.

## 14. Error taxonomy and edge cases

Content errors and transport errors are different things in this design, and the split matters
because the gateway branches on `error_kind` rather than on the error string.

**`error_kind` mapping.** The taxonomy is fixed: `too_long`, `bad_format`, `forbidden`,
`not_found`, `rate_limited`, `transient`, `unknown` (base.py:1635-1636). Anything unrecognized
must be `unknown`, never a benign default, so an unclassified failure is never mistaken for
success. The adapter sets `error_kind` explicitly rather than letting `classify_send_error`
guess from substring matching, because the strings here are LXMF's and the classifier's
substring table is built for API-server wording (base.py:1659-1678).

**What the adapter emits in practice (as of the 2026-09-29 revision).** The implemented
`send()` currently emits exactly two kinds: `transient` (FAILED receipt after 5 attempts,
and packet refusal with no path) and `too_long` (a single chunk part over the 368-byte
budget, detected by `delivery.chunk_for_send`). The other table rows are *defined* here as
the contract but are not reachable in the current code, for two reasons: the adapter's
outbound `send()` performs no allowlist check at all (inbound ACL rejection is silent,
section 7), so `forbidden` has no trigger; and no code path has yet produced `bad_format`,
`not_found`, `rate_limited`, or the `unknown` catchall. When those paths land, they must
emit the table's kinds verbatim.

| Condition | `success` | `retryable` | `error_kind` |
|---|---|---|---|
| `DELIVERED` receipt | True | — | — |
| `FAILED` after 5 attempts | False | True | `transient` |
| `Packet.send()` refused (no path, no interface) | False | True | `transient` |
| Content exceeds the size budget | False | False | `too_long` |
| Destination hash malformed or wrong length | False | False | `bad_format` |
| Peer not in the allowlist | False | False | `forbidden` |
| Peer identity unknown, no path findable | False | False | `not_found` |
| Propagation node accepted (`SENT`) | True | — | — (see section 6) |
| Anything unclassified | False | False | `unknown` |

`not_found` deserves a caution: `is_chat_level_not_found` treats it as the whole chat being
gone (base.py:1681-1687). A peer that is merely unreachable is `transient`, not `not_found`.
Conflating them would make the gateway mark a live conversation dead.

**Size limits are real and small.** LXMF computes actual content ceilings from the transport
MDU rather than using a fixed constant: `ENCRYPTED_PACKET_MAX_CONTENT` for opportunistic
single-packet delivery, `LINK_PACKET_MAX_CONTENT` for link delivery, and
`PLAIN_PACKET_MAX_CONTENT` for plain destinations (LXMessage.py:65-95). The practical
consequence for the adapter:

- Messages above the single-packet ceiling cannot go opportunistically. The adapter should
  chunk, or route over a link, rather than fail.
- The gateway's `MAX_MESSAGE_LENGTH` default of 4096 is an upper bound only and is larger than
  a single LXMF packet can carry. `splits_long_messages = True` (section 2) is what makes the
  gateway hand over content the adapter can then split sensibly.
- The 368-byte per-block content budget is the existing evidence of what actually fits after
  chunk-prefix overhead. Carry the chunking logic over; do not re-derive the budget. It lives
  in `delivery.chunk_for_send`, which owns both the budget constant and the `[tag i/N]`
  prefixing; the adapter's `send()` calls it and sends one packet per part.

**Empty and degenerate input.** Empty text, whitespace-only, and a message that chunks to a
single empty part must not produce an empty announce or a zero-length packet. Define the
behaviour rather than leaving it to chance: an empty reply is a bug in the turn, not something
to send. Log it and send nothing.

**Chunk output contract.** Every part from `chunk_for_send` must be non-empty, must fit
368 bytes including its `[tag i/N]` prefix, and the parts must reassemble to the original
text byte-for-byte. Whitespace-only parts render as empty bubbles on MeshChatX (seen live
between p12 2/7 and 3/7), so the chunker folds blank runs into neighbouring parts' slack
while preserving byte order — the run's front rides onto the previous part, its tail onto
the next, the middle keeps its place as budget-sized slices. A run with no content-side
neighbour (e.g. a leading indent wider than the budget) still ships as its own part — byte
preservation outranks cosmetics, but an over-budget part never ships. `sequence_chunks`'
truncation is a guard rail, not a policy: if it ever fires, that is a chunker bug to fix
upstream, not content to drop.

**Malformed inbound.** A message whose destination hash is not 32 hex characters, whose payload
fails to decode, or that arrives from a peer not in the allowlist must be dropped with a log
line and no reply. Do not reply to unparseable input, and do not crash the drain loop on it.
The reference behaviour exists in the mesh ACL's validation, which already rejects hashes that
are not 32 hex characters.

**Inbound ACL rejection is silent by design, and the spec says so explicitly.** The implemented
`handle_message` drops disallowed peers with a log line and returns no reply. This is the
correct behaviour for a mesh: an unknown peer with a valid identity should not learn that the
agent is up (an echo would be a liveness oracle). The cost is that a user who mistyped their
hash into the allowlist gets no feedback, and the only signal is the log line on the host.
That is an accepted limitation of the inbound path, recorded here so a future fix is a
decision rather than an accidental change.

**Stamps.** `stamp_cost` (default 8) and `enforce_stamps` carry over as config. When stamps are
enforced, a message failing stamp validation is dropped, not retried. Worth documenting, since
a peer with a misconfigured client will look like a peer that is ignoring the agent.

**Announce values.** Non-finite intervals (`nan`, `inf`) must be rejected explicitly, not just
negative ones: they pass a naive range check and then break the scheduler. The bridge already
handles this case, and the check belongs in the spec so it survives a rewrite.

## 15. Open questions

- Whether the propagated-delivery pending set should eventually expire entries and emit a
  failure after a chosen horizon. Needs a decision before release; not needed to start.
- Whether `standalone_sender_fn` can work at all for out-of-process cron delivery, since a
  cron job in a separate process needs its own RNS. Note this interacts with section 11: a
  separate process can connect to the same shared instance, so this may resolve itself, but
  the shared instance must be running for it to attach.
- Which exact RNS/LXMF versions to pin for the first release. The *divergence* half of this
  question is settled by section 11: pin exactly, document that co-resident RNS programs must
  match, and log the version alongside the sharing state. What remains open is only the choice
  of version.
- O-7: **decided 2026-09-30 (was open).** The `/new` ack names the new session's id and auto-
  title (the discoverability half of the peer's `/new` decision, §21.3). The lane name stays
  stable by design (section 18.2); Telegram's visible freshness comes from `chat_id`-keyed
  lanes, which Reticulum's key shape cannot express, so the fix is the ack text, not key
  churn. See §21.3.
- O-8: whether the mesh keeps the destructive-confirm guard. The peer asked to spec plain-text
  rendering for interactive commands, so the guard stays on (section 20, corrected: the text
  fallback works out of the box; disabling it would lose the accidental-`/new` guard for no
  benefit). The open decision is narrower: how terse the confirm/clarify render is (§20.3),
  and whether the peer wants the guard on at all once the terse render lands. Decision
  requested from the peer after the phone-exercise check.

## 16. Invariants the adapter must not break

From the gateway's contract, each with a known failure behind it:

A send result is binary: done or failed. No third state may be invented.
`connect()` returning True asserts that inbound will be pushed.
Profile-scoped env reads fail closed and never fall back to `os.environ` under multiplex.
Approval and control commands must bypass both message guards to reach the runner.
Adapter acceptance is at-least-once admission, not proof of delivery.
The adapter's pending set is the authoritative delivery record for propagated sends; the
gateway's ledger will be wrong in both directions and is never written to or argued with.
`approvals.timeout` ships at 900 for this platform, documented, or every mesh approval fails
closed at the 300s default.

## 17. Implementation deltas (2026-09-29 revision)

What had to be patched in between the 2026-09-24 draft and the implemented branch, with the
reason each was needed. This section is the record for future readers; it does not change
any design decision above, only documents what the draft did not foresee.

### 17.1 The adapter never constructed: `is_connected` gate

The first implementation of the `is_connected` platform-entry hook read a runtime flag that
nothing ever set. The plugin system calls `is_connected` before constructing the adapter, so
the gate was permanently `False` and `connect()` never ran. The fix was to implement
`is_connected` as `_validate_config(config)` returning `True`, because every config field has
a default and the only real failure mode is a missing identity file, which `connect()`
surfaces. This is the IRC reference plugin's contract, and it was the first live bug found.
The draft's `_shared.env_is_connected(...)` helper is therefore not used; the adapter
validates config directly.

### 17.2 `SUPPORTS_MESSAGE_EDITING=False` (commit 6134de5)

The gateway streams token deltas as separate messages for adapters that do not declare
message-edit support. LXMF has no edit API, so leaving the flag at its `True` default turned
one reply into a burst of partial messages, one per token delta. The fix sets
`SUPPORTS_MESSAGE_EDITING=False` on the adapter, which suppresses the delta path and leaves
the single final send. This flag does not appear in the gateway's documented capability
table (section 2); it is read via `getattr` with a `True` default, which is why it bites
silently rather than loudly. Section 2's table now records it.

### 17.3 Thread-bridge callback split (commit 798bbd6)

The draft described one shared `LXMFSession` with one callback slot. The implementation
required two: the peer's `session` destination carries inbound (peer → agent), and the peer's
`thread` destination carries outbound (agent → peer, via `Packet.send`). With both bound to
the same slot, inbound messages were being consumed as outbound replies and never dispatched
to the agent. The fix split the slots so each direction has its own callback, and the bridge
now uses a queue plus an asyncio drain task (`connect()` enqueues `thread_send`; the drain
task awaits each send on the transport loop). `connect()` is idempotent; a call with
`is_reconnect=True` tears down and rebuilds the session, the thread, and the drain task.
See `docs/mesh-bridge-findings-2026-09-26-reply-never-dispatched.md` for the instrumented
resolution of the earlier drop.

### 17.4 Chunk budget location

The draft named `_BLOCK_CONTENT_BUDGET = 368` as a constant on the adapter. The implementation
put the budget and the `[tag i/N]` prefixing in `delivery.chunk_for_send`, which the adapter's
`send()` calls and then sends one packet per part. The 1024-byte `max_message_length` on the
platform entry (registration.py) is what the gateway passes to `splits_long_messages`: it is
the gateway-side ceiling, not a transport constant. The 368-byte per-block budget in
`delivery.py` is the only real size limit, and it is enforced by `chunk_for_send` returning
fewer parts when the content cannot be split to fit. There is no `MAX_PART_BYTES` constant.

### 17.5 Config still arrives from env, not from YAML — CLOSED

Section 12 requires `apply_yaml_config_fn` to own the YAML schema, and section 2's hook list
requires `RETICULUM_DISPLAY_NAME` and `RETICULUM_ANNOUNCE_INTERVAL` to be `config.yaml` keys.
Neither is wired yet: the adapter reads both from env via `get_scoped_secret`, and
`apply_yaml_config_fn` is not registered on the platform entry. This is a known gap, not a
silent one. The env path works today, so phone-based testing can proceed without it, but the
YAML hook must land before this platform is shipped as a public plugin.

Closed on card A7: the platform entry registers `apply_yaml_config_fn`, table-driven through
the gateway's `_shared.apply_yaml_bridge`, so `display_name` and `announce_interval` are
`config.yaml` keys — `config_loader.platform_section` accepts them under `platforms.reticulum`
or a top-level `reticulum:` block. The hook bridges each key to the env name the transport
already reads and seeds the raw YAML value into `PlatformConfig.extra`, which the transport's
resolution chain reads first. Precedence is the gateway's contract, not a plugin-local rule:
an operator value in `.env` or a secret source wins over the YAML key, and under a multiplexed
secondary profile the hook seeds that profile's extra and writes no env. The display name
remains the enable switch (D1) at the plugin gate: the YAML key satisfies `_env_enablement()`
because it bridges `RETICULUM_DISPLAY_NAME`. That gate is not end-to-end — the gateway also
activates the platform on the mere presence of a `platforms.reticulum` block, and
`is_connected` returns `True` unconditionally (section 2), so a block carrying only
`announce_interval` currently comes up announcing under the transport default name. Closing
D1 across both layers is a separate card (issue #16), not this paragraph's claim. The remaining
`RETICULUM_*` names (storage path, config path, allowlists, home channel) stay env-only; no
ruling widens them into YAML.

### 17.6 `approvals.timeout=900` gap — CLOSED

Section 7's approval row and section 16 both require the 900-second window to ship as this
platform's documented default. The gateway's default is 300s; without the 900s value, mesh
approvals fail closed at 300s, which is too short for LoRa hop latency and AFK users.

Closed: the plugin ships `approvals.timeout: 900` in `plugins/reticulum/config.yaml` with the
LoRa rationale, and `install.sh` applies it to the user's `config.yaml` on a fresh install
(leaving an existing user value untouched).

### 17.7 `standalone_sender_fn` not registered

Section 2 requires this hook for out-of-process cron delivery. It is not registered on the
platform entry. This is a known gap, documented in section 15, and is not a blocker for
phone-based testing or in-process operation. The design question (whether a separate process
can attach to the shared RNS instance) remains open.

### 17.8 Inbound ACL rejection is silent

> **Implemented on card A3 (2026-10-12):** the decided form is now the shipped code. The
> implementing ruling record is `docs/specs/adapter/a3-spec.md` (card A3): single `.env`
> gate via `core/acl.py` `AccessControl`, closed default, per-destination rejection
> cooldown, host-local full-hash deny logs, and the D7 gateway-desync signal. The text
> below records the pre-A3 gap and stays as history.
>
> **Amended 2026-10-24 (owner ruling):** the ACL knobs are read PROFILE-SCOPED via
> `platform_gate_env` (single-profile == the `.env`; multiplex == per-profile bridged
> values) — a single process-global `.env` is the source only in single-profile
> deployments. Per-profile Reticulum identity + per-profile ACL is an intended goal
> (mesh leans TCP; co-resident multi-profile bridges are target). See the dated
> review-amendment in a3-spec.md §2.

> **Ruled 2026-09-30 (peer):** this stays true only as a description of the *implemented*
> code. The decided behaviour is an **explicit automated rejection** like the original bridge
> (`⛔ Access not authorized.`, the `core/acl.py` + `cli.py` path: `is_allowed()` denies, the
> caller returns the literal reply text, and Hermes is never reached). Authorization is a
> code-level allowlist decision and must never be delegated to the model, so an unauthorized
> peer cannot prompt-inject its way into a turn. The decided form supersedes the
> liveness-oracle argument below. Full decision text: §21.3; code change deferred (§21).

The draft's section 14 table includes a `forbidden` row for allowlist denial. The implemented
`handle_message` drops disallowed peers with a log line and no reply, and the outbound
`send()` performs no allowlist check at all. This is the correct inbound behaviour (an echo
would be a liveness oracle), but it means the `forbidden` row has no trigger in the current
code. Section 14 now records this explicitly, and section 14's "what the adapter emits"
paragraph lists only the three kinds the code actually produces.
**Superseded as a decision by §21.3 (2026-09-30): the adapter replies with an automated
rejection instead of dropping.**

### 17.9 `mesh-tool-events` hook targets a deleted control server

The old bridge's `mesh-tool-events` hook POSTs tool-call steps to the bridge's control server
on port 8471. That server no longer exists under the adapter model. The hook still fires and
still swallows the `Connection refused` error (it is passive and runs on a daemon thread), so
it cannot block the final send, but tool progress is not surfaced to the phone. The fix is to
replace the HTTP push with the gateway's own tool-event surface (`display.platforms.reticulum`
`tool_progress: new`), which the 2026-09-26 findings document already identifies as the
intended replacement. This is a separate, never-built feature, not a regression.

**Correction (2026-10-26, `mesh-bridge-findings-2026-10-26-adapter-live-delivery-one-shot.md`):**
before any progress surface is built, the *global* default (`display.tool_progress: all`)
already streams tool-progress and thinking ticks to the mesh as separate one-shot LXMF
messages, and that streaming marks the turn as delivered so the model's real reply is held.
Fixed by the per-platform override down to **off** (not `new`):

```yaml
display:
  platforms:
    reticulum:
      tool_progress: false
      thinking_progress: false
```

`tool_progress: new` the original text proposes remains a *future* toggle for when a
first-class progress surface exists; today the working setting is `false`. Also note: the
memory-recall indicator (`agent/memory_manager.py` `describe_recall()`, a lifecycle status
event) is transmitted as a real message on the one-shot mesh — see the findings doc.

### 17.10 `/new` and gateway command dispatch — verified 2026-09-29

The draft claimed the gateway's command dispatch intercepts messages before the adapter's
`handle_message`. **This is backwards.** The real flow (verified in source):

1. Inbound RNS packet → `InboundQueue.run` → `PlatformBase.handle_message` (base.py:1160) →
   `deliver_inbound` (adapter.py:280) → ACL check → `_message_handler(event, source)`.
2. `_message_handler` is the gateway's `_handle_message` (run_adapters.py:1570, set as
   `_primary_message_handler`). This handler:
   a. Checks `event.is_command()` (event.py:99: `allow_gateway_control and text.startswith("/")`).
   b. Calls `_hm_admit_event(event)` (run_inbound.py:161) — auth gate:
      `_is_user_authorized_for_source(source)` (run_inbound.py:231). For reticulum, this
      checks `HERMES_RETICULUM_ALLOWED_USERS` (registration.py:149). **If the user's RNS
      hash is not in that env var, the message is dropped here before command dispatch.**
   c. Calls `_hm_dispatch_idle_commands(event, source, _quick_key)` (run_inbound.py:1306).
3. `_hm_dispatch_idle_commands` (run_inbound.py:1221):
   a. `_hm_resolve_command` (run_inbound.py:793): calls `is_gateway_known_command("new")`
      (commands.py:405). `/new` IS registered (commands.py:55, `busy_policy="interrupt_then_dispatch"`),
      so this returns True.
   b. `_hm_command_hooks` fires (mesh-tool-gate hooks may intercept).
   c. Falls to `_hm_dispatch_canonical_command` → `_hm_cmd_new` (run_inbound.py:848) →
      `_handle_reset_command`.

**The wiring is correct.** The adapter's `deliver_inbound` calls `_message_handler`, which
reaches command dispatch. `/new` works through the standard path.

**Most likely cause of `/new` not working from the phone:**
- The user's RNS hash is not in `HERMES_RETICULUM_ALLOWED_USERS`, so the message is
  dropped at the auth gate (run_inbound.py:231) before command dispatch.
- Alternatively, the NomadNet page sends the text in a format that doesn't preserve
  the leading `/` (e.g., it's wrapped in a block or the page's send function
  strips it).

**Diagnosis steps (for the user to run from the phone):**
1. Send `/help` from the phone. If it responds, command dispatch is working and `/new`
   should also work. If it doesn't, the text is being mangled in transit.
2. Check `HERMES_RETICULUM_ALLOWED_USERS` on the gateway: `echo $HERMES_RETICULUM_ALLOWED_USERS`.
   The phone's RNS hash must be in that list.
3. Check the gateway log for the inbound message: `journalctl --user -u hermes-gateway --since "5 min ago" | grep -i "reticulum\|command\|new"`.

**Spec fix:** the draft's claim was wrong. The adapter's `deliver_inbound` delegates to
the gateway's handler directly. No adapter-side fix is needed for `/new` to work; the
fix is ensuring the phone's hash is in `HERMES_RETICULUM_ALLOWED_USERS` and the text
arrives with a leading `/`.
