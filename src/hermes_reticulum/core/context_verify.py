"""Context verification (card A2): compare the resolved Hermes context window
against the endpoint-reported n_ctx and warn exactly once per mismatch.

Pure module: no rns/lxmf imports, no I/O — the endpoint-reported value arrives
through an injected reader callable so this stays import-guard clean.
"""

import logging
from collections.abc import Callable

log = logging.getLogger(__name__)

MATCH = "match"
MISMATCH = "mismatch"
UNAVAILABLE = "endpoint-unavailable"

_WARNED: set[tuple[int, int]] = set()


def _read_n_ctx_from_endpoint() -> int | None:
    return None


def verify_context_window(
    resolved_window: int | None,
    read_endpoint_n_ctx: Callable[[], int | None] | None = None,
    *,
    warn: Callable[[str], None] | None = None,
) -> str:
    reader = read_endpoint_n_ctx or _read_n_ctx_from_endpoint
    emit = warn or log.warning
    try:
        reported = reader()
    except Exception:
        return UNAVAILABLE
    if reported is None or resolved_window is None:
        return UNAVAILABLE
    if int(reported) == int(resolved_window):
        return MATCH
    key = (int(resolved_window), int(reported))
    if key not in _WARNED:
        _WARNED.add(key)
        emit(
            f"context window mismatch: resolved {resolved_window} "
            f"vs endpoint n_ctx {reported}"
        )
    return MISMATCH


def reset_warned() -> None:
    _WARNED.clear()
