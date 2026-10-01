# 0004. Adapter capability flags must be declared explicitly

Status: Accepted
Source: `docs/specs/adapter/spec.md` §2, §17.2; findings `docs/mesh-bridge-findings-2026-09-*`

## Context

The gateway decides whole slices of behaviour by asking the adapter what it can do, in the
shape `getattr(adapter, "SOME_FLAG", True)`. An adapter that never declares a flag therefore
inherits the permissive default — the capability it does **not** have — silently, with no log
line.

The worked case: an adapter without a message-edit API never declared
`SUPPORTS_MESSAGE_EDITING=False`. The gateway streamed into it, every mid-turn delta left as
its own truncated message, and the clean final response was then suppressed because the stream
consumer believed it had already delivered the content. The tell was a gateway log line reading
`streamed=... content_delivered=...` followed by `Suppressing normal final send ... already_sent`.
The result was not a degraded feature but data loss, reported as success.

## Decision

Every capability flag the gateway reads with a permissive default is a claim the adapter
**must** make explicitly. Declaring `False` is a fact about the platform, not an operator
preference, so it belongs in the adapter class, not in config. The platform's actual capability
is settled against a real client, not assumed from the library's nature (LXMF renders markdown,
so `supports_code_blocks` is not False merely because the transport is text).

## Consequences

- Capability flags are declared on the adapter class itself; tests assert the attribute exists
  on the class (`"FLAG" in vars(AdapterClass)`), because a flag that has gone missing still
  passes a `getattr(...) is False` check if a base class supplies a falsy value.
- Declaring the flag is necessary but sometimes not sufficient: the actual consumer call site
  must be traced before claiming a fix. See ADR-0001's gateway-ownership principle.
- Tracked as part of the adapter build (spec §17.2 recorded the `SUPPORTS_MESSAGE_EDITING=False`
  commit).
