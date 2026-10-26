"""Reticulum/LXMF gateway platform plugin — directory-plugin shim.

Hermes discovers directory plugins from ``~/.hermes/plugins/<name>/`` and calls
``register(ctx)`` in this module. The real implementation lives in the
``hermes_reticulum`` package, which install.sh installs into the *bridge's* own
virtualenv — not into Hermes' environment. Those are usually different Python
interpreters (e.g. Hermes via pipx/mise on 3.13, the bridge venv on 3.14).

Rather than mutating the user's Hermes installation (``pipx inject`` and
friends are install-method specific), this shim makes ``hermes_reticulum``
importable by adding the right directory to ``sys.path``. Hermes is taken as
found.

Where to look is written by install.sh into ``venv_path.txt`` (one path per
line). install.sh writes BOTH:

  * the bridge venv root (covers a normal ``pip install`` — the package lands
    in ``<venv>/lib/pythonX.Y/site-packages/hermes_reticulum``), and
  * the repo's ``src/`` directory (covers an editable ``pip install -e`` —
    the package lives at ``<repo>/src/hermes_reticulum``).

Both are needed: an editable install registers a ``.pth``/finder that only the
``site`` module executes at interpreter start, so simply appending the venv's
site-packages to ``sys.path`` does NOT make an editable package importable.
Adding the source directory does.

``$HERMES_RETICULUM_VENV`` overrides the search. If nothing importable is
found, ``register`` degrades to a no-op so a missing/renamed bridge venv can
never break the user's Hermes startup — the platform just isn't registered.
"""

from __future__ import annotations

import glob
import logging
import os
import sys

logger = logging.getLogger("reticulum-plugin")

_HERE = os.path.dirname(os.path.abspath(__file__))
_MARKER = os.path.join(_HERE, "venv_path.txt")


def _roots() -> list[str]:
    """Candidate roots, in priority order (env override first)."""
    roots: list[str] = []

    override = os.environ.get("HERMES_RETICULUM_VENV", "").strip()
    if override:
        roots.append(override)

    try:
        with open(_MARKER, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    roots.append(line)
    except OSError:
        pass

    return roots


def _importable_dirs(root: str):
    """Yield directories under *root* that could hold ``hermes_reticulum``."""
    yield root
    yield os.path.join(root, "Lib", "site-packages")  # Windows layout
    for pattern in (
        os.path.join(root, "lib", "python*", "site-packages"),  # POSIX layout
    ):
        yield from glob.glob(pattern)


def _extend_sys_path() -> None:
    """Prepend the first directory that actually contains hermes_reticulum,
    AND the venv's site-packages (which holds RNS/LXMF and other deps).

    An editable install puts hermes_reticulum in ``src/`` but its runtime
    dependencies (RNS, LXMF, ruamel, ...) in ``<venv>/lib/python*/site-packages``.
    Only extending the hermes_reticulum dir leaves those deps invisible to a
    gateway Python that does not otherwise have them, so the platform import
    fails (``No module named 'LXMF'``) and registration silently degrades to a
    no-op. Add both: the dir with the package AND its sibling site-packages.
    """
    # Iterate roots in REVERSE so the highest-priority root (env override
    # first in `_roots()`) is inserted LAST — i.e. at the FRONT of sys.path.
    # Inserting each root at position 0 in declaration order would let a
    # lower-priority root (e.g. a stale venv_path.txt entry) shadow the
    # operator's HERMES_RETICULUM_VENV override and load the wrong deps.
    for root in reversed(_roots()):
        candidates = list(_importable_dirs(root))
        for candidate in candidates:
            if not candidate or candidate in sys.path:
                continue
            if os.path.isdir(os.path.join(candidate, "hermes_reticulum")):
                sys.path.insert(0, candidate)
                logger.debug("reticulum plugin: added %s to sys.path", candidate)
        # Also add every site-packages dir under this root so runtime deps
        # (RNS/LXMF/...) resolve even on an editable install.
        for candidate in candidates:
            if not candidate or candidate in sys.path:
                continue
            if candidate.endswith("site-packages") and os.path.isdir(candidate):
                sys.path.insert(0, candidate)
                logger.debug("reticulum plugin: added deps path %s to sys.path", candidate)


def _noop_register(ctx) -> None:  # pragma: no cover - fallback path
    logger.warning(
        "reticulum plugin: hermes_reticulum is not importable, so the "
        "Reticulum platform was NOT registered. Set HERMES_RETICULUM_VENV to "
        "the bridge venv path, or re-run install.sh."
    )


_extend_sys_path()

try:
    from hermes_reticulum.plugin.registration import register  # noqa: F401
except Exception as exc:  # noqa: BLE001 — never break Hermes startup
    logger.warning("reticulum plugin unavailable: %s", exc)
    register = _noop_register

__all__ = ["register"]
