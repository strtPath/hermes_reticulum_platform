# A2 Part A — context-verification wire-in note

The pure module `src/hermes_reticulum/core/context_verify.py` (verdict:
`match` / `mismatch` / `endpoint-unavailable`, exactly one warning per
distinct mismatch pair, none when the endpoint is unreachable) is delivered
with tests (`tests/test_context_verification.py`). The integration is
**deferred** per a2-spec.md §5 fallback ("deliver the verified pure module +
tests + a short note explaining the recommended wire-in point").

## Recommended wire-in point

`_cmd_status` in `src/hermes_reticulum/core/commands.py` — append one line
to the `/status` output via a small helper mirroring `_append_liveness`:

```python
def _append_context_check(lines, ctx):
    from hermes_reticulum.core.context_verify import verify_context_window
    verdict = verify_context_window(
        resolved_window=<gateway-resolved window>,
        read_endpoint_n_ctx=<endpoint reader>,
    )
    lines.append(f"  Context:    {verdict}")
```

Why `/status` and not a turn-start hook:

- `/status` is the adapter's existing diagnostic surface; adding a line
  there cannot block or fail a turn and adds no new failure mode on the
  LXMF hot path (the spec's hard constraint).
- The gateway's automatic context discovery stays the source of truth; the
  adapter only observes and warns.

## Prerequisite that must be built first (out of A2's verified scope)

Two inputs do not exist in the repo yet and must not be guessed:

1. **`read_endpoint_n_ctx`** — no endpoint reader exists anywhere in
   `src/hermes_reticulum/` (grep for `n_ctx` finds only `context_verify.py`
   itself). A concrete reader (e.g. querying the inference endpoint's
   status/model-info endpoint) is a separate card.
2. **The resolved window** — the bridge spawns `hermes chat` as a child;
   the resolved window lives inside the gateway child's runtime, not in the
   adapter. A cheap observation path (gateway status surface or state.db)
   must be confirmed before wiring.

Until both inputs exist, `verify_context_window` degrades safely: the
default reader returns `None` → verdict `endpoint-unavailable` → zero
warnings, zero spam. Wiring it before the inputs exist would either invent
an endpoint contract (spec violation) or add hot-path risk.
