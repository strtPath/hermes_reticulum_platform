"""RED-BEFORE tests for the mesh-tool-events hook control-URL gate (card A2, US-1..US-11).

The hook must POST tool steps only when HERMES_MESH_CONTROL_URL is explicitly
set to a valid http(s) URL — no forced loopback default, one shared predicate
for both /step and /step/full. Loaded via importlib like the installed hook is;
exercised through the hook's public entry point ``handle``.
"""

import importlib.util
import os
import threading
import urllib.request

import pytest

HOOK_PATH = os.path.expanduser("~/.hermes/hooks/mesh-tool-events/handler.py")


class _FakeResp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return b'{"ok": true}'


def _load_hook():
    if not os.path.exists(HOOK_PATH):
        pytest.skip("installed hook not present on this machine")
    spec = importlib.util.spec_from_file_location("mesh_tool_events_hook", HOOK_PATH)
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
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda req, timeout=None: (calls.append(req.full_url), _FakeResp())[1],
    )

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
    return [c for c in calls if "/step" in c]


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


# --- US-3/US-4: valid URL -> predicate true, posts happen --------------------

def test_valid_env_predicate_true_and_posts(hook, monkeypatch):
    mod, calls, fire = hook
    monkeypatch.setenv("HERMES_MESH_CONTROL_URL", "http://control.invalid:1")
    assert mod._control_server_configured() is True
    fire()
    steps = _steps(calls)
    assert len(steps) == 1
    assert steps[0].startswith("http://control.invalid:1/step?")


# --- US-5: both endpoints share the one predicate ----------------------------

def test_step_full_uses_same_predicate_and_base(hook, monkeypatch):
    mod, calls, fire = hook
    monkeypatch.setenv("HERMES_MESH_CONTROL_URL", "http://control.invalid:1")
    fire(step_mode=True)
    assert len([c for c in calls if c.startswith("http://control.invalid:1/step/full")]) == 1


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
    if not os.path.exists(HOOK_PATH):
        pytest.skip("installed hook not present on this machine")
    with open(HOOK_PATH, encoding="utf-8") as f:
        src = f.read()
    assert "127.0.0.1" not in src


# --- US-11: hook stays stdlib-only (import guard) ----------------------------

def test_hook_imports_are_stdlib_only():
    if not os.path.exists(HOOK_PATH):
        pytest.skip("installed hook not present on this machine")
    with open(HOOK_PATH, encoding="utf-8") as f:
        src = f.read()
    banned = ["import rns", "import lxmf", "from rns", "from lxmf"]
    assert not any(b in src for b in banned)


def test_token_file_default_untouched(monkeypatch):
    monkeypatch.delenv("HERMES_MESH_TOKEN_FILE", raising=False)
    mod = _load_hook()
    assert mod.TOKEN_FILE.endswith(os.path.join(".lxmf", "storage", "control_token"))

