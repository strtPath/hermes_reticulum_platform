# A2 — Pin `model.context_length` below the live `n_ctx` + silence the dead mesh step POST

Status: DRAFT (for review)
Card: A2 from `docs/specs/adapter/tasks.md`
Unblocks: A4 (keystone) depends on a clean peer-facing turn; A2 removes the two things that
currently break or muddy that turn (silent context overflow, and a dead HTTP round-trip on
every tool step).

Both root causes below are verified against the live source as of this writing.

---

## 1. Problem (from the user's perspective)

Two independent defects, both invisible to the operator until a turn goes wrong:

**Part A — the context window is not pinned.** A long multi-tool turn on the local
qwen3.8-27b can exceed the model's *actually allocated* `n_ctx` while Hermes believes the
window is the much larger declared training window. When that happens the model's context
overflows mid-turn: the turn is dropped or the model emits a truncated/garbled tail, and
nothing surfaces it on the mesh (the adapter already swallows the failure into a bare text
reply). The operator sees a turn that "just stopped working" with no signal for why.

Intended behaviour in one sentence: **Hermes resolves the context window to the value the
llama.cpp slot is actually allocated (`n_ctx`), not the declared model window, so a turn
that would overflow is caught before it happens instead of failing silently mid-stream.**

Observable symptom: long turns on the local 27B occasionally return a truncated or empty
reply with no error surfaced; the gateway log shows a context-window mismatch only in
retrospective, not at request time.

**Part B — every tool step makes a dead HTTP POST.** For every tool step in a mesh turn, the
`mesh-tool-events` hook fires and POSTs a step summary to `CONTROL_URL` (module constant,
`handler.py:49`), defaulting to `http://127.0.0.1:<control-port>` (a loopback host:port). That control server was part of the
retired standalone bridge; it is not running under the adapter. Every POST therefore fails
(404 or connection-refused) and is swallowed, so the hook does a dead network round-trip on
the hot path of every tool step, with zero functional effect.

Intended behaviour in one sentence: **no step event is emitted at all when no control server
is configured (the adapter has no such server), so the hook is a no-op rather than a dead
round-trip.**

Observable symptom: a `WARNING` from `http.client` / the hook in the gateway log on each
step, plus a small per-step latency bump from a failed connection. No functional effect —
but it is a hot-path defect that A6 (tool-step parity) must not inherit.

---

## 2. Root cause (diagnosis)

### Part A — why the window is not pinned

The context window is resolved by `get_model_context_length()` in the installed Hermes
(`~/.hermes/hermes-agent/agent/model_metadata.py:2245`). The resolution order, verbatim from
the docstring:

> "Resolution order: 0 config override / MoA aggregator / model_overrides /
> custom_providers / endpoint-scoped; 1 persistent cache ...; 2-3 custom endpoints
> (/models, local probe, Ollama); ... 8 hardcoded defaults; 9 256K fallback."

The **priority-0 explicit config override** is the lever A2 uses. First statement in the
function body (model_metadata.py:2245, top of the function):

```python
# 0. Explicit config override — user knows best
if isinstance(config_context_length, int) and config_context_length > 0:
    return config_context_length
```

i.e. a `model.context_length` in `config.yaml` is returned **immediately, before any
cache, probe, or default**. That is exactly the behaviour we want: it pins the window to a
known-correct value and bypasses the lower-priority probes that would otherwise report the
declared training window (larger than the live slot).

**This is a LIVE path, not an install-time one.** The gateway reads `model.context_length`
from config.yaml **at every inbound/turn**, not once at install:
- `gateway/run_inbound.py:1652` (`_inbound_model_context_length`) reads
  `model.context_length` from the live gateway config and passes it as
  `config_context_length` into `get_model_context_length_async` (run_inbound.py:1705).
- `gateway/run_turn.py:647` reads it into `hs.config_context_length` and
  `run_turn.py:743` passes it to `get_model_context_length_async` on the turn path.
- There is a runtime setter, `set_config_context_length(agent, value)`
  (`agent/agent_init.py:1773`), that updates `agent._config_context_length` and the
  compressor's copy without a restart.
- A disagreement guard already exists: `warn_once_on_pin_disagreement`
  (`agent/context_pin.py:56`) compares the pin against the advertised window and warns once
  when they differ.

So the operator does **not** need install-time resolution. They surface the live `n_ctx`
(via `/props`, the server banner, or the `/new`-time resolution the gateway already does),
set `model.context_length` to that value, and the next turn applies it immediately. The pin
is a config value with a live resolver, not a value baked in at install.

**The defect is that nothing sets the override.** The live `~/.hermes/config.yaml` `model:`
block is:

```yaml
model:
  default: <local-model>
  provider: custom
  base_url: <local-llamacpp-endpoint>
  api_mode: chat_completions
```

No `context_length` key. So the resolver falls through priority 0 and lands on the
endpoint-scoped / local-probe / cache / default path. On a llama.cpp endpoint the resolver
*does* have a reconcile path: `_apply_llamacpp_props` (model_metadata.py:974) reads the
allocated `n_ctx` from `/props` (or `/v1/props`) and overwrites the cached `context_length`
with it (model_metadata.py:990-991, 1003). So the resolver can report the correct live slot
when that path fires.

But that reconcile path is **conditional**, and it can silently miss: it only rewrites the
cache when `n_ctx` and a `model_alias` are both present and the alias is already in the cache
(model_metadata.py:990-991); it skips unloaded children in router mode (model_metadata.py:
"unloaded children are skipped"); it falls back to `/props` only if `/v1/props` errors; and if
the endpoint isn't recognized as a llamacpp server, or the props call 404s, or the model_alias
isn't in cache, the rewrite never happens. In those cases the resolver falls back to the
endpoint's declared window (the GGUF `context_length` / `model_info.*.context_length`, the
model's trained max, model_metadata.py:570-591, 915) — which is ≥ the live slot. A turn whose
prompt+tools+output exceed the live `n_ctx` therefore overflows, and the adapter swallows it
(A8 note: the adapter swallows gateway exceptions into a bare text reply).

The pin is the deterministic complement: `model.context_length` is priority 0 and is read
from the live config at every inbound/turn, so it does not depend on the reconcile path
firing, the props call succeeding, or the cache. It is the value the operator controls.

**Intended logic vs what is there.** Intended: `model.context_length` in config.yaml should
equal (or be safely below) the live `n_ctx` so the resolver returns priority 0 and the turn
is size-checked against the real slot. What is there: no such key, so the resolver trusts
whatever the endpoint/probe reports (the declared window), which is ≥ the live slot.

**Where the live value comes from.** The operator reads `n_ctx` from the running llama.cpp
endpoint (`/props` or the server banner) — or simply lets the gateway's own live resolution
surface it at `/new`/turn time and reads the result. The pin is `n_ctx` (the full allocated
window) or, conservatively, `n_ctx` minus a margin reserved for the next turn's growth.
This is a *deployment* value, not a code constant: the operator sets the number, the
resolver applies it live.

### Part B — why every step POSTs to a dead URL

The step emitter is the `mesh-tool-events` hook, installed at
`~/.hermes/hooks/mesh-tool-events/handler.py`. The control URL is a **module-level
constant read once at import** (handler.py:49):

```python
CONTROL_URL = os.environ.get("HERMES_MESH_CONTROL_URL", "http://127.0.0.1:<control-port>")
```

It **has no "off" state** — the env default makes it always present and always
`http://127.0.0.1:<control-port>`. Under the adapter that URL points at the retired standalone
bridge's control server, which is not running.

Every mesh step then makes a POST. `_handle_step` (handler.py:458) resolves the gateway
session to a mesh thread and, for each tool, hits one of two `_post` call sites:

- Normal (recap) mode — handler.py:520: `_post("/step", {...}, timeout=10.0)`.
- Step-through mode — handler.py:405 (via `_post_full_step`, handler.py:374):
  `_post("/step/full", {...}, timeout=FULL_STEP_POST_TIMEOUT)`.

`_post` (handler.py:205) builds `url = f"{CONTROL_URL}{path}?token={tok}"` and does a
`urllib.request.urlopen` with a short timeout. The only early-out is at handler.py:207:
`if not tok: ... return None` — i.e. **no control token ⇒ skip**. So today the hook's real
on/off seam is *the token file*, not the URL. The URL is never gated: whenever a token
exists the hook POSTs to `CONTROL_URL`, which under the adapter is a dead address. The
failure is logged at `logger.warning` (handler.py:225) and swallowed, so a live token file
produces a failed round-trip on the tool-step hot path with zero functional effect.

**Grounding (verified live on this box):** the control token file
`~/.lxmf/storage/control_token` **is present** (left over from the bridge era), and
`HERMES_MESH_CONTROL_URL` is **not** set in the gateway env or any service unit. So the
hook is currently firing dead step POSTs on this box — the Part B red-before test is real,
not hypothetical.

**The defect, precisely:** `CONTROL_URL` (handler.py:49) has no "absent" state, and `_post`
has no "no control server configured" guard — its only skip condition is "no token". The env
default forces the URL to be present, so the hook always emits to a URL that does not exist
under the adapter.

**Intended logic vs what is there.** Intended: the hook emits only when a control server is
actually configured (an operator set `HERMES_MESH_CONTROL_URL` to a live control server),
and is a clean no-op otherwise. What is there: the default forces the URL to be present, and
the only skip is the token, so the hook emits to a dead URL.

**Contributing factor (seam candidate, NOT the cause):** the hook is *installed* in the
shared `~/.hermes/hooks/mesh-tool-events/` directory (a copy install.sh manages, paralleling
the plugin copies at install.sh:149). Because it was written for the retired bridge, the
"always-present URL + gate-on-token" design was correct for the bridge era and wrong for the
adapter. The fix is to make "no control server" a first-class state, not to delete the hook
(it still serves the legitimate step/recap audit trail once a live control URL is
configured).

---

## 3. User stories

Core behaviour:
- **US-1.** As an operator running the mesh adapter, I want to set `model.context_length`
  to the live `n_ctx` so that a turn that would overflow the allocated slot is caught by
  Hermes before it runs instead of failing silently mid-stream.
- **US-2.** As an operator, I want the pin to be a **live** config value: I set it (via
  whatever command surfaces `n_ctx`, e.g. the `/new`-time resolution), and the next turn
  applies it immediately — no install-time read and no restart.
- **US-3.** As an operator, I want the pin to be a *deployment* value I read from the
  endpoint (`/props` → `n_ctx`), not a guess, and I want the mechanism (priority-0 override
  at `model_metadata.py:2245`) documented so I understand it wins over the probes.

Correctness and precision:
- **US-4.** As an operator, if I change `model.context_length` while a session is running,
  the change takes effect from the next turn (the resolver reads the live config at
  inbound/turn time), and the existing `warn_once_on_pin_disagreement` warns me once if the
  new pin disagrees with the advertised window.
- **US-5.** As an operator, I want the step hook to be a no-op when
  `HERMES_MESH_CONTROL_URL` is unset, so a fresh adapter install (which sets no control URL)
  performs zero dead HTTP calls on the tool-step hot path.
- **US-6.** As an operator, I want the step hook to *still work* (POST steps) when I do set
  `HERMES_MESH_CONTROL_URL` to a live control server, so the fix does not break the
  legitimate audit path.

Edge and degenerate states:
- **US-7.** As an operator, if I leave `model.context_length` unset (e.g. I haven't read
  `n_ctx` yet), the resolver falls through to the probe/cache/default path exactly as
  before — the pin must not break the no-pin case.
- **US-8.** As an operator, if I set `HERMES_MESH_CONTROL_URL` to an empty string or an
  obviously-invalid value, the hook treats it as "no control server" (no-op), not as a URL
  to POST to.
- **US-9.** As an operator, the `/step` and `/step/full` events (and any other `_post` call
  in the hook) must share one "is a control server configured?" decision, so I don't get a
  partial audit trail (some events sent, others not).

Negative / unusual data:
- **US-10.** As an operator, if `n_ctx` is read from `/props` and the endpoint reports a
  value *smaller* than the declared window, the pin uses the smaller (live) value — the pin
  is what the slot actually holds, never the declared max.
- **US-11.** As an operator, the pin is an integer token count (tokens, not characters or
  bytes); the resolver consumes `config_context_length` as `int` and compares `> 0`, so a
  non-positive or non-integer value is simply ignored (treated as "no pin") rather than
  erroring.

---

## 4. Test seams

Part A (context pin) — test surface:
- The resolver contract (read-only, no test of installed Hermes code): the observable
  behaviour is that a set `model.context_length` short-circuits resolution at priority 0 and
  is re-read from the live config at each inbound/turn. Verified by reading
  `model_metadata.py:2245`, `gateway/run_inbound.py:1652`/`1705`,
  `gateway/run_turn.py:647`, and `agent/agent_init.py:1773`. Not unit-tested in this repo —
  the seam is the gateway's resolved context at request time (see Testing decisions).
- **No install.sh test for Part A.** The pin is a live config value, not an install-time
  write. (An optional convenience write in `install.sh` is out of scope; see below.)

Part B (dead step POST) — test surface:
- `~/.hermes/hooks/mesh-tool-events/handler.py`:
  - The control-URL "off" decision — invariant: "off" when `HERMES_MESH_CONTROL_URL` is
    unset **or** empty **or** not a syntactically plausible URL; "on" only for a valid
    URL. (Today the only seam is the token at handler.py:207; the URL is never gated.)
  - `_post` (handler.py:205) and its two call sites in `_handle_step` (handler.py:520 for
    `/step`, handler.py:405 for `/step/full`) — invariant: when "off", `_post` is never
    called (zero HTTP). When "on", each call site still POSTs exactly once with the current
    payload.
- Sibling invariants that must stay green (not part of the fix, must not break):
  - The `mesh-tool-gate` *pre-exec* plugin (`src/hermes_reticulum/mesh-tool-gate/__init__.py`)
    is a **different** component (a blocking POST to `/gate/notify` before a tool executes).
    A2 must not touch it. Do not confuse the gate with the step emitter.
  - The adapter's `send()` / `on_user_message()` / `on_incoming_message()` paths
    (`src/hermes_reticulum/reticulum_platform_adapter.py`) are unrelated to A2 and must stay
    unchanged.
  - `plugins.enabled` in `~/.hermes/config.yaml` (reticulum + mesh-tool-gate) must remain
    enabled.
  - The turn-alive liveness marker (`_write_turn_alive_marker`, handler.py:150) must keep
    writing regardless of the control-URL state — it is a file write, not an HTTP POST, and
    is not gated by A2.

---

## 5. Implementation decisions

Part A:
1. The fix is **operational, not code**: the operator sets `model.context_length` in
   `~/.hermes/config.yaml` to the live `n_ctx` (read from `/props` or surfaced by the
   gateway's live resolution at `/new`/turn time). The live resolver then applies it at
   priority 0 on the next turn. No source change required.
2. Document (in the adapter plan, next to A2) the live-path mechanism: priority-0 override
   (`model_metadata.py:2245`), the inbound/turn re-read (`gateway/run_inbound.py:1652`), the
   runtime setter (`agent/agent_init.py:1773`), and the disagreement warning
   (`agent/context_pin.py:56`). This is the "why it works" note for the next operator.
3. The numeric value is a deployment value, not a code constant. No hardcoded token count in
   source; no install-time read.
4. **Out of scope (no gold-plating):** an optional `install.sh` convenience that writes a
   default `model.context_length`. It is not needed for the fix (the live path covers it),
   and it would introduce a value the operator may not have verified against the live
   `n_ctx`. Revisit only if the operator wants a bootstrap default.

Part B:
1. Introduce a single `_control_server_configured()` predicate in `handler.py`. It returns
   True only when `HERMES_MESH_CONTROL_URL` is set to a non-empty, syntactically plausible
   URL; False for unset, empty, or invalid. Replace the module-level `CONTROL_URL` default
   (handler.py:49) so there is an "off" state instead of a forced `http://127.0.0.1:<control-port>`.
2. Guard the two `_post` call sites (handler.py:520 and handler.py:405) — and any future
   `_post` call — on that one predicate: if not configured, return without calling `_post`
   (zero HTTP). One predicate, both call sites (US-9).
3. Preserve the on-path exactly: when a valid URL is configured, each event type still POSTs
   once with the current payload and the existing timeout / token / `logger.warning`
   semantics unchanged (US-6, sibling invariant).

Explicitly NOT being changed (out of scope, no gold-plating):
- The `mesh-tool-gate` pre-exec plugin (different component; do not touch).
- The adapter's send/receive/identity/command paths (A4/A5/A6 territory).
- The llama.cpp endpoint itself, the `.env` model pin, or the fallback provider list.
- Any change to `get_model_context_length()` or the installed Hermes resolver (A2 uses the
  existing priority-0 hook and the live resolver; it does not patch Hermes).
- The token-file early-out in `_post` (handler.py:207) — it stays as a second, independent
  skip; A2 adds the URL gate in front of it, it does not remove the token skip.

Prefactoring needed: none. Part A is operational (config value + docs); Part B is additive
(a guard on a predicate). No API signature changes; no schema migration.

---

## 6. Testing decisions

Test file naming and location:
- Part A: no in-repo unit test (the seam is the gateway's live resolution, verified by
  reading the live call sites and by the live smoke below). The deliverable is the operator
  setting the config value + the plan doc note.
- Part B: `tests/test_mesh_tool_events_control_url.py` (handler.py — drives the
  control-URL predicate and the two `_post` call sites with env set/unset/empty/invalid).

Test framework and runner: the project's existing runner (pytest, matching the sibling
`tests/` layout). No new framework.

What "passing" means:
- Part A: operator has set `model.context_length` to the live `n_ctx`; a turn sized near
  the pinned window does not truncate mid-stream; the gateway resolves the pinned value at
  request time (confirmed by the live smoke and by the resolver's priority-0 path).
- Part B: with `HERMES_MESH_CONTROL_URL` unset, empty, or invalid, the predicate is False
  and both `_post` call sites perform **zero** HTTP calls (assert via a monkeypatched
  `_post` that is never called). With a valid URL, the predicate is True and each call site
  calls `_post` exactly once with the correct path (`/step`, `/step/full`) and payload
  shape.
- "Passing" for A2 as a whole = Part B file green AND the Part A live smoke.

Feedback loop (the test that goes red on THIS bug before the fix):
- Part B red-before: with the env unset (but the live token file present, as on this box),
  drive `_handle_step` for one tool and assert `_post` was **not** called. On the current
  code (no URL gate; token present) `_post` *is* called (to the dead default URL) → the
  assertion fails red. After the gate, it passes green.
- Part A has no red-before unit test (it is a config value + live resolution); the feedback
  loop is the live smoke (next section).

Live smoke (the one thing a unit test cannot prove):
- After the operator sets the pin, run one long multi-tool turn on the mesh and confirm
  (a) no mid-stream truncation on a turn sized near the pinned window, and (b) the gateway
  log shows zero failed step-POST warnings during the turn. This is the operator's
  acceptance check; it is not automatable in-repo, so it is listed as the manual
  verification step in `tasks.md` A2.

---

## Acceptance (maps to tasks.md A2)

- [ ] Operator sets `model.context_length` in `~/.hermes/config.yaml` to the live `n_ctx`
      (a live config value, applied at priority 0 on the next turn).
- [ ] Live resolver reads the pin at inbound/turn time (`gateway/run_inbound.py:1652`) and
      applies it — verified by reading the live call sites and by the live smoke, not by
      patching Hermes.
- [ ] The plan doc records the live-path mechanism (priority-0 override, live re-read,
      runtime setter, disagreement warning).
- [ ] `HERMES_MESH_CONTROL_URL` unset ⇒ the hook performs zero step POSTs (both `_post`
      call sites no-op).
- [ ] `HERMES_MESH_CONTROL_URL` set to a valid URL ⇒ `/step` and `/step/full` still POST
      (regression guard for the legitimate audit path).
- [ ] Live smoke: one long mesh turn, no mid-stream truncation, no failed step-POST
      warnings in the gateway log.
