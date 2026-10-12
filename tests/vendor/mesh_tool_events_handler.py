"""Hermes hook: stream tool steps to the Reticulum mesh bridge.

Runs inside the gateway on every ``agent:step`` event. For each tool
that ran in the previous iteration it:

  * resolves the mesh session name (via the bridge's state.db lookup),
  * in **step-through mode**: posts the FULL tool call (name + arguments)
    and FULL tool output to the bridge, chunked across multiple LXMF
    posts (the user opted into the bandwidth cost),
  * reports safe tools to the bridge (recap / /status),
  * for risky tools, asks the bridge's approval gate (blocking) and
    relays the decision.

The hook is deliberately *passive*: it never raises into the
conversation loop. Any failure is logged and swallowed so a
mesh/bridge outage can't break a normal Hermes turn.

Step-through mode
-----------------
Toggled on the bridge side with ``/steps on|off``.  The hook detects it
by reading a small state file written by the bridge (``~/.hermes/.reticulum-step-mode``)
because the hook runs in the gateway process and the mode flag lives in
the bridge process.  In step-through mode the hook sends the complete
tool arguments and complete tool result (truncated only to a hard cap
for absurdly large outputs) to the bridge via a new ``/step/full``
endpoint, which chunks it into ≤1500-char LXMF posts.

Configuration (env, read at call time):
  HERMES_MESH_CONTROL_URL  REQUIRED for step posts; unset/empty/invalid ->
                          no control server, no posts (see a2-spec.md)
  HERMES_MESH_TOKEN_FILE   default ~/.lxmf/storage/control_token
  HERMES_MESH_PROFILE      optional; if set, only bridge turns for this
                          profile are streamed (default: all)
  HERMES_STEP_MODE_FILE    default ~/.hermes/.reticulum-step-mode
  HERMES_STEP_HOLD_FILE    default ~/.hermes/.reticulum-hold-state
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import urllib.parse
import urllib.request

logger = logging.getLogger("mesh-tool-events")

def _control_server_configured() -> bool:
    """Single truth: is a control server explicitly configured?

    No default is forced (a2-spec.md): unset/empty/whitespace-only/non-http(s)
    HERMES_MESH_CONTROL_URL all mean "no control server" -> no step posts.
    """
    raw = os.environ.get("HERMES_MESH_CONTROL_URL")
    if raw is None or not raw.strip():
        return False
    try:
        parsed = urllib.parse.urlparse(raw.strip())
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


TOKEN_FILE = os.environ.get(
    "HERMES_MESH_TOKEN_FILE",
    os.path.expanduser("~/.lxmf/storage/control_token"),
)
PROFILE_FILTER = os.environ.get("HERMES_MESH_PROFILE", "").strip()

STATE_DB = os.environ.get(
    "HERMES_STATE_DB", os.path.expanduser("~/.hermes/state.db")
)

# Liveness-heartbeat marker file (spec Part 2026-08-27, Part 1 / Option A).
# Written by the hook on every ``agent:step`` (tool progress) and, when
# subscribed, on ``agent:start`` (turn start → the "phase: model" window).
# The bridge's liveness-guard watcher polls it each second; a fresh marker
# for the same session touches the guard's clock. mtime is authoritative.
TURN_ALIVE_FILE = os.environ.get(
    "HERMES_TURN_ALIVE_FILE",
    os.path.expanduser("~/.hermes/.reticulum-turn-alive"),
)

# Diagnostic log for the mesh-tool-events hook. The hook's own logger
# ("mesh-tool-events") is swallowed by the hermes gateway's logging config,
# so for debugging we also append to a plain file. Every significant event
# (event received, mesh resolution, mode check, POST result) is logged here
# so the operator can verify whether the hook is firing for the right
# session and whether the control server is actually receiving the POSTs.
DIAG_LOG = os.environ.get(
    "HERMES_MESH_HOOK_LOG",
    os.path.expanduser("~/.hermes/logs/mesh-tool-events.log"),
)


def _diag(msg: str) -> None:
    """Append a line to the hook's diagnostic log (best-effort)."""
    try:
        os.makedirs(os.path.dirname(DIAG_LOG), exist_ok=True)
        with open(DIAG_LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}\n")
    except Exception:  # noqa: BLE001 — never raise from logging
        pass

# Step-through mode state file (written by the bridge, read by the hook).
STEP_MODE_FILE = os.environ.get(
    "HERMES_STEP_MODE_FILE",
    os.path.expanduser("~/.hermes/.reticulum-step-mode"),
)
HOLD_STATE_FILE = os.environ.get(
    "HERMES_STEP_HOLD_FILE",
    os.path.expanduser("~/.hermes/.reticulum-hold-state"),
)

# Hard cap on tool output size sent to the bridge in step-through mode.
# 32 KB ≈ 21 chunked LXMF posts of 1500 chars.  Larger than this the
# hook truncates with a "… [truncated]" marker.
STEP_FULL_MAX_CHARS = int(os.environ.get("HERMES_STEP_FULL_MAX", "32768"))

# ── Bounded HTTP timeouts (never park the gateway event loop) ──────────
# The hook runs *inside* the Hermes gateway's asyncio event loop. Any
# blocking call here stalls Telegram/Discord heartbeats (the 2026-08-27
# "gateway hiccup" was exactly this: a 600s urlopen wedging the loop).
# So every mesh POST uses a short timeout, and the whole per-event work is
# dispatched onto a worker thread so the event-loop thread never blocks on
# the network. The approval *wait* is bounded by the control server's
# approval_timeout (default 120s, deny-by-default), so a single gate can
# park the worker for at most that long — and only the worker, not the loop.
REPORT_POST_TIMEOUT = 5.0      # safe-tool recap: fire-and-forget, short
FULL_STEP_POST_TIMEOUT = 10.0  # step-through full step: bounded, off-loop
GATE_POST_TIMEOUT = 600.0      # approval gate: may block for operator AFK,
                               #   but bounded; only the worker thread waits.

# Mirror of the bridge's risk classes (kept in sync with
# core/control_server.py). Unknown tools gate (deny-by-default).
SAFE_TOOLS = {
    "read_file", "search_files", "web_search", "web_extract",
    "fact_store", "fact_feedback", "session_search", "skills_list",
    "skill_view", "clarify", "todo", "page_info", "vision_analyze",
}
RISKY_PREFIXES = ("browser_", "execute_")
RISKY_TOOLS = {
    "terminal", "write_file", "patch", "memory", "cronjob",
    "delegate_task", "text_to_speech",
}


def _classify(name: str) -> str:
    if name in SAFE_TOOLS:
        return "safe"
    if name in RISKY_TOOLS or any(name.startswith(p) for p in RISKY_PREFIXES):
        return "risky"
    return "safe"  # unknown: report (recap), but don't gate on every new tool


def _token() -> str | None:
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def _write_turn_alive_marker(mesh: str, phase: str) -> None:
    """Refresh the bridge's liveness-heartbeat marker for this mesh thread.

    Spec Part 1, Option A + Option B (per-child generation scoping).

    The hook runs in the gateway process, which streams tool events for
    every session that resolves to a mesh thread — including the
    operator's own gateway session (the bridge's step hook fires for it
    too, because the mesh bridge's child IS a hermes session). If the
    hook wrote a bare marker, the operator's long-running gateway session
    would keep the bridge child's liveness clock warm (or clobber the
    bridge child's marker with the gateway's session name), defeating the
    stall detector.

    The generation counter (``gen`` in the marker, owned by the bridge)
    scopes the marker to one live bridge child. On each refresh the hook
    reads the current gen from the marker file (the bridge writes a fresh
    ``gen`` at spawn) and rewrites the marker with the *same* gen, so:

    * a mesh child's heartbeat keeps ticking for that child's lifetime,
    * the operator's gateway session can no longer keep a dead/next child's
      clock warm, because it never writes a new gen — it only refreshes
      the gen the bridge last wrote.
    """
    try:
        import time

        # Read the current generation (set by the bridge at the last spawn).
        # If the marker doesn't exist yet (bridge not spawned, or cleared
        # between spawn and first step), create one with gen=0 so the
        # bridge's initial phase="model" marker is the only one that
        # carries a non-zero gen — the bridge's watcher ignores gen=0.
        cur_gen = 0
        try:
            with open(TURN_ALIVE_FILE, encoding="utf-8") as f:
                cur_gen = int(json.load(f).get("gen", 0) or 0)
        except (OSError, ValueError, TypeError):
            cur_gen = 0

        tmp = TURN_ALIVE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "session": mesh,
                    "ts": time.time(),
                    "phase": phase,
                    "gen": cur_gen,
                },
                f,
            )
        os.replace(tmp, TURN_ALIVE_FILE)
    except Exception:  # noqa: BLE001 — hook must never raise
        logger.debug("could not write turn-alive marker", exc_info=True)


def _post(path: str, payload: dict, timeout: float) -> str | None:
    if not _control_server_configured():
        logger.debug("no control server configured — skipping %s", path)
        return None
    tok = _token()
    if not tok:
        logger.debug("no control token at %s — skipping", TOKEN_FILE)
        _diag(f"  POST {path} skipped: no token at {TOKEN_FILE}")
        return None
    url = f"{os.environ['HERMES_MESH_CONTROL_URL'].strip()}{path}?token={tok}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "X-Hermes-Token": tok},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace").strip()
            _diag(f"  POST {path} → {resp.status} {body[:80]}")
            return body
    except Exception as e:  # noqa: BLE001 — hook must never raise
        logger.warning("mesh bridge POST %s failed: %s", path, e)
        _diag(f"  POST {path} FAILED: {e}")
        return None


def _resolve_mesh_session(session_id: str) -> str | None:
    """Map a gateway session ID to its mesh thread name (sessions.title).

    The bridge spawns ``hermes chat`` with ``-c <title>``; the child's
    session is stored in state.db with ``title`` = the mesh thread name
    and ``id`` = the concrete session ID. Returns the title, or None if
    this session isn't a mesh session.
    """
    if not session_id:
        return None
    try:
        conn = sqlite3.connect(STATE_DB)
    except sqlite3.Error as e:
        logger.warning("state.db open failed: %s", e)
        return None
    try:
        row = conn.execute(
            "SELECT title FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
    except sqlite3.Error as e:
        logger.warning("state.db query failed: %s", e)
        return None
    finally:
        conn.close()
    return row[0] if row else None


def _step_mode_on() -> bool:
    """Read the step-through mode flag (written by the bridge)."""
    try:
        with open(STEP_MODE_FILE, encoding="utf-8") as f:
            return f.read().strip() == "1"
    except OSError:
        return False


def _hold_state() -> bool:
    """Read the hold gate state (written by the bridge)."""
    try:
        with open(HOLD_STATE_FILE, encoding="utf-8") as f:
            return f.read().strip() == "1"
    except OSError:
        return False


def _fmt_args(args) -> str:
    """Format tool arguments as a compact JSON string for display."""
    if args is None:
        return "(no arguments)"
    if isinstance(args, str):
        return args
    try:
        return json.dumps(args, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(args)


# Primary argument fields, in priority order, for the tools we know about.
# The mesh operator wants to see the actual command/path/URL, not a JSON
# blob — so the primary field is extracted and shown first.
_ARG_PRIMARY = {
    "terminal": ["command"],
    "read_file": ["path"],
    "write_file": ["path"],
    "search_files": ["pattern"],
    "patch": ["path"],
    "web_search": ["query"],
    "web_extract": ["urls"],
    "browser_exec": ["code"],
    "execute_code": ["code"],
    "process": ["action"],
    "clarify": ["questions"],
    "memory": ["action", "content"],
    "fact_store": ["action", "query", "content"],
    "cronjob": ["action", "job_id"],
    "skill_view": ["name"],
    "skill_manage": ["action", "name"],
    "delegate_task": ["goal"],
    "session_search": ["query", "session_id"],
    "vision_analyze": ["image_url"],
    "text_to_speech": ["text"],
    "browser_navigate": ["url"],
    "new_tab": ["url"],
    "goto_url": ["url"],
}


def _fmt_args_compact(args, name: str) -> tuple[str, str]:
    """Return (primary, extra) for display.

    primary: the human-readable main argument (command, path, query...) or
        "" if there is none.
    extra: a compact JSON of any remaining arguments, or "" if there are
        none. This keeps the mesh bubble looking like the gateway's own
        tool-progress message (primary arg front-and-center) instead of a
        raw JSON dump.
    """
    if args is None:
        return "", ""
    if isinstance(args, str):
        return args, ""
    if not isinstance(args, dict):
        try:
            return json.dumps(args, ensure_ascii=False, default=str), ""
        except (TypeError, ValueError):
            return str(args), ""

    # Pull out the primary field(s) for this tool, in priority order.
    primary_parts: list[str] = []
    rest: dict = dict(args)
    for key in _ARG_PRIMARY.get(name, []):
        if key in rest:
            val = rest.pop(key)
            if val is None:
                continue
            if isinstance(val, (dict, list)):
                val = json.dumps(val, ensure_ascii=False, default=str)
            primary_parts.append(str(val))
    primary = " ".join(primary_parts).strip()

    # Remaining args → compact JSON (omit empty).
    extra = ""
    if rest:
        try:
            extra = json.dumps(rest, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            extra = str(rest)
    return primary, extra


def _fmt_result(result) -> str:
    """Format tool result for display, truncating to the hard cap."""
    if result is None:
        return "(no output)"
    if not isinstance(result, str):
        try:
            result = json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            result = str(result)
    if len(result) > STEP_FULL_MAX_CHARS:
        result = result[:STEP_FULL_MAX_CHARS] + "\n… [truncated]"
    return result


def _post_full_step(
    mesh: str, name: str, args, result, is_error: bool,
) -> None:
    """Step-through mode: POST the full tool call + output to the bridge.

    The bridge chunks this into ≤1500-char LXMF posts (multiple sends,
    user-approved bandwidth cost).

    The header format matches the gateway's own tool-progress rendering
    (Telegram/Discord): an emoji + tool name, the arguments on the next
    line, then the output. The old ``🔧 STEP <name> / ── args ── / ──
    output ──`` prose format looked like a log dump, not like the harness
    progress message the operator expects.

    For known tools we extract the *primary* argument (command, path,
    query, ...) and show it front-and-center; any remaining args go in a
    compact ``… also: <json>`` trailer so nothing is lost without the
    whole JSON blob dominating the message.
    """
    primary, extra = _fmt_args_compact(args, name)
    result_str = _fmt_result(result)
    tag = " ❌" if is_error else ""
    emoji = "💻" if name == "terminal" else "🔧"
    lines = [f"{emoji} {name}{tag}"]
    if primary:
        lines.append(primary)
    if extra:
        lines.append(f"… also: {extra}")
    if result_str:
        lines.append(result_str)
    body = "\n".join(lines)
    _post(
        "/step/full",
        {
            "session": mesh,
            "tool": name,
            "body": body,
            "is_error": is_error,
        },
        timeout=FULL_STEP_POST_TIMEOUT,
    )


def handle(event_type: str, context: dict):
    """Entry point for the subscribed gateway events. Never raises.

    Runs the per-event work on a short-lived daemon thread so the gateway's
    asyncio event-loop thread (this hook's caller) never blocks on the
    control-server HTTP or marker writes. The gate's blocking wait (up to
    GATE_POST_TIMEOUT) parks the worker thread only, never the loop.
    """
    ctx = context or {}
    if event_type == "agent:start":
        worker = _handle_start
    else:
        worker = _handle_step
    try:
        t = threading.Thread(
            target=worker,
            args=(ctx,),
            name="mesh-tool-events",
            daemon=True,
        )
        t.start()
    except Exception as e:  # noqa: BLE001 — hook must never raise
        logger.warning("mesh-tool-events handler error: %s", e)


def _handle_start(ctx: dict) -> None:
    """``agent:start``: turn started → refresh the liveness marker.

    Covers the ``phase: model`` window: the time between the child spawning
    and the first tool batch (when the model is thinking with no tools yet).
    For mesh sessions only; a non-mesh session never touches the marker.
    """
    sid = ctx.get("session_id", "")
    mesh = _resolve_mesh_session(sid)
    if not mesh:
        return
    if PROFILE_FILTER and not mesh.startswith(PROFILE_FILTER):
        return
    _write_turn_alive_marker(mesh, "model")


def _handle_step(ctx: dict) -> None:
    sid = ctx.get("session_id", "")
    tools = ctx.get("tools") or []
    mesh = _resolve_mesh_session(sid)
    _diag(f"agent:step sid={sid[:8]} mesh={mesh!r} tools={[t.get('name') if isinstance(t, dict) else t for t in tools]}")
    if not mesh:
        # Not a mesh (bridge) session — nothing to stream.
        return
    if PROFILE_FILTER and not mesh.startswith(PROFILE_FILTER):
        _diag(f"  → skipped (profile filter)")
        return

    # Liveness heartbeat (spec Part 1, Option A): this hook fires after
    # every tool batch, so the model is provably alive — refresh the marker
    # BEFORE any blocking control-server POST, so a slow/stuck relay can't
    # starve the heartbeat. (The marker file is what turns the bridge's
    # liveness guard from a wall clock into a stall detector.)
    _write_turn_alive_marker(mesh, "tool")

    if not tools:
        return

    step_mode = _step_mode_on()
    _diag(f"  step_mode={step_mode}")

    for tool in tools:
        if isinstance(tool, dict):
            name = tool.get("name") or ""
            args = tool.get("arguments")
            result = tool.get("result")
        else:
            name, args, result = str(tool), None, None
        if not name:
            continue
        kind = _classify(name)
        is_error = False
        # Heuristic error detection: result string starting with error markers.
        if isinstance(result, str) and result.strip().startswith(
            ("❌", "Error", "error:")
        ):
            is_error = True

        # ── Step-through mode: full tool call + output, chunked ──────
        if step_mode:
            _post_full_step(mesh, name, args, result, is_error)
            _diag(f"  → step/full POST for {name} (is_error={is_error})")
            # GATE MOVED OUT OF THIS HOOK (2026-08-28): the pre-execution
            # mesh-tool-gate *plugin* (pre_tool_call hook) now owns approve/
            # deny — it fires before dispatch, reuses Hermes' own
            # detect_dangerous/hardline detection, is fail-closed, and is
            # bounded. This hook stays pure stream: it only pushes the full
            # step + liveness marker here. (Previously it ran a second,
            # post-hoc, 600s blocking /step gate — the old ack-then-veto —
            # which double-gated the operator and parked a worker 600s.)
            continue

        # ── Normal mode: recap (no gate) ───────────────────────────────
        # The pre-execution mesh-tool-gate plugin owns approve/deny now.
        # This hook only reports every tool (risky or safe) for the recap /
        # /status stream — it no longer runs a blocking approval gate here,
        # which previously double-gated the operator (the plugin gates
        # pre-dispatch; the old /step kind=gate here was ack-then-veto).
        _post(
            "/step",
            {
                "session": mesh,
                "kind": "report",
                "tool": name,
                "args": args,
                "result": result,
                "is_error": is_error,
            },
            timeout=10.0,
        )
