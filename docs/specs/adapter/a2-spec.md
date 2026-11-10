# A2 — Automatic per-conversation context discovery + silence the dead mesh step POST

Status: DRAFT v2 (supersedes the manual-pin draft; operator ruling 2026-10-08: the
adapter must find the context automatically per conversation, not rely on a manual pin)
Card: A2 from `docs/specs/adapter/tasks.md`
Unblocks: A4 (keystone) depends on a clean peer-facing turn; A2 removes the two things that
currently break or muddy that turn (silent context overflow, and a dead HTTP round-trip on
every tool step).

Both root causes below are verified against the live source as of this writing.

---

## 1. Problem (from the user's perspective)

Two independent defects, both invisible to the operator until a turn goes wrong:

**Part A — context discovery is manual, and the automatic path is not proven durable.**
A long multi-tool turn can exceed the model's *actually allocated* `n_ctx` while Hermes
believes the window is the much larger declared training window. When that happens the
model's context overflows mid-turn: the turn is dropped or the model emits a truncated/
garbled tail, and nothing surfaces it on the mesh (the adapter already swallows the
failure into a bare text reply). The operator sees a turn that "just stopped working"
with no signal for why.

Intended behaviour in one sentence: **the gateway discovers the context window
automatically from the serving endpoint on every conversation — Hermes resolves to the
value the llama.cpp slot is actually allocated (`n_ctx`), not the declared model window —
so a turn that would overflow is caught before it happens, with no operator pin required.**

Observable symptom: long turns occasionally return a truncated or empty reply with no
error surfaced; the gateway log shows a context-window mismatch only in retrospective,
not at request time.

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

### Part A — why automatic discovery was believed broken, and what live verification shows

The resolver is `get_model_context_length()` in the installed Hermes
(`~/.hermes/hermes-agent/agent/model_metadata.py:2245`). Resolution order, verbatim from
its docstring:

> "Resolution order: 0 config override / MoA aggregator / model_overrides /
> custom_providers / endpoint-scoped; 1 persistent cache ...; 2-3 custom endpoints
> (/models, local probe, Ollama); ... 8 hardcoded defaults; 9 256K fallback."

The earlier (superseded) draft claimed the automatic path falls back to the *declared*
training window and proposed a manual priority-0 pin (`model.context_length` in
config.yaml) as the fix. **That diagnosis was wrong on this endpoint, and the operator
ruled against the manual design: the adapter must find the context automatically.**
Live verification on the serving llama.cpp endpoint (2026-10-08):

1. The endpoint's `/v1/models` reports the **runtime** window, not the GGUF training max:
   the model entry carries `meta: {n_ctx: 65536}`.
2. `/props` answers `default_generation_settings.n_ctx = 65536` with a `model_alias` that
   matches the served model.
3. Calling the installed resolver directly —
   `get_model_context_length(<served-model>, base_url=<endpoint>/v1)` — returns **65536**,
   matching the live slot exactly.

Why the automatic path works here, at file:line:

- Custom-endpoint step 2 (`_resolve_custom_endpoint_context_length`,
  model_metadata.py:~1215) probes `/models`; `_parse_models_payload` extracts the first
  int from `_CONTEXT_LENGTH_KEYS` (model_metadata.py:414) which **includes `n_ctx`** — so
  the runtime window is read straight off the endpoint.
- The local probe (`_query_local_context_length_uncached`, model_metadata.py:1566+)
  queries llama.cpp `/props` via `_llamacpp_context` (model_metadata.py:1586) — the
  runtime `n_ctx`, answered even for a lazily-loaded model — and
  `_openai_models_list_context` (model_metadata.py:1602) prefers `meta.n_ctx` over
  `n_ctx_train` explicitly ("Runtime n_ctx ... beats n_ctx_train, which can exceed what
  the server allocates").
- The endpoint qualifies as local: `is_local_endpoint` (model_metadata.py:~680) counts
  Tailscale CGNAT addresses as local, so the tailnet endpoint gets the llama.cpp probe
  treatment.
- Persistence: `_probe_local_context_length` (model_metadata.py:648) caches a positive
  probe into `~/.hermes/context_length_cache.yaml` (`save_context_length`,
  model_metadata.py:1143), keyed `model@base_url`.
- **Staleness is handled**: `_reconcile_local_cached_context_length` (model_metadata.py:
  659) re-probes a cached local entry and overwrites it when the live value differs —
  the docstring names exactly our scenario: "operators restart vLLM/Ollama with a new
  --max-model-len / num_ctx under the same id." A failed probe keeps the disk entry; a
  live value below `MINIMUM_CONTEXT_LENGTH` invalidates rather than persists.
- Per-conversation application: the gateway re-reads resolution at every inbound/turn
  (`gateway/run_inbound.py:1652`, `gateway/run_turn.py:647` → `743` passes it to
  `get_model_context_length_async`), so each conversation gets the endpoint's current
  window with no per-session setup.

**The residual gap (what A2 must actually do).** The automatic chain is real but has
silent failure modes that must not be inherited by the adapter:

- `_resolve_endpoint_context_length` returns the value **without persisting it** for the
  custom-endpoint path (contrast the novita path at model_metadata.py:2209, which calls
  `save_context_length` on a hit). If the `/props` probe path is ever unavailable, the
  fallback chain can land on the declared window or the 256K default.
- `_apply_llamacpp_props` (model_metadata.py:974) only rewrites the cache when the
  `/v1/models` payload carries `owned_by: "llamacpp"` (model_metadata.py:1072). The live
  endpoint does **not** report `owned_by` at all, so that reconcile path never fires here
  — the working path is the local-probe one, and its correctness depends on
  `is_local_endpoint` continuing to classify the endpoint as local.
- The whole chain ends in probe-down to `DEFAULT_FALLBACK_CONTEXT` (256K) with only an
  `logger.info` when detection fails — silent on the mesh (the adapter swallows it into a
  bare reply, A8 note).

So Part A is no longer "set a pin". It is: **prove the automatic discovery end-to-end on
the live endpoint (done, above), document the mechanism and its failure modes (this
spec), and add one adapter-side verification so a resolved window is never silently
absurd.** The invariant from the card survives unchanged: the adapter never asserts a
context window it has not verified against the serving endpoint. The verification, not a
manual number, is the adapter's job.

`model.context_length` remains available as an *escape hatch* only (priority 0,
model_metadata.py:2245; runtime setter `set_config_context_length`,
agent/agent_init.py:1773; disagreement warning `warn_once_on_pin_disagreement`,
agent/context_pin.py:56). It is not part of the A2 design and must stay unset on this
deployment so the automatic path is the single source of truth.

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

Core behaviour (Part A, automatic discovery):
- **US-1.** As an operator, I want the adapter/gateway to discover the context window
  automatically from the serving endpoint for every conversation, with no manual pin and
  no per-session setup, so a turn that would overflow the allocated slot is caught by
  Hermes before it runs instead of failing silently mid-stream.
- **US-2.** As an operator, I want discovery to track the live slot: if I restart llama.cpp
  with a different `n_ctx` under the same model id, the next conversation uses the new
  value without a config edit or restart of the gateway (the reconcile path,
  model_metadata.py:659).
- **US-3.** As an operator, I want the resolved window to be the value the endpoint
  actually allocates (`meta.n_ctx` from `/v1/models`, `/props`
  `default_generation_settings.n_ctx`), never the GGUF trained max or a 256K fallback.
- **US-4.** As an operator, I want the adapter to verify the window it is about to rely
  against the serving endpoint at turn time, and to log (not silently swallow) when the
  resolved value disagrees with what the endpoint reports. The adapter asserts no context
  window it has not verified.

Correctness and precision:
- **US-5.** As an operator, if I do set `model.context_length` as an escape hatch, it wins
  (priority 0) and `warn_once_on_pin_disagreement` warns once about the disagreement — but
  the default deployment leaves it unset.
- **US-6.** As an operator, I want the step hook to be a no-op when
  `HERMES_MESH_CONTROL_URL` is unset, so a fresh adapter install (which sets no control URL)
  performs zero dead HTTP calls on the tool-step hot path.
- **US-7.** As an operator, I want the step hook to *still work* (POST steps) when I do set
  `HERMES_MESH_CONTROL_URL` to a live control server, so the fix does not break the
  legitimate audit path.

Edge and degenerate states:
- **US-8.** As an operator, if the endpoint is unreachable at resolution time, the probe
  keeps the last cached value (probe-failure does not wipe the disk entry) rather than
  falling to a 256K default mid-conversation.
- **US-9.** As an operator, if I set `HERMES_MESH_CONTROL_URL` to an empty string or an
  obviously-invalid value, the hook treats it as "no control server" (no-op), not as a URL
  to POST to.
- **US-10.** As an operator, the `/step` and `/step/full` events (and any other `_post` call
  in the hook) must share one "is a control server configured?" decision, so I don't get a
  partial audit trail (some events sent, others not).
- **US-11.** As an operator, if llama.cpp is reloaded with a smaller `n_ctx` than a
  previously cached value, the smaller (live) value wins — the window is what the slot
  actually holds, never the larger remembered one.

## 4. Test seams

Part A (automatic discovery) — test surface:
- Live-endpoint contract (verified 2026-10-08, re-run as the acceptance smoke): the
  serving endpoint's `/v1/models` reports `meta.n_ctx` and `/props` reports
  `default_generation_settings.n_ctx`, and the installed resolver
  (`get_model_context_length(model, base_url)`) returns that exact value. Not unit-tested
  in this repo — a unit test cannot prove the live endpoint's behaviour; the seam is the
  live smoke plus the adapter-side verification hook.
- Adapter-side verification (the only new code in Part A): where the adapter surfaces or
  depends on a resolved window (e.g. `/status` output, turn-init metadata), it compares
  the resolved value against the endpoint's reported `n_ctx` and logs a warning on
  disagreement. Pure-logic comparison lives in a pure module (no rns/lxmf imports —
  `scripts/check_imports.py` guard) and is unit-testable with a stubbed endpoint reader.
- **No install.sh test and no config write for Part A.** The pin is not part of the
  design; nothing in install.sh or config.yaml may set `model.context_length`.

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
1. The fix is **verification, not a pin**: leave `model.context_length` unset; the
   automatic discovery chain (custom-endpoint `/models` probe with `n_ctx` in
   `_CONTEXT_LENGTH_KEYS`, llama.cpp `/props` local probe, `model@base_url` disk cache,
   live re-probe on cached entries) is the design. It is proven working on the live
   endpoint (see §2 grounding: the installed resolver returned the live `n_ctx` exactly).
2. Add a small adapter-side verification: when the adapter reports or relies on a context
   window, compare it against the endpoint-reported `n_ctx` (same
   `/v1/models` `meta.n_ctx` / `/props` shape) and log a single warning line on
   disagreement. This makes the card invariant executable: the adapter never asserts a
   window it has not verified. It must not block or rewrite the gateway's own resolution.
3. Document the failure modes (done in §2): the un-persisted custom-endpoint hit, the
   `owned_by: "llamacpp"` gate that never fires on this endpoint, and the probe-down to
   256K behind only a logger.info. These are the reasons the verification step exists.
4. **Out of scope (no gold-plating):** patching Hermes' resolver, changing
   `_resolve_endpoint_context_length` persistence upstream, or an install.sh convenience
   that writes `model.context_length`. If an override is ever needed, the priority-0
   escape hatch already exists and is documented in §2.

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

Part A integration decision (deferred, agreed):
5. The Part A.2 verification ships as the verified pure module
   (`src/hermes_reticulum/core/context_verify.py` + tests) with the wire-in
   point documented in `a2-context-verify-wire-in.md`. The wiring itself is a
   **separate decision**, not part of A2: its two inputs (`read_endpoint_n_ctx`
   and the gateway-resolved window) do not exist in the repo yet and must not
   be guessed. Until both exist, `verify_context_window` degrades safely to
   `endpoint-unavailable` with zero warnings.

Part A integration decision (deferred, agreed):
5. The adapter-side verification from Part A.2 ships as the verified pure module
   (`src/hermes_reticulum/core/context_verify.py` + tests) with the wire-in point
   documented in `a2-context-verify-wire-in.md`. The actual wiring is a **separate
   decision**, not part of A2: the two inputs it needs (`read_endpoint_n_ctx` and
   the gateway-resolved window) do not exist in the repo yet and must not be
   guessed. Until both exist, `verify_context_window` degrades safely to
   `endpoint-unavailable` with zero warnings.

Explicitly NOT being changed (out of scope, no gold-plating):
- The `mesh-tool-gate` pre-exec plugin (different component; do not touch).
- The adapter's send/receive/identity/command paths (A4/A6 territory).
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
- Part A: `tests/test_context_verification.py` for the adapter-side comparison (pure
  module: endpoint-reported `n_ctx` vs resolved window; agree / disagree / endpoint
  unreachable ⇒ no warning spam, no gateway interference). The live resolver itself is
  verified by the live smoke, not unit-tested.
- Part B: `tests/test_mesh_tool_events_control_url.py` (handler.py — drives the
  control-URL predicate and the two `_post` call sites with env set/unset/empty/invalid).

Test framework and runner: the project's existing runner (pytest, matching the sibling
`tests/` layout). No new framework.

What "passing" means:
- Part A: with `model.context_length` unset, the gateway resolves to the endpoint's live
  `n_ctx` (live smoke: resolver value == `/props` `n_ctx` == `/v1/models` `meta.n_ctx`);
  a turn sized near the window compresses instead of truncating; the adapter-side check
  logs zero disagreements on a healthy endpoint and exactly one warning when a stubbed
  endpoint disagrees with the resolved value.
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
- Part A red-before: drive the adapter-side verification with a stub endpoint reporting
  `n_ctx = 8192` against a resolved window of `262144` (the probe-down fallback value).
  On the current code there is no comparison at all, so no warning is emitted → the
  assertion "exactly one disagreement warning" fails red. After the check, it passes green.

Live smoke (the one thing a unit test cannot prove):
- Run one long multi-tool turn on the mesh with no pin set and confirm (a) the gateway
  resolves to the live `n_ctx` (compare against `/props`), (b) no mid-stream truncation on
  a turn sized near the window, and (c) the gateway log shows zero failed step-POST
  warnings during the turn. This is the operator's acceptance check; it is not automatable
  in-repo, so it is listed as the manual verification step in `tasks.md` A2.

---

## Acceptance (maps to tasks.md A2)

- [ ] `model.context_length` is **unset** in `~/.hermes/config.yaml`; discovery is fully
      automatic (verified: the installed resolver returns the endpoint's live `n_ctx`
      exactly — 65536 on the current llama.cpp slot).
- [ ] A reload of llama.cpp with a different `n_ctx` under the same model id is picked up
      by the next conversation via the reconcile path (`_reconcile_local_cached_context_length`)
      — no config edit, no gateway restart.
- [ ] Adapter-side context verification exists: resolved window vs endpoint-reported
      `n_ctx`, one warning line on disagreement, in a pure module (import-guard clean),
      covered by `tests/test_context_verification.py`.
- [ ] The plan doc records the automatic-discovery mechanism and its failure modes
      (un-persisted custom-endpoint hit, `owned_by` gate not firing on this endpoint,
      probe-down to 256K) so the next operator does not re-diagnose it.
- [ ] `HERMES_MESH_CONTROL_URL` unset ⇒ the hook performs zero step POSTs (both `_post`
      call sites no-op).
- [ ] `HERMES_MESH_CONTROL_URL` set to a valid URL ⇒ `/step` and `/step/full` still POST
      (regression guard for the legitimate audit path).
- [ ] Live smoke: one long mesh turn, no pin set, resolved window == live `n_ctx`, no
      mid-stream truncation, no failed step-POST warnings in the gateway log.
