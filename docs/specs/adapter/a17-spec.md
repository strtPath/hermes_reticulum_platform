# A17 spec: one mesh identity per Hermes profile

Status: proposal, 2026-11-19. Both rulings landed the same day: D1 keeps the
display-name gate, D2 refuses a conflicting RNS instance setting (option A).
Nothing here is built yet. Card: `tasks.md`, "Card A17".

## The problem, as observed

A gateway with `gateway.multiplex_profiles: true` serves several profiles from
one process. On 2026-11-19 the second profile's Reticulum adapter failed every
attempt with

```
✗ reticulum error (profile: <name>): 'Attempt to register an already registered destination.'
Secondary reticulum reconnect retry in 30s (profile: <name>)
```

then 60s, 120s, 240s, 300s, 300s, 300s: the backoff never stops, and nothing in
the message says the cause is configuration rather than a bad radio.

Two facts in the code produce it.

1. `transport.py:168-171` resolves the storage path to the literal
   `~/.hermes/.reticulum-gateway/storage` when nothing sets
   `RETICULUM_STORAGE_PATH`. `expand_path` expands `~` from `HOME`, never from
   the profile's `HERMES_HOME`, so every profile lands in the same directory.
2. `transport.py:303-315` loads `<storage>/gateway_identity` and registers it
   with the process-wide RNS instance (`RNS.Reticulum.get_instance()`,
   `transport.py:274`). A destination hash is derived from the identity, so one
   identity yields one delivery hash: the second adapter asks RNS to register a
   destination the first adapter already owns. RNS refuses, correctly.

Card A11 made the ACL knobs and the display name profile-scoped and proved the
trust lists do not leak, but it left the identity directory global. "Co-resident
profiles can each carry their own identity" is therefore true only when the
operator sets `RETICULUM_STORAGE_PATH` for every profile but the first, and no
doc or default says so.

## What must stay true

- **Address stability.** An existing install must not change its LXMF address on
  upgrade. The default profile keeps its path, so its address is safe. A
  non-default profile that ran its own gateway process on the shared path is the
  one install that can move, and it gets a WARNING rather than silence; that
  case is spelled out under "Migration" below.
- **One RNS instance per process.** `transport.py:274-286` already documents and
  handles the strict singleton: a second `RNS.Reticulum()` raises, so the second
  adapter reuses the live instance. Both adapters therefore share the
  interfaces, the path table, and the announce machinery.
- **Per-identity LXMF storage.** `LXMRouter(storagepath=...)` holds ratchets and
  message queues. Two identities must not share a directory even by accident:
  the ratchet quarantine at `transport.py:296` exists because a corrupt ratchet
  dir wedges delivery, and cross-identity ratchets would be indistinguishable
  from corruption.
- **Outbound propagation is per router, downloads are not implemented.** LXMF
  would scope a download to one identity: `request_messages_from_propagation_node(identity, ...)`
  (`LXMRouter.py:579-596`) identifies to the node with that identity and asks
  for its list only. Nothing requests one today (see §5). Outbound node choice
  is per-router state (`LXMRouter.py:481-487`), so two adapters keep two settings
  without interfering. The peer ruled 2026-11-19 that sharing `rnsd` and the
  interfaces is fine: only the identities are per profile.

## Proposal

### 1. Resolve the storage path per profile

Order, first hit wins:

1. `platforms.reticulum.storage_path` in the profile's `config.yaml`, then
   `RETICULUM_STORAGE_PATH` in the profile's `.env` (unchanged);
2. legacy `RETICULUM_STORAGE` (unchanged);
3. **new default:** `$HERMES_HOME/.reticulum-gateway/storage`, where
   `HERMES_HOME` is the profile home resolved **at adapter construction**, not
   at import. A module-level constant would freeze the first profile's path and
   hand it to every later adapter in the process.

For the default profile `HERMES_HOME` is `~/.hermes`, so the resolved path is
exactly today's default and no address moves. For a profile at
`~/.hermes/profiles/<name>` the identity becomes
`~/.hermes/profiles/<name>/.reticulum-gateway/storage`.

### 2. Refuse a shared identity, loudly and once

Before creating or loading the identity, take an exclusive `flock` on
`<storage>/gateway_identity.lock` and record the resolved path in a process
registry. A second adapter that resolves an already-held path raises a named
`IdentityInUse` error carrying:

- the profile whose adapter holds the path,
- the profile that asked for it,
- the resolved path,
- the two remedies: give this profile its own path, or turn its Reticulum
  platform off.

The recovery layer must classify this as non-retryable, and `gateway status`
must show it as a configuration conflict. A permanent misconfiguration deserves
one clear line, not a reconnect loop that looks like a dead antenna. The same
lock is what makes the check correct across processes (two gateways, or a
gateway plus a stray CLI connect).

### 3. Name the second node

Default display name when a profile enables the platform through config:
`Hermes (<profile>)`. The default profile keeps `Hermes for Reticulum`.
`RETICULUM_DISPLAY_NAME` still wins when set.

**D1, ruled 2026-11-19: keep the gate.** `registration.py:97-108` continues to
return `None` from `_env_enablement()` unless `RETICULUM_DISPLAY_NAME` is set,
so a new profile turns the mesh platform on deliberately, with one line naming
itself. The derived name above is only the fallback for a profile that reaches
the adapter through config after A7 lands; it does not weaken the gate.

### 4. RNS config is process-wide (D2, ruled: refuse)

A second adapter asking for a different `rns_config_path`, or a different
`require_shared_instance`, cannot be honoured while the singleton lives: the
first adapter's instance already exists, interfaces are already attached, and
`transport.py:274-286` deliberately reuses it.

**Ruled 2026-11-19: refuse it.** The behaviour:

- The first adapter to start records its effective instance settings (config
  directory, shared-instance requirement) and the profile that owns them.
- A later adapter whose resolved settings differ raises `RnsInstanceConflict`
  naming the owning profile, both values, and the remedy: a profile that needs
  its own RNS instance runs its own gateway process.
- The recovery layer classifies it as non-retryable, and `gateway status` shows
  a configuration conflict rather than a transport fault. A permanent
  misconfiguration gets one clear line, not a reconnect loop.
- Every start logs one INFO line so the topology is visible without reading
  config: `RNS instance: config <dir>, shared=<bool>, owner=<profile>`.

Rejected in the same ruling: warn-and-continue (the per-profile key would
quietly mean something else, which is the failure class this card removes) and
a second gateway process as adapter behaviour (it stays the documented
deployment option for a genuinely separate radio setup).

### 5. Outbound propagation is per router; downloads are not implemented

Each adapter keeps its own outbound propagation policy (`auto`, `off`, or a
pinned node from `RETICULUM_PROPAGATION_NODE`, already profile-scoped) and
`outbound_propagation_node` is per-router state (`LXMRouter.py:481-487`). Two
routers therefore keep two outbound settings without interfering.

Inbound downloads are a different matter: LXMF requires an explicit
`request_messages_from_propagation_node(identity, ...)` (`LXMRouter.py:579-596`)
and does not initiate one on its own. The adapter never calls it
(`grep -rn request_messages_from_propagation_node src/` finds nothing), so
messages a peer leaves at a propagation node for any profile are not fetched
today, by either identity. When a later card adds downloads, it passes its own
delivery identity, and LXMF then asks the node for that identity's list only.

This corrects an earlier draft that proposed giving autopeering to the first
adapter only, on the claim that two routers would duplicate sync traffic. There
is no sync traffic to duplicate while no download is requested, and cutting the
secondary adapter's outbound node would have stopped its replies to pathless
peers for no saving.

## Migration and back-compat

- Default profile: identical path, identical address. Assert this in a test.
- A non-default profile that explicitly set `RETICULUM_STORAGE_PATH` or
  `RETICULUM_STORAGE`: unchanged.
- A non-default profile that ran its OWN gateway process and so connected on
  the shared path: this is the working install the earlier draft wrongly
  declared impossible. The duplicate-destination check is process-local, so two
  gateway processes never collide; a standalone profile has been running fine on
  `~/.hermes/.reticulum-gateway/storage` while using the default profile's
  identity and address. Under A17 it resolves its own path and would acquire a
  new address silently. On the first start after the upgrade it logs one WARNING
  naming the old path and the file the adapter just created, with the exact
  steps to carry the address across: stop the gateway, copy the whole old
  storage directory over `<new>`, start it again. The
  warning comes after creation on purpose: `Transport.start()`
  (`transport.py:255`) runs `os.makedirs` (`transport.py:266`) and creates the
  identity (`transport.py:303-305`) before any operator could act, so the
  message names the file to overwrite instead of asking for something
  impossible. Copy the whole storage directory, not just `gateway_identity`:
  ratchets are named per destination hash (`transport.py:469`,
  `LXMRouter.py:423`), and a peer holding the old ratchets would otherwise send
  mail the new install cannot decrypt until its own ratchets rotate. The same
  warning covers the multiplexed case, where the profile could not connect at
  all.

## Acceptance

Each row is Given/When/Then and runs in both gateway modes: real-base, and
CI-stub forced with `HOME=/nonexistent HERMES_GATEWAY_ROOT=/nonexistent
HERMES_VENV_PATH=/nonexistent` (the mechanics are in
`docs/specs/adapter/verification.md:164`).

1. Given two profiles on one box, neither setting a storage path, When both
   Reticulum adapters start, Then each has a distinct `gateway_identity`, a
   distinct destination hash, and both connect.
2. Given the default profile with nothing configured, When the adapter resolves
   its path, Then `Path(storage_path) == Path.home() / ".hermes" /
   ".reticulum-gateway" / "storage"` (compare paths, not the literal tilde
   string) and the identity file there is the pre-existing one, so the address
   does not change.
3. Given two adapters resolving the same storage path in one process, When the
   second starts, Then it raises `IdentityInUse` naming both profiles and the
   path, and the recovery layer does not schedule a retry.
4. Given `gateway status` after a conflict, When it is read, Then the profile is
   listed as a configuration conflict with the remedies, not as a transport
   error.
5. Given a profile with an explicit `RETICULUM_STORAGE_PATH`, When it starts,
   Then the explicit path wins over the per-profile default.
6. Given two adapters in one process, When both start, Then each keeps its own
   propagation node setting from its own profile and neither disables or
   reassigns the other's.
7. Given a second adapter whose resolved `rns_config_path` or
   `require_shared_instance` differs from the running instance, When it starts,
   Then it raises `RnsInstanceConflict` naming the owning profile and both
   settings, no retry is scheduled, and `gateway status` reports a configuration
   conflict.
8. Given any start, When the Reticulum adapter comes up, Then one INFO line
   states the instance owner and its settings (`config`, `shared`, `owner`).
9. Given a non-default profile whose previous storage path was the shared
   default, When its gateway starts after the upgrade, Then one WARNING names
   the old path and the new storage directory, states the copy-the-directory and
   restart steps (ratchets included), and no file is moved or deleted on its
   behalf.

## Not in scope

- Propagation downloads. The adapter requests no messages from a propagation
  node today, for any identity. Adding that, with its per-identity scope, is a
  follow-up card that is not yet numbered. A17 only has to leave each adapter's
  outbound node setting alone.
- Running two RNS instances in one process (not possible; D2 covers the
  workaround).
- Mesh-level discovery of which profile owns which address; the announce name
  and the operator's own records are the discovery mechanism.
- Transport identity migration tooling beyond the warning line.
