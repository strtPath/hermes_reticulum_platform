"""RED-BEFORE tests for the mesh-tool-events hook control-URL gate (card A2, US-1..US-11).

The hook must POST tool steps only when HERMES_MESH_CONTROL_URL is explicitly
set to a valid http(s) URL — no forced loopback default, one shared predicate
for both /step and /step/full. Loaded via importlib like the installed hook is;
exercised through the hook's public entry point ``handle``.

Handler source resolution (in order):
1. HERMES_MESH_HOOK_HANDLER env override (CI can point at a pinned copy).
2. ~/.hermes/hooks/mesh-tool-events/handler.py (the installed live hook).
3. tests/vendor/mesh_tool_events_handler.py (pinned copy shipped with the
   repo, kept byte-identical to the installed hook).

If none is present the tests skip — but the vendor file ships in the repo,
so a clean CI runner runs them instead of skipping silently.
"""

import hashlib
import importlib.util
import json
import os
import threading
import urllib.request

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALLED_HOOK_PATH = os.path.expanduser("~/.hermes/hooks/mesh-tool-events/handler.py")
VENDOR_PATH = os.path.join(REPO_ROOT, "tests", "vendor", "mesh_tool_events_handler.py")


def _handler_path() -> str | None:
    override = os.environ.get("HERMES_MESH_HOOK_HANDLER")
    if override:
        return override if os.path.exists(override) else None
    if os.path.exists(INSTALLED_HOOK_PATH):
        return INSTALLED_HOOK_PATH
    if os.path.exists(VENDOR_PATH):
        return VENDOR_PATH
    return None


class _FakeResp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return b'{"ok": true}'


def _load_hook():
    path = _handler_path()
    if path is None:
        pytest.skip("mesh-tool-events handler not present (no installed hook, no vendor copy)")
    spec = importlib.util.spec_from_file_location("mesh_tool_events_hook", path)
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    return hook

@pytest.fixture()
def hook(monkeypatch, tmp_path):
    mod = _load_hook()
    monkeypatch.delenv("HERMES_MESH_CONTROL_URL", raising=False)
    monkeypatch.setattr(mod, "TOKEN_FILE", str(tmp_path / "token"))
    (tmp_path / "token").write_text("tok")
    monkeypatch.setattr(mod, "TURN_ALIVE_FILE", str(tmp_path / "alive"))
    monkeypatch.setattr(mod, "STEP_MODE_FILE", str(tmp_path / "step-mode"))
    monkeypatch.setattr(mod, "HOLD_STATE_FILE", str(tmp_path / "hold"))
    monkeypatch.setattr(mod, "_diag", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_resolve_mesh_session", lambda sid: "mesh-unit-test")

    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append({
            "url": req.full_url,
            "method": req.get_method(),
            "body": json.loads(req.data.decode("utf-8")) if req.data else None,
        })
        return _FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    def fire(step_mode=False):
        (tmp_path / "step-mode").write_text("1\n" if step_mode else "0\n")
        mod.handle("agent:step", {"session_id": "s1", "tools": [
            {"name": "terminal", "arguments": {"command": "echo hi"}, "result": "hi"},
        ]})
        for t in threading.enumerate():
            if t.name == "mesh-tool-events":
                t.join(timeout=5)

    return mod, calls, fire


def _steps(calls):
    return [c for c in calls if "/step" in c["url"]]


# --- handler source integrity: vendor copy must mirror the installed hook ----

def test_vendor_handler_matches_installed_hook():
    """The pinned vendor copy must stay byte-identical to the installed hook
    on machines where both exist, so CI's pinned copy tests the same code."""
    if not os.path.exists(INSTALLED_HOOK_PATH):
        pytest.skip("installed hook not present on this machine")
    with open(INSTALLED_HOOK_PATH, "rb") as f:
        installed = f.read()
    with open(VENDOR_PATH, "rb") as f:
        vendor = f.read()
    if hashlib.sha256(installed).hexdigest() != hashlib.sha256(vendor).hexdigest():
        pytest.fail(
            "tests/vendor/mesh_tool_events_handler.py is stale vs the installed "
            "hook — re-vendor it: cp ~/.hermes/hooks/mesh-tool-events/handler.py "
            "tests/vendor/mesh_tool_events_handler.py"
        )


# --- US-1/US-2: unset -> no control server, zero posts -----------------------

def test_predicate_exists(hook):
    mod, _, _ = hook
    assert callable(mod._control_server_configured)


def test_unset_env_predicate_false_and_zero_posts(hook):
    mod, calls, fire = hook
    assert mod._control_server_configured() is False
    fire()
    assert _steps(calls) == []


def test_unset_env_step_full_zero_posts(hook):
    mod, calls, fire = hook
    fire(step_mode=True)
    assert _steps(calls) == []


def test_unset_env_no_http_calls_at_all(hook):
    _, calls, fire = hook
    fire()
    assert calls == []


# --- off-state invariant: the gate kills only the dead HTTP POST, never the
#     liveness marker (spec Part B: heartbeat must keep refreshing) -----------

def _marker(mod):
    with open(mod.TURN_ALIVE_FILE, encoding="utf-8") as f:
        return json.load(f)


def test_marker_still_written_with_control_url_off(hook):
    mod, calls, fire = hook
    fire()
    assert _steps(calls) == []
    marker = _marker(mod)
    assert marker["session"] == "mesh-unit-test"
    assert marker["phase"] == "tool"


def test_marker_still_written_step_full_with_control_url_off(hook):
    mod, calls, fire = hook
    fire(step_mode=True)
    assert _steps(calls) == []
    marker = _marker(mod)
    assert marker["session"] == "mesh-unit-test"
    assert marker["phase"] == "tool"


def test_marker_refreshed_on_second_step(hook):
    mod, _, fire = hook
    fire()
    ts1 = _marker(mod)["ts"]
    fire()
    ts2 = _marker(mod)["ts"]
    assert ts2 >= ts1


# --- US-3/US-4: valid URL -> predicate true, posts happen --------------------

def test_valid_env_predicate_true_and_posts(hook, monkeypatch):
    mod, calls, fire = hook
    monkeypatch.setenv("HERMES_MESH_CONTROL_URL", "http://control.invalid:1")
    assert mod._control_server_configured() is True
    fire()
    steps = _steps(calls)
    assert len(steps) == 1
    assert steps[0]["url"].startswith("http://control.invalid:1/step?")


# --- step bodies: payload fields for both endpoints (spec §5 Part B.3:
#     the on-path POSTs the current payload unchanged) ------------------------

def test_normal_step_post_body_fields(hook, monkeypatch):
    mod, calls, fire = hook
    monkeypatch.setenv("HERMES_MESH_CONTROL_URL", "http://control.invalid:1")
    fire()
    steps = _steps(calls)
    assert len(steps) == 1
    call = steps[0]
    assert call["method"] == "POST"
    assert call["url"].startswith("http://control.invalid:1/step?token=tok")
    body = call["body"]
    assert body["session"] == "mesh-unit-test"
    assert body["kind"] == "report"
    assert body["tool"] == "terminal"
    assert body["args"] == {"command": "echo hi"}
    assert body["result"] == "hi"
    assert body["is_error"] is False


def test_step_full_post_body_fields(hook, monkeypatch):
    mod, calls, fire = hook
    monkeypatch.setenv("HERMES_MESH_CONTROL_URL", "http://control.invalid:1")
    fire(step_mode=True)
    steps = [c for c in calls if "/step/full" in c["url"]]
    assert len(steps) == 1
    call = steps[0]
    assert call["method"] == "POST"
    assert call["url"].startswith("http://control.invalid:1/step/full?token=tok")
    body = call["body"]
    assert body["session"] == "mesh-unit-test"
    assert body["tool"] == "terminal"
    assert body["is_error"] is False
    # Gateway-style rendering: emoji + tool name line, primary arg, output.
    assert body["body"].startswith("💻 terminal")
    assert "echo hi" in body["body"]


def test_step_full_error_flag_in_body(hook, monkeypatch):
    mod, calls, _ = hook
    monkeypatch.setenv("HERMES_MESH_CONTROL_URL", "http://control.invalid:1")
    with open(mod.STEP_MODE_FILE, "w", encoding="utf-8") as f:
        f.write("1\n")
    mod.handle("agent:step", {"session_id": "s1", "tools": [
        {"name": "terminal", "arguments": {"command": "false"}, "result": "❌ failed"},
    ]})
    for t in threading.enumerate():
        if t.name == "mesh-tool-events":
            t.join(timeout=5)
    steps = [c for c in calls if "/step/full" in c["url"]]
    assert len(steps) == 1
    assert steps[0]["body"]["is_error"] is True
    assert "❌" in steps[0]["body"]["body"]


# --- US-5: both endpoints share the one predicate ----------------------------

def test_step_full_uses_same_predicate_and_base(hook, monkeypatch):
    mod, calls, fire = hook
    monkeypatch.setenv("HERMES_MESH_CONTROL_URL", "http://control.invalid:1")
    fire(step_mode=True)
    assert len([c for c in calls if c["url"].startswith("http://control.invalid:1/step/full")]) == 1


# --- US-9: empty / whitespace / invalid scheme -> no control server ----------

@pytest.mark.parametrize("value", ["", "   ", "ftp://host:1", "not a url", "://broken"])
def test_invalid_values_predicate_false(hook, monkeypatch, value):
    mod, calls, fire = hook
    monkeypatch.setenv("HERMES_MESH_CONTROL_URL", value)
    assert mod._control_server_configured() is False
    fire()
    assert _steps(calls) == []


# --- gate lives in _post itself: both call sites gated (US-10) ---------------

def test_post_gated_at_single_choke_point(hook, monkeypatch):
    mod, calls, _ = hook
    assert mod._post("/step", {}, 1.0) is None
    assert mod._post("/step/full", {}, 1.0) is None
    assert calls == []
    monkeypatch.setenv("HERMES_MESH_CONTROL_URL", "http://control.invalid:1")
    assert mod._post("/step", {}, 1.0) is not None
    assert len(calls) == 1


# --- no forced loopback default anywhere in the hook -------------------------

def test_no_forced_loopback_default_in_source():
    path = _handler_path()
    if path is None:
        pytest.skip("mesh-tool-events handler not present")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    assert "127.0.0.1" not in src


# --- US-11: hook stays stdlib-only (import guard) ----------------------------

def test_hook_imports_are_stdlib_only():
    path = _handler_path()
    if path is None:
        pytest.skip("mesh-tool-events handler not present")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    banned = ["import rns", "import lxmf", "from rns", "from lxmf"]
    assert not any(b in src for b in banned)


def test_token_file_default_untouched(monkeypatch):
    monkeypatch.delenv("HERMES_MESH_TOKEN_FILE", raising=False)
    mod = _load_hook()
    assert mod.TOKEN_FILE.endswith(os.path.join(".lxmf", "storage", "control_token"))
