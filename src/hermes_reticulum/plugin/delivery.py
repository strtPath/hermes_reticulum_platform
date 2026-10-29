"""Delivery handling for the Reticulum platform adapter (spec section 6).

Owns three things the adapter delegates:

- **Send-result mapping** (:func:`map_receipt`): translate an LXMF delivery
  state into a gateway ``SendResult``. The mapping is a translation, not a
  design problem (spec section 3): ``DELIVERED`` → success, ``FAILED`` after
  retries → failure with a real ``error_kind``, ``SENT`` (propagated) →
  success plus an entry in the pending set.
- **The propagation pending set** (:class:`PropagationPendingSet`): the
  authoritative delivery record for propagated sends. The gateway's ledger
  is wrong in both directions on a mesh (spec section 6), so this set is
  the record that stays true. It is constructed once in ``__init__`` and
  survives ``disconnect()``/``connect()`` on the same adapter instance —
  never rebuilt in ``connect()``.
- **Chunking** (:func:`sequence_chunks`): the port of the downlink
  ``_BLOCK_CONTENT_BUDGET`` and ``[tag i/N]`` prefix logic from
  ``core/downlink.py``.

The adapter never writes to the ledger and never re-sends on its own
initiative (spec section 16 invariant).
"""

import logging
import threading
from dataclasses import dataclass

from gateway.platforms.base import SendResult

logger = logging.getLogger("hermes_reticulum.delivery")

# LXMF 1.1.1 delivery-state values (hardcoded; the LXMF state list values are
# NOT contiguous indices — see core/downlink.py ``_STATE_MAP``).
STATE_GENERATING = 0x00
STATE_OUTBOUND = 0x01
STATE_SENDING = 0x02
STATE_SENT = 0x04          # node accepted (propagated) — no further signal
STATE_DELIVERED = 0x08     # direct delivery confirmed
STATE_REJECTED = 0xFD
STATE_CANCELLED = 0xFE
STATE_FAILED = 0xFF

# Outcome names, matching core/downlink.py ``_STATE_OUTCOME``.
_OUTCOME_DELIVERED = "delivered"
_OUTCOME_PROPAGATED = "propagated"
_OUTCOME_FAILED = "failed"

# Error kinds the adapter emits (the full set: too_long, bad_format,
# forbidden, not_found, rate_limited, transient, unknown).
_ERR_TRANSIENT = "transient"
_ERR_TOO_LONG = "too_long"
_ERR_UNKNOWN = "unknown"


# ── Chunking (ported from core/downlink.py) ────────────────────────────────


# Block content budget (LXMF 1.1.1): 368 bytes max content per link block.
# Carried over verbatim from core/downlink.py:43 — do not re-derive.
_BLOCK_CONTENT_BUDGET = 368


def sequence_chunks(parts: list, tag: str) -> list:
    """Prefix each part with ``[tag i/N] `` so recipients can spot a dropped
    tail without protocol changes. Single-part lists are returned unchanged.

    Ported from ``core/downlink.py:sequence_chunks``: the prefix is counted
    against the per-block budget and the part is truncated on a UTF-8
    codepoint boundary if the prefix would push it over budget.
    """
    if len(parts) <= 1:
        return parts
    n = len(parts)
    out = []
    for i, part in enumerate(parts, 1):
        prefix = f"[{tag} {i}/{n}] "
        encoded = prefix + part
        # Never let the prefix push a part over a single-block boundary.
        # If the part itself is over budget, truncate the part (not the prefix).
        max_bytes = _BLOCK_CONTENT_BUDGET - len(prefix.encode("utf-8"))
        if max_bytes <= 0:
            out.append(encoded)
            continue
        raw = part.encode("utf-8")
        if len(raw) > max_bytes:
            # Truncate on a UTF-8 codepoint boundary.
            truncated = raw[:max_bytes]
            # Drop any incomplete trailing codepoint.
            while truncated:
                try:
                    truncated.decode("utf-8")
                    break
                except UnicodeDecodeError:
                    truncated = truncated[:-1]
            encoded = prefix + truncated.decode("utf-8")
        out.append(encoded)
    return out


def _utf8_codepoint_chunks(text: str, budget: int) -> list:
    """Split ``text`` on UTF-8 codepoint boundaries into parts of at most
    ``budget`` bytes. No empty parts are produced for non-empty input.

    Kept for backward compatibility; new code prefers
    :func:`_content_aware_chunks`, which is line-/paragraph-/fence-aware.
    """
    if not text:
        return []
    out = []
    cur = []
    cur_bytes = 0
    for ch in text:
        b = len(ch.encode("utf-8"))
        if cur and cur_bytes + b > budget:
            out.append("".join(cur))
            cur = []
            cur_bytes = 0
        cur.append(ch)
        cur_bytes += b
    if cur:
        out.append("".join(cur))
    return out


def _fence_info(stripped: str) -> tuple[int, str | None]:
    """Return (run_length, kind) for a stripped line.

    kind is '`' or '~' when the line starts with at least three fence
    characters; run_length is the count. Returns (0, None) for non-fence
    lines.  An info string after the run (e.g. "python" in ```python) is
    allowed on opening fences — closing fences must be whitespace-only.
    """
    if not stripped:
        return 0, None
    ch = stripped[0]
    if ch not in ("`", "~"):
        return 0, None
    run = 0
    for c in stripped:
        if c == ch:
            run += 1
        else:
            break
    if run < 3:
        return 0, None
    return run, ch


def _content_aware_chunks(text: str, budget: int) -> list:
    """Split ``text`` on content-aware boundaries into parts of at most
    ``budget`` bytes.

    Preserves markdown structure so MeshChat / Sideband recipients see
    intact code blocks and paragraphs instead of mid-line breaks:

    - Fenced code blocks (`` ``` `` / `` ~~~ ``) stay whole when they fit
      within one chunk.  When too large they split at line boundaries
      inside the block, never mid-line.
    - Normal prose prefers paragraph breaks (blank lines), then line
      breaks, then word breaks, falling back to codepoint boundaries as
      a last resort.
    - A line that exceeds ``budget`` on its own is split at word
      boundaries via :func:`_split_oversized_line`.

    Single-pass, O(n) in the input length — same asymptotic cost as the
    old codepoint walker.  No regex, no lookahead beyond the current
    fence block.

    No empty parts are produced for non-empty input (same contract as
    :func:`_utf8_codepoint_chunks`).
    """
    if not text:
        return []

    # Split on line boundaries, preserving the newline characters so
    # reassembly is exact (no guessing about \\n vs \\r\\n).
    lines = text.splitlines(keepends=True)
    if not lines:
        return []

    out: list[str] = []
    cur: list[str] = []
    cur_bytes = 0
    # (index-into-cur, bytes) of the last paragraph boundary seen.
    # When we overflow on a non-blank line we can split here instead
    # of at the current position, keeping paragraphs whole.
    last_para_split: tuple[int, int] | None = None
    in_fence = False
    fence_kind: str | None = None   # "`" or "~"
    fence_run: int = 0              # count, e.g. 3 for "```", 4 for "````"

    i = 0
    while i < len(lines):
        line = lines[i]
        line_bytes = len(line.encode("utf-8"))
        stripped = line.strip()

        # ── fence detection ──────────────────────────────────────
        is_fence_open = False
        is_fence_close = False

        if not in_fence:
            run, kind = _fence_info(stripped)
            if run >= 3:
                in_fence = True
                fence_kind = kind
                fence_run = run
                is_fence_open = True
        else:
            run, kind = _fence_info(stripped)
            if kind == fence_kind and run >= fence_run:
                is_fence_close = True

        # ── inside a code block ──────────────────────────────────
        if in_fence and not is_fence_open:
            # Mid-block or closing line — accumulate; flush on close.
            cur.append(line)
            cur_bytes += line_bytes
            if is_fence_close:
                in_fence = False
                fence_kind = None
                fence_run = 0
            i += 1
            continue

        # ── fence open (first line of a block) ───────────────────
        if is_fence_open:
            # Collect the whole block so we can decide whether it
            # fits as a unit.
            block_lines = [line]
            block_bytes = line_bytes
            j = i + 1
            closed = False
            while j < len(lines):
                nl = lines[j]
                nlb = len(nl.encode("utf-8"))
                ns = nl.strip()
                block_lines.append(nl)
                block_bytes += nlb
                run, kind = _fence_info(ns)
                if kind == fence_kind and run >= fence_run:
                    closed = True
                    j += 1
                    break
                j += 1

            # If the block was never closed it ends at EOF; treat it
            # as ordinary lines (no special handling — the closing
            # fence is just missing).
            if not closed:
                # Reset fence state: EOF acts as an implicit close.
                in_fence = False
                fence_kind = None
                fence_run = 0
                # Fall through to normal line handling below.
            else:
                if block_bytes <= budget:
                    # Block fits in ONE chunk — keep it whole.
                    if cur and cur_bytes + block_bytes > budget:
                        out.append("".join(cur))
                        cur = []
                        cur_bytes = 0
                    cur.extend(block_lines)
                    cur_bytes += block_bytes
                else:
                    # Block too large — flush current chunk, then
                    # split the block at its own line boundaries.
                    if cur:
                        out.append("".join(cur))
                        cur = []
                        cur_bytes = 0
                        last_para_split = None
                    for bl in block_lines:
                        blb = len(bl.encode("utf-8"))
                        # Oversized single line inside a fence block:
                        # split it with _split_oversized_line so the
                        # tail is not silently discarded (P1).
                        if blb > budget:
                            if cur:
                                out.append("".join(cur))
                                cur = []
                                cur_bytes = 0
                            sub = _split_oversized_line(bl, budget)
                            for sp in sub[:-1]:
                                out.append(sp)
                            cur.append(sub[-1])
                            cur_bytes = len(sub[-1].encode("utf-8"))
                            continue
                        if cur and cur_bytes + blb > budget:
                            out.append("".join(cur))
                            cur = []
                            cur_bytes = 0
                        cur.append(bl)
                        cur_bytes += blb
                i = j
                in_fence = False
                fence_kind = None
                fence_run = 0
                last_para_split = None
                continue

        # ── normal prose ─────────────────────────────────────────
        is_blank = stripped == ""

        # ── single oversized line ───────────────────────────────
        # A line longer than the budget on its own must be split
        # regardless of whether `cur` is empty (first line, or the
        # first line after a fence block).  Prefer word boundaries,
        # fall back to codepoint.
        if line_bytes > budget:
            if cur:
                out.append("".join(cur))
                cur = []
                cur_bytes = 0
                last_para_split = None
            sub = _split_oversized_line(line, budget)
            # All but the last sub-part become their own chunks;
            # the last one starts the next chunk so short lines
            # that follow can join it.
            for sp in sub[:-1]:
                out.append(sp)
            cur.append(sub[-1])
            cur_bytes = len(sub[-1].encode("utf-8"))
            i += 1
            continue

        # ── normal overflow (multi-line accumulation) ───────────
        if cur and cur_bytes + line_bytes > budget:
            if is_blank:
                # Blank line → flush BEFORE it so the blank sits at
                # the end of the previous chunk (natural paragraph
                # boundary visible to the reader).
                out.append("".join(cur))
                cur = []
                cur_bytes = 0
                last_para_split = None
            elif last_para_split is not None:
                # Remembered paragraph boundary: split there so
                # the current paragraph stays whole instead of
                # being torn mid-stream (P2).
                split_idx, split_bytes = last_para_split
                out.append("".join(cur[:split_idx]))
                cur = cur[split_idx:]
                cur_bytes -= split_bytes
                last_para_split = None
            else:
                # Overflow — flush current chunk starting fresh.
                # This line is known to fit (line_bytes ≤ budget at
                # this point), so it can safely start the new chunk.
                out.append("".join(cur))
                cur = []
                cur_bytes = 0
                last_para_split = None

        cur.append(line)
        cur_bytes += line_bytes
        if is_blank:
            last_para_split = (len(cur), cur_bytes)
        i += 1

    if cur:
        out.append("".join(cur))

    return out


def _split_oversized_line(line: str, budget: int) -> list:
    """Split a single line that exceeds ``budget`` bytes at codepoint
    boundaries.

    Single-line blobs (no newlines) have no structural information to
    preserve, so a codepoint-level split is the best we can do.  For
    multi-line text the parent ``_content_aware_chunks`` handles fence
    and paragraph boundaries before we ever reach this fallback.

    Returns at least one part; no empty parts.  Lossless: joining the
    returned parts reproduces the original line exactly.
    """
    if not line:
        return [line]
    out: list[str] = []
    cur: list[str] = []
    cur_bytes = 0
    for ch in line:
        b = len(ch.encode("utf-8"))
        if cur and cur_bytes + b > budget:
            out.append("".join(cur))
            cur = []
            cur_bytes = 0
        cur.append(ch)
        cur_bytes += b
    if cur:
        out.append("".join(cur))
    return out or [line]


# ── Propagation pending set ────────────────────────────────────────────────


@dataclass(frozen=True)
class PendingEntry:
    """One unconfirmed (propagated) send.

    ``destination`` is the destination hash; ``content`` is the text that was
    sent; ``sequence`` is the per-destination monotonic sequence number.
    """
    destination: str
    content: str
    sequence: int


class PropagationPendingSet:
    """Authoritative record of propagated (unconfirmed) sends (spec section 6).

    Constructed once in the adapter's ``__init__`` and kept across
    ``disconnect()``/``connect()`` on the same instance. The gateway's ledger
    is wrong in both directions on a mesh, so this set — not the ledger — is
    the delivery record a user can inspect to answer "did that message
    actually go".

    The set NEVER writes to the ledger and NEVER re-sends on its own
    initiative (spec section 16 invariant). It only records and reports.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._entries: list = []
        self._counters = {
            "delivered": 0,
            "propagated": 0,
            "failed": 0,
        }
        # Per-destination monotonic sequence.
        self._seq: dict = {}

    # ── Record ────────────────────────────────────────────────────────

    def record_propagated(self, destination: str, content: str) -> PendingEntry:
        """Record a propagated (``SENT``) send and return the new entry."""
        with self._lock:
            self._seq[destination] = self._seq.get(destination, 0) + 1
            seq = self._seq[destination]
            entry = PendingEntry(destination=destination, content=content,
                                  sequence=seq)
            self._entries.append(entry)
            self._counters["propagated"] += 1
            return entry

    def note_delivered(self, destination: str, content: str) -> None:
        """Record a confirmed (``DELIVERED``) send and clear any matching pending entry."""
        with self._lock:
            self._counters["delivered"] += 1
            self._resolve(destination, content)

    def note_failed(self, destination: str, content: str) -> None:
        """Record a failed send and clear any matching pending entry."""
        with self._lock:
            self._counters["failed"] += 1
            self._resolve(destination, content)

    def _resolve(self, destination: str, content: str) -> None:
        """Drop the newest pending entry matching this destination and content.

        A pending entry only exists for a propagated send, but a later
        confirmation (or failure) for the same destination and payload resolves
        it. Content is compared because outcome callbacks carry the message, not
        a sequence number.

        The NEWEST match is dropped, not the oldest: a receipt describes the
        message just sent, so when the same text was sent twice (an older
        propagated send still unconfirmed, a newer direct send now confirmed),
        resolving the older entry would leave the confirmed send marked pending
        forever and silently clear a reply that is still in flight.

        Caller holds the lock.
        """
        for i in range(len(self._entries) - 1, -1, -1):
            e = self._entries[i]
            if e.destination == destination and e.content == content:
                del self._entries[i]
                return

    def note_failed_entry(self, entry) -> bool:
        """Count a failure and remove this EXACT entry. True if it was pending.

        Keyed on identity, not destination+content: the caller is resolving the
        specific send it recorded, and must not clear a newer send's entry that
        happens to share the peer and text.

        The counter is incremented only when the entry was actually present, so
        a send already resolved by a delivery receipt is not also counted as a
        failure (it would appear in both tallies).
        """
        with self._lock:
            try:
                self._entries.remove(entry)
            except ValueError:
                return False
            self._counters["failed"] += 1
            return True

    def was_resolved(self, entry) -> bool:
        """True when ``entry`` is no longer pending (a receipt cleared it).

        Used by the send path: the entry is recorded before the send, so a
        callback that lands during the send resolves it, and the send must not
        then treat the message as still unconfirmed.
        """
        with self._lock:
            return entry not in self._entries

    # ── Inspect ───────────────────────────────────────────────────────

    def pending_for(self, destination: str) -> list:
        """Unconfirmed entries for a destination, oldest first."""
        with self._lock:
            return [e for e in self._entries if e.destination == destination]

    def all_pending(self) -> list:
        """All unconfirmed entries, oldest first."""
        with self._lock:
            return list(self._entries)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def stats(self) -> dict:
        """Counters plus the unconfirmed count."""
        with self._lock:
            counters = dict(self._counters)
        counters["unconfirmed"] = len(self)
        return counters


# ── Send-result mapping ──────────────────────────────────────────────────────


_STATE_NAMES = {
    STATE_GENERATING: "GENERATING",
    STATE_OUTBOUND: "OUTBOUND",
    STATE_SENDING: "SENDING",
    STATE_SENT: "SENT",
    STATE_DELIVERED: "DELIVERED",
    STATE_REJECTED: "REJECTED",
    STATE_CANCELLED: "CANCELLED",
    STATE_FAILED: "FAILED",
}


def _state_name(state: int) -> str:
    """Map an LXMF state value to its name string (unknown → ``state_0xNN``)."""
    return _STATE_NAMES.get(state, f"state_{state:02x}")


def state_name(state) -> str:
    """Public form of :func:`_state_name` for callers outside this module.

    LXMF exposes state constants but no name function of its own, so this
    module owns the mapping both ways (:func:`state_from_name` is the inverse).
    A non-integer state yields ``state_none`` rather than raising.
    """
    if not isinstance(state, int):
        return "state_none"
    return _state_name(state)


def state_from_name(name: str) -> int:
    """Map an LXMF state NAME string back to its value.

    The transport reports outcomes as names (``LXMF.LXMessage.state_name``),
    so the adapter needs the inverse of :func:`_state_name` to reach
    :func:`map_receipt`. An unknown name maps to ``STATE_FAILED``: a state we
    cannot classify must not be reported as delivered.
    """
    if not isinstance(name, str):
        return STATE_FAILED
    for value, label in _STATE_NAMES.items():
        if label == name.upper():
            return value
    return STATE_FAILED


_STATE_OUTCOMES = {
    STATE_DELIVERED: _OUTCOME_DELIVERED,
    STATE_SENT: _OUTCOME_PROPAGATED,
    STATE_FAILED: _OUTCOME_FAILED,
}


def _state_outcome(state: int) -> str:
    """Map an LXMF state value to a delivery outcome string."""
    return _STATE_OUTCOMES.get(state, "unknown")


def map_receipt(
    pending: PropagationPendingSet,
    state: int,
    destination: str,
    content: str,
    reason: str | None = None,
) -> SendResult:
    """Translate an LXMF delivery-state callback into a gateway ``SendResult``.

    - ``DELIVERED`` → ``success=True`` (direct delivery confirmed).
    - ``SENT`` (propagated) → ``success=True`` and an entry in ``pending``.
      No per-message signal arrives afterwards, so the pending set is the
      record of what was actually confirmed.
    - ``FAILED`` → ``success=False`` with ``retryable=True`` and
      ``error_kind="transient"`` (peer unreachable / route dropped).
    - Anything else (``GENERATING``/``OUTBOUND``/``SENDING``/``REJECTED``/
      ``CANCELLED``/unrecognised) → ``success=False`` with
      ``error_kind="unknown"``. Never a benign default.

    ``error_kind`` is always set explicitly — the gateway's
    ``classify_send_error`` substring table is built for API-server wording and
    would misclassify LXMF strings.
    """
    outcome = _state_outcome(state)

    if outcome == _OUTCOME_DELIVERED:
        pending.note_delivered(destination, content)
        return SendResult(success=True)

    if outcome == _OUTCOME_PROPAGATED:
        entry = pending.record_propagated(destination, content)
        logger.info(
            "Reticulum: propagated send recorded (seq=%d); no further "
            "per-message signal will arrive — the pending set is the record.",
            entry.sequence,
        )
        return SendResult(success=True)

    if outcome == _OUTCOME_FAILED:
        pending.note_failed(destination, content)
        err = reason if reason else "delivery failed after retries"
        return SendResult(
            success=False,
            error=err,
            retryable=True,
            error_kind=_ERR_TRANSIENT,
        )

    # Unrecognised / in-flight states: never a benign default.
    err = reason if reason else f"unrecognised delivery state 0x{state:02x}"
    return SendResult(
        success=False,
        error=err,
        retryable=False,
        error_kind=_ERR_UNKNOWN,
    )


def map_chunk_overflow(total_bytes: int) -> SendResult:
    """Over-budget content: ``success=False, retryable=False, too_long``."""
    return SendResult(
        success=False,
        error="message exceeds the single-packet content budget",
        retryable=False,
        error_kind=_ERR_TOO_LONG,
    )


def chunk_for_send(text: str, tag: str) -> list:
    """Split ``text`` for transmission, returning ready-to-send parts.

    This is the seam that joins the two halves of the chunking logic:
    :func:`_content_aware_chunks` splits on paragraph/line/fence-aware
    boundaries, :func:`sequence_chunks` adds the ``[tag i/N]`` prefixes.

    The prefix must be budgeted BEFORE splitting, not after. Splitting at the
    full budget and letting ``sequence_chunks`` re-truncate silently drops the
    bytes between the two cut points: the parts still look well-formed and each
    fits the budget, but the reassembled text is shorter than the original.

    That is why this predicts the prefix width up front and splits against
    ``budget - prefix``. The part count feeds back into the prefix width, so a
    first pass at the widest plausible prefix (``i/N`` with two-digit numbers)
    decides the split, and the exact prefix is applied afterwards. Parts then
    fit the budget with no re-truncation and no loss.
    """
    if not text:
        return []
    # The prefix width depends on the part count, and the part count depends on
    # the prefix width, so iterate to a fixed point: estimate with a prefix,
    # split, recompute the prefix from the actual count, and repeat until the
    # count stops growing. This avoids reserving for a two-digit index while the
    # send actually needs three digits (100+ parts), which would make
    # sequence_chunks truncate and silently drop content.
    parts = _content_aware_chunks(text, _BLOCK_CONTENT_BUDGET)
    if len(parts) <= 1:
        return parts
    seen = 0
    for _ in range(8):  # converges in 2-3 passes; the bound guards a bad input
        reserve = len((f"[{tag} {len(parts)}/{len(parts)}] ").encode())
        budget = _BLOCK_CONTENT_BUDGET - reserve
        if budget <= 0:
            return []
        parts = _content_aware_chunks(text, budget)
        if len(parts) == seen:
            break
        seen = len(parts)
    if len(parts) > 1:
        parts = sequence_chunks(parts, tag)
    return parts
