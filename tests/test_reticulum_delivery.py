"""
Delivery-mapping tests for the Reticulum platform adapter (spec section 10
seams + the ledger-independence case).

Covers:
- DELIVERED receipt -> success=True.
- FAILED after retries -> success=False with a real error_kind.
- SENT (propagated) -> success=True AND an entry in the pending set.
- Packet refusal -> retryable=True.
- Ledger independence: a retryable=True result recorded while the adapter is
  down stays in the adapter's own pending set across disconnect()/connect()
  on the same instance and is still reported unconfirmed. This is the case
  the gateway's boot sweep skips and abandons at 24h, so the adapter's own
  record is the only assertion that can fail here.
- Chunking: long messages split into the expected number of parts, each
  within budget, prefixes correct, single part unchanged, UTF-8 boundaries
  respected (multi-byte characters).
- Unclassified failure -> unknown, never a benign value.
- Over-budget content -> too_long.
- Empty / degenerate input -> log and send nothing (no zero-length packet).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from hermes_reticulum.plugin import delivery
from hermes_reticulum.plugin.adapter import (
    FakeTransport,
    ReticulumPlatformAdapter,
)

# ── Fixtures ───────────────────────────────────────────────────────────────


class PlatformConfig:
    """Minimal stand-in for the gateway's PlatformConfig dataclass."""

    def __init__(self, extra=None):
        self.extra = extra or {}
        self.enabled = True
        self.token = None
        self.api_key = None
        self.home_channel = None


def make_adapter(transport=None):
    adapter = ReticulumPlatformAdapter(PlatformConfig())
    if transport is not None:
        adapter._transport_factory = lambda: transport
    return adapter


# A fake transport that lets us script the delivery-state callback. The real
# LXMF transport fires a state through the adapter's _record_receipt seam;
# this fake lets tests drive that seam directly.
class ScriptedTransport(FakeTransport):
    def __init__(self):
        super().__init__()
        self.refuse = False

    def send_to(self, destination_hash, payload):
        self.sent.append((destination_hash, payload))
        return not self.refuse


# LXMF state values (from hermes_reticulum.plugin.delivery).
DELIVERED = delivery.STATE_DELIVERED
SENT = delivery.STATE_SENT
FAILED = delivery.STATE_FAILED
REJECTED = delivery.STATE_REJECTED  # unrecognised → unknown


# ── Send-result mapping (spec section 6 / 10) ───────────────────────────────


def test_delivered_receipt_maps_to_success():
    pending = delivery.PropagationPendingSet()
    result = delivery.map_receipt(pending, DELIVERED, "0" * 32, "hello")
    assert result.success is True
    assert result.error is None
    # DELIVERED is confirmed, so no pending entry.
    assert len(pending) == 0
    assert pending.stats()["delivered"] == 1


def test_failed_receipt_maps_to_failure_with_error_kind():
    pending = delivery.PropagationPendingSet()
    result = delivery.map_receipt(
        pending, FAILED, "0" * 32, "hello", reason="route dropped after 5 attempts"
    )
    assert result.success is False
    assert result.retryable is True
    assert result.error_kind == "transient"
    # FAILED is not confirmed delivery; no pending entry.
    assert len(pending) == 0
    assert pending.stats()["failed"] == 1


def test_failed_receipt_default_reason_still_transient():
    pending = delivery.PropagationPendingSet()
    result = delivery.map_receipt(pending, FAILED, "0" * 32, "hello")
    assert result.success is False
    assert result.retryable is True
    assert result.error_kind == "transient"
    assert result.error is not None


def test_propagated_sent_maps_to_success_and_pending_entry():
    pending = delivery.PropagationPendingSet()
    result = delivery.map_receipt(pending, SENT, "0" * 32, "hello")
    assert result.success is True
    assert result.error is None
    # Propagated: recorded in the pending set, unconfirmed.
    assert len(pending) == 1
    entry = pending.all_pending()[0]
    assert entry.destination == "0" * 32
    assert entry.content == "hello"
    assert entry.sequence == 1
    assert pending.stats()["propagated"] == 1
    assert pending.stats()["unconfirmed"] == 1


def test_propagated_sequence_increments_per_destination():
    pending = delivery.PropagationPendingSet()
    delivery.map_receipt(pending, SENT, "0" * 32, "one")
    delivery.map_receipt(pending, SENT, "0" * 32, "two")
    entries = pending.pending_for("0" * 32)
    assert [e.sequence for e in entries] == [1, 2]
    assert [e.content for e in entries] == ["one", "two"]


def test_unrecognised_state_maps_to_unknown():
    pending = delivery.PropagationPendingSet()
    # REJECTED / GENERATING / OUTBOUND / SENDING / CANCELLED are not
    # DELIVERED/SENT/FAILED → unclassified → unknown, never a benign default.
    for state in (REJECTED, 0x00, 0x01, 0x02, 0xFE, 0x09):
        result = delivery.map_receipt(pending, state, "0" * 32, "x")
        assert result.success is False
        assert result.error_kind == "unknown", f"state {state:#x}"
    assert len(pending) == 0


def test_unrecognised_state_keeps_reason():
    pending = delivery.PropagationPendingSet()
    result = delivery.map_receipt(pending, REJECTED, "0" * 32, "x", reason="stamp invalid")
    assert result.success is False
    assert result.error_kind == "unknown"
    assert result.error == "stamp invalid"


def test_over_budget_content_is_too_long():
    result = delivery.map_chunk_overflow(5000)
    assert result.success is False
    assert result.retryable is False
    assert result.error_kind == "too_long"


# ── Adapter-level: the send() seam drives the pending set ───────────────────


@pytest.mark.asyncio
async def test_send_through_adapter_records_propagated_entry():
    adapter = make_adapter(ScriptedTransport())
    await adapter.connect()
    result = await adapter.send("0" * 32, "hello mesh")
    # The fake transport accepted the packet → node acceptance (SENT).
    assert result.success is True
    # The send is recorded as unconfirmed in the adapter's own pending set.
    entries = adapter.unconfirmed_for("0" * 32)
    assert len(entries) == 1
    assert entries[0].content == "hello mesh"
    assert adapter.delivery_stats["unconfirmed"] == 1
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_send_packet_refusal_is_retryable_transient():
    transport = ScriptedTransport()
    transport.refuse = True
    adapter = make_adapter(transport)
    await adapter.connect()
    result = await adapter.send("0" * 32, "hello")
    assert result.success is False
    assert result.retryable is True
    assert result.error_kind == "transient"
    # Refusal means nothing reached the transport; no pending entry.
    assert len(adapter.unconfirmed_for("0" * 32)) == 0
    await adapter.disconnect()


# ── Ledger independence (spec section 10, highest value) ────────────────────


@pytest.mark.asyncio
async def test_pending_set_survives_disconnect_and_reconnect():
    """A retryable/propagated result recorded while connected stays in the
    adapter's own pending set after disconnect() then connect() on the same
    instance, and is still reported unconfirmed.

    This is the case the gateway's boot sweep skips and abandons at 24h, so
    the adapter's own record is the only assertion that can fail here.
    The set is constructed in __init__ (NOT in connect()), so it survives.
    """
    adapter = make_adapter(ScriptedTransport())
    await adapter.connect()
    # Record a propagated send while connected.
    result = await adapter.send("0" * 32, "the reply")
    assert result.success is True
    assert len(adapter.unconfirmed_for("0" * 32)) == 1

    # Simulate the ledger case: the adapter goes down, comes back up.
    await adapter.disconnect()
    await adapter.connect()

    # The entry must still be there, still unconfirmed.
    entries = adapter.unconfirmed_for("0" * 32)
    assert len(entries) == 1
    assert entries[0].content == "the reply"
    assert entries[0].sequence == 1
    assert adapter.delivery_stats["unconfirmed"] == 1
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_pending_set_survives_full_reconnect_flag():
    """Same guarantee via connect(is_reconnect=True), the path the gateway's
    reconnect watcher uses after a transport death."""
    adapter = make_adapter(ScriptedTransport())
    await adapter.connect()
    await adapter.send("0" * 32, "persisted")
    assert len(adapter.unconfirmed_for("0" * 32)) == 1

    await adapter.disconnect()
    assert await adapter.connect(is_reconnect=True) is True
    assert len(adapter.unconfirmed_for("0" * 32)) == 1
    await adapter.disconnect()


# ── Chunking (ported from core/downlink.py) ─────────────────────────────────


def test_sequence_chunks_single_part_unchanged():
    assert delivery.sequence_chunks(["hello"], "p1") == ["hello"]


def test_sequence_chunks_multi_part_prefixes_correct():
    parts = delivery.sequence_chunks(["aa", "bb", "cc"], "p1")
    assert parts == ["[p1 1/3] aa", "[p1 2/3] bb", "[p1 3/3] cc"]


def test_sequence_chunks_respects_utf8_boundary():
    # A multi-byte character must not be split mid-codepoint.
    part = "é" * 200  # 200 × 2 bytes = 400 bytes, over budget once prefix added
    out = delivery.sequence_chunks([part], "p1")
    # Single part: returned unchanged (no prefix for single-part).
    assert out == [part]

    # Force multi-part so the prefix + truncation logic engages, and verify
    # the result decodes cleanly as UTF-8 (no corrupted codepoints).
    big = "é" * 500
    out = delivery.sequence_chunks([big], "p1")
    # With a single part there is no split; but verify the truncation helper
    # directly on a multi-byte chunk that would be cut mid-byte.
    chunks = delivery._utf8_codepoint_chunks(big, 10)
    for c in chunks:
        c.encode("utf-8")  # must round-trip
    assert "".join(chunks) == big
    assert all(len(c.encode("utf-8")) <= 10 for c in chunks)


def test_sequence_chunks_truncation_never_splits_codepoint():
    # Build a part whose UTF-8 bytes are just over budget so truncation fires,
    # and confirm no part contains a lone continuation byte.
    prefix = "[p1 1/2] "
    budget = delivery._BLOCK_CONTENT_BUDGET
    max_bytes = budget - len(prefix.encode("utf-8"))
    # A 3-byte emoji repeated so the byte length exceeds max_bytes.
    part = "🚀" * ((max_bytes // 3) + 2)
    out = delivery.sequence_chunks([part, "tail"], "p1")
    assert out[0].startswith(prefix)
    body = out[0][len(prefix):]
    body.encode("utf-8")  # must be valid UTF-8 (no split codepoint)
    assert len(body.encode("utf-8")) <= max_bytes


def test_chunking_produces_no_empty_parts():
    # A message that would chunk to a single empty part must not produce one.
    assert delivery._utf8_codepoint_chunks("", 10) == []
    # Whitespace-only is handled upstream (log + send nothing); the chunker
    # still must not emit an empty part for non-empty input.
    chunks = delivery._utf8_codepoint_chunks("   ", 10)
    assert chunks == ["   "]


def test_long_ascii_message_splits_into_expected_parts():
    # 2000 ASCII chars → ceil(2000 / 368) = 6 parts, each within budget.
    text = "a" * 2000
    chunks = delivery._utf8_codepoint_chunks(text, delivery._BLOCK_CONTENT_BUDGET)
    assert len(chunks) == 6
    for c in chunks:
        assert 0 < len(c.encode("utf-8")) <= delivery._BLOCK_CONTENT_BUDGET
    assert "".join(chunks) == text


def test_long_multibyte_message_splits_and_reassembles():
    text = "é" * 2000  # 4000 bytes
    chunks = delivery._utf8_codepoint_chunks(text, delivery._BLOCK_CONTENT_BUDGET)
    # ceil(4000 / 368) = 11 parts.
    assert len(chunks) == 11
    for c in chunks:
        assert 0 < len(c.encode("utf-8")) <= delivery._BLOCK_CONTENT_BUDGET
    assert "".join(chunks) == text


# ── Empty / degenerate input ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_content_sends_nothing():
    transport = ScriptedTransport()
    adapter = make_adapter(transport)
    await adapter.connect()
    result = await adapter.send("0" * 32, "   ")
    assert result.success is False
    assert result.error_kind == "bad_format"
    # Nothing reached the transport: no zero-length packet.
    assert transport.sent == []
    await adapter.disconnect()


# ── chunk_for_send: the seam joining splitter to prefixer ─────────────────
# These are the tests that were missing. The pieces existed and were tested
# individually, but nothing joined them to adapter.send(), so a long reply
# went out as one oversized packet. A test per piece cannot catch that.


def test_chunk_for_send_short_message_unchanged():
    assert delivery.chunk_for_send("hello", "p1") == ["hello"]


def test_chunk_for_send_splits_long_message():
    text = "a" * 2000
    parts = delivery.chunk_for_send(text, "p1")
    assert len(parts) > 1
    # Every part must fit the block budget INCLUDING its prefix.
    assert all(
        len(p.encode("utf-8")) <= delivery._BLOCK_CONTENT_BUDGET for p in parts
    )


def test_chunk_for_send_parts_reassemble_to_original():
    """Prefixes are overhead, not loss: stripping them must give the original."""
    text = "the quick brown fox " * 100
    parts = delivery.chunk_for_send(text, "p1")
    assert len(parts) > 1
    stripped = []
    for p in parts:
        assert p.startswith("[p1 ")
        stripped.append(p.split("] ", 1)[1])
    assert "".join(stripped) == text


def test_chunk_for_send_multibyte_survives():
    text = "héllo wörld 🎉 " * 200
    parts = delivery.chunk_for_send(text, "p1")
    assert len(parts) > 1
    assert all("\ufffd" not in p for p in parts)
    assert all(
        len(p.encode("utf-8")) <= delivery._BLOCK_CONTENT_BUDGET for p in parts
    )


def test_chunk_for_send_empty_returns_no_parts():
    assert delivery.chunk_for_send("", "p1") == []


def test_chunk_for_send_100plus_parts_reassemble() -> None:
    """A send large enough to need three-digit part numbers must not lose bytes.

    Reserving prefix space for a two-digit index while the send needs three
    makes sequence_chunks truncate each part, silently shortening the reply.
    """
    text = "a" * (delivery._BLOCK_CONTENT_BUDGET * 150)
    parts = delivery.chunk_for_send(text, "p1")
    assert len(parts) > 100, f"wanted a 100+ part send, got {len(parts)}"
    stripped = "".join(p.split("] ", 1)[1] for p in parts)
    assert stripped == text
    assert all(
        len(p.encode("utf-8")) <= delivery._BLOCK_CONTENT_BUDGET for p in parts
    )


# ── Pending set resolves on a later outcome ──────────────────────────────────


def test_pending_entry_cleared_on_delivered() -> None:
    pending = delivery.PropagationPendingSet()
    delivery.map_receipt(pending, SENT, "0" * 32, "hello")
    assert len(pending) == 1
    delivery.map_receipt(pending, DELIVERED, "0" * 32, "hello")
    assert len(pending) == 0
    assert pending.stats()["unconfirmed"] == 0


def test_pending_entry_cleared_on_failed() -> None:
    pending = delivery.PropagationPendingSet()
    delivery.map_receipt(pending, SENT, "0" * 32, "hello")
    assert len(pending) == 1
    delivery.map_receipt(pending, FAILED, "0" * 32, "hello")
    assert len(pending) == 0


def test_pending_resolution_matches_content_not_just_destination() -> None:
    """Two sends to one peer: resolving the first must leave the second pending."""
    pending = delivery.PropagationPendingSet()
    delivery.map_receipt(pending, SENT, "0" * 32, "first")
    delivery.map_receipt(pending, SENT, "0" * 32, "second")
    delivery.map_receipt(pending, DELIVERED, "0" * 32, "first")
    remaining = pending.all_pending()
    assert [e.content for e in remaining] == ["second"]


def test_repeat_reply_resolves_newest_not_oldest() -> None:
    """The same text sent twice: a receipt for the newer send must clear the
    newer entry, leaving the older (still-unconfirmed) one pending. Clearing by
    oldest-match would resolve the wrong send and strand the newer one."""
    pending = delivery.PropagationPendingSet()
    older = pending.record_propagated("0" * 32, "same text")
    newer = pending.record_propagated("0" * 32, "same text")
    delivery.map_receipt(pending, DELIVERED, "0" * 32, "same text")
    remaining = pending.all_pending()
    assert [e.sequence for e in remaining] == [older.sequence]
    assert newer not in remaining


def test_note_failed_entry_clears_exactly_that_entry() -> None:
    """Identity-keyed failure must not clear a newer send of the same text.

    A cancelled/torn-down older send resolves its OWN entry; a newer send to the
    same peer with identical text must stay pending.
    """
    pending = delivery.PropagationPendingSet()
    older = pending.record_propagated("0" * 32, "same text")
    newer = pending.record_propagated("0" * 32, "same text")
    assert pending.note_failed_entry(older) is True
    remaining = pending.all_pending()
    assert [e.sequence for e in remaining] == [newer.sequence]
    # A second call for an already-removed entry is a no-op, not an error, and
    # must not bump the failed counter (the send was already resolved).
    before = pending.stats()["failed"]
    assert pending.note_failed_entry(older) is False
    assert pending.stats()["failed"] == before


# ── Content-aware chunking ─────────────────────────────────────────────────


def _strip_prefixes(parts: list) -> str:
    """Strip ``[pN i/M] `` prefixes and join — inverse of sequence_chunks."""
    stripped = []
    for p in parts:
        if "] " in p:
            stripped.append(p.split("] ", 1)[1])
        else:
            stripped.append(p)
    return "".join(stripped)


class TestContentAwareChunks:
    """_content_aware_chunks preserves markdown structure."""

    def test_empty_returns_empty(self):
        assert delivery._content_aware_chunks("", 100) == []

    def test_short_text_returns_single_part(self):
        assert delivery._content_aware_chunks("hello", 100) == ["hello"]

    def test_single_long_line_splits_at_codepoint(self):
        text = "a" * 500
        parts = delivery._content_aware_chunks(text, 100)
        assert len(parts) > 1
        assert "".join(parts) == text
        assert all(len(p.encode("utf-8")) <= 100 for p in parts)

    def test_code_block_fits_stays_whole(self):
        text = "before\n```\ndef foo():\n    pass\n```\nafter"
        # Each line is short — block fits well within 500 bytes.
        parts = delivery._content_aware_chunks(text, 500)
        assert len(parts) == 1
        assert parts[0] == text

    def test_code_block_preserved_across_chunk_boundary(self):
        # Preceding prose forces a split while the code block stays
        # small enough to fit intact.  Budget is tight: 100 bytes.
        prefix = "prose " * 30  # ~180 bytes — forces a split
        code = "```\ncode\n```"
        text = prefix + "\n" + code + "\nmore"
        parts = delivery._content_aware_chunks(text, 100)
        assert len(parts) > 1, "should force at least one split"
        # The code block must appear whole in one part — no part
        # should contain an unpaired fence.
        for p in parts:
            count = p.count("```")
            assert count in (0, 2), f"unpaired fence in part: {p!r}"
        assert "".join(parts) == text

    def test_oversized_code_block_splits_at_line_boundaries(self):
        # Code block lines are long enough that the block exceeds budget.
        code_lines = [f"# line {i:03d} " + "x" * 50 for i in range(20)]
        block = "```\n" + "\n".join(code_lines) + "\n```"
        text = f"intro\n{block}\noutro"
        parts = delivery._content_aware_chunks(text, 300)
        assert "".join(parts) == text
        # No part should split mid-line inside a code block.
        for p in parts:
            for line in p.splitlines(keepends=True):
                assert len(line.encode("utf-8")) <= 300, (
                    f"line exceeds budget: {line[:60]!r}"
                )

    def test_paragraph_boundary_preferred(self):
        text = "para one a " * 15 + "\n\n" + "para two b " * 15
        parts = delivery._content_aware_chunks(text, 200)
        # We should get at least 2 parts (one per paragraph) because
        # the blank line is a preferred split point.
        assert len(parts) >= 2
        assert "".join(parts) == text
        # The blank line should end a chunk, not sit mid-chunk orphaned.
        assert any(p.endswith("\n\n") for p in parts) or \
               any(p.startswith("\n\n") for p in parts) or \
               any("\n\n" not in p for p in parts), \
               "blank-line paragraph boundary should be a split point"

    def test_triple_tilde_fence_preserved(self):
        text = "before\n~~~\ncode\n~~~\nafter"
        parts = delivery._content_aware_chunks(text, 500)
        assert len(parts) == 1
        assert parts[0] == text

    def test_unclosed_fence_at_eof(self):
        # A fence that opens but never closes ends at EOF — treated as
        # ordinary lines (no crash, no infinite loop).
        text = "before\n```\ncode without close"
        parts = delivery._content_aware_chunks(text, 200)
        assert "".join(parts) == text

    def test_mixed_fence_types(self):
        text = "```\nbacktick block\n```\n\n~~~\ntilde block\n~~~"
        parts = delivery._content_aware_chunks(text, 500)
        assert len(parts) == 1
        assert parts[0] == text

    def test_fence_inside_chunk_with_prose(self):
        # A small fence block should merge with surrounding prose.
        text = "hello\n```\nx = 1\n```\nworld"
        parts = delivery._content_aware_chunks(text, 400)
        assert len(parts) == 1
        assert parts[0] == text

    def test_many_small_fences(self):
        # Several small fence blocks in one message.
        blocks = []
        for i in range(5):
            blocks.append(f"```\nblock {i}\n```")
        text = "\n\n".join(blocks)
        parts = delivery._content_aware_chunks(text, 500)
        assert "".join(parts) == text

    def test_multibyte_in_code_block(self):
        text = "```\n# émoji: 🚀🚀🚀🚀🚀\nprint('héllo')\n```"
        parts = delivery._content_aware_chunks(text, 300)
        assert "".join(parts) == text
        assert all("\ufffd" not in p for p in parts)

    def test_skip_for_short_message(self):
        # A message that fits under _BLOCK_CONTENT_BUDGET skips chunking.
        text = "short message"
        parts = delivery.chunk_for_send(text, "p1")
        assert parts == [text]

    def test_chunk_for_send_preserves_code_block(self):
        # End-to-end: chunk_for_send with enough preceding prose to
        # force a split, while the code block fits intact.
        prefix = "Here is some text. " * 20  # ~420 bytes → forces split
        code = "def answer():\n    return 42"
        text = f"{prefix}\n```\n{code}\n```\nThat's it."
        parts = delivery.chunk_for_send(text, "p1")
        assert len(parts) > 1, "should force at least one split"
        reassembled = _strip_prefixes(parts)
        assert reassembled == text
        # The code block opening and closing should be in the same part.
        for p in parts:
            stripped = p.split("] ", 1)[1] if "] " in p else p
            if "```" in stripped:
                assert stripped.count("```") in (0, 2), (
                    f"fence split across parts: {stripped!r}"
                )

    # ── P1: oversized fenced lines ─────────────────────────────

    def test_oversized_fenced_line_not_silently_truncated(self):
        # A 500-char code line in a fence block exceeds the budget.
        # Must be split by _split_oversized_line, not silently
        # truncated by sequence_chunks.
        line = "x" * 500
        text = f"```\n{line}\n```"
        parts = delivery._content_aware_chunks(text, 300)
        assert "".join(parts) == text
        for p in parts:
            assert len(p.encode("utf-8")) <= 300

    # ── P2: paragraph boundary memory ──────────────────────────

    def test_paragraph_boundary_remembered(self):
        # Greptile P2: "a"*199 + "\n\n" + "b"*99 + "\n" + "c"*99
        # at budget 359. Each paragraph fits whole; the split
        # should land at the blank line, not mid-paragraph.
        text = "a" * 199 + "\n\n" + "b" * 99 + "\n" + "c" * 99 + "\n"
        parts = delivery._content_aware_chunks(text, 359)
        assert "".join(parts) == text
        # The first part should end with the blank line (paragraph A
        # intact), paragraph B starts in the second part.
        assert len(parts) >= 2
        assert parts[0].endswith("\n\n"), (
            f"first part should end at paragraph boundary, got: {parts[0]!r}"
        )

    # ── P3: fence length tracking ──────────────────────────────

    def test_four_backtick_fence_not_closed_by_three(self):
        # A block opened with ```` (4 backticks) contains a line
        # with ``` (3 backticks) as ordinary content. Must not close.
        text = "````\nregular ``` inside\n````"
        parts = delivery._content_aware_chunks(text, 500)
        assert len(parts) == 1, "four-backtick block should stay whole"
        assert parts[0] == text

    def test_longer_fence_kept_whole_with_preceding_prose(self):
        # The comment's literal scenario: prose fills most of the packet,
        # so prose+block overflows while the block alone still fits. A
        # four-backtick block whose body contains a three-backtick line
        # must travel as one intact unit. Matching closers by their first
        # three characters closes the block at the inner "```" and spills
        # "code\n```\n````" into the next packet.
        prefix = "Some prose before the block.\n" * 3  # 87 bytes
        block = "````\n```\ncode\n```\n````\n"           # 22 bytes — fits whole
        tail = "y" * 20
        text = prefix + block + tail
        parts = delivery._content_aware_chunks(text, 100)
        assert "".join(parts) == text
        assert len(parts) == 2, f"unexpected split: {parts!r}"
        assert parts[0] == prefix, (
            f"block should have started a fresh packet, got: {parts[0]!r}"
        )
        assert any(block in p for p in parts), (
            f"four-backtick block was torn by a three-backtick line: {parts!r}"
        )
        assert parts[1] == block + tail

    def test_five_tilde_fence_not_closed_by_three(self):
        text = "~~~~~\ncode\n~~~ not a close\n~~~~~"
        parts = delivery._content_aware_chunks(text, 500)
        assert len(parts) == 1
        assert parts[0] == text

    def test_fence_info_helper(self):
        assert delivery._fence_info("```") == (3, "`")
        assert delivery._fence_info("````python") == (4, "`")
        assert delivery._fence_info("~~~") == (3, "~")
        assert delivery._fence_info("~~~~~") == (5, "~")
        assert delivery._fence_info("hello") == (0, None)
        assert delivery._fence_info("``") == (0, None)
        assert delivery._fence_info("") == (0, None)
        assert delivery._fence_info("``````") == (6, "`")

    def test_fence_info_closing_rejects_trailing_info(self):
        # Closing fences carry no info string: "```python" is content.
        assert delivery._fence_info("```python", closing=True) == (0, None)
        assert delivery._fence_info("````", closing=True) == (4, "`")
        # Opening fences still accept an info string.
        assert delivery._fence_info("```python") == (3, "`")

    def test_fence_close_requires_whitespace_only(self):
        # A ``` block containing a "```python" line: that line is ordinary
        # content, so the block must not be torn there. The budget is set
        # so preceding prose just fits with the opening lines but not with
        # the whole block — closing early would leave the opening in the
        # first packet and the rest of the code in the next one.
        prefix = "Some prose before the block.\n" * 3
        block = "```\n```python\nprint(1)\n```\n"
        tail = "after prose\n"
        text = prefix + block + tail
        budget = len(prefix.encode("utf-8")) + 16
        parts = delivery._content_aware_chunks(text, budget)
        assert "".join(parts) == text
        assert len(parts) == 2, f"unexpected split: {parts!r}"
        assert parts[0] == prefix, (
            f"block should not close at '```python', got: {parts[0]!r}"
        )
        assert parts[1] == block + tail

    def test_longer_close_run_still_closes_shorter_open(self):
        # A closing run at least as long as the opener is a close
        # (CommonMark allows longer closers).
        text = "```\ncode\n````\n"
        parts = delivery._content_aware_chunks(text, 500)
        assert len(parts) == 1
        assert parts[0] == text

    # ── P1 follow-up: paragraph recheck after split ─────────────

    def test_paragraph_split_rechecks_remaining(self):
        # Greptile P1 follow-up: after splitting at a paragraph
        # boundary, the remaining paragraph may still exceed budget.
        # "short para.\n\n" + ("q" * 120 + "\n") * 4 at budget 359
        # should not silently drop bytes.
        text = "short para.\n\n" + ("q" * 120 + "\n") * 4
        parts = delivery._content_aware_chunks(text, 359)
        assert "".join(parts) == text, "no bytes dropped"
        for p in parts:
            assert len(p.encode("utf-8")) <= 359

    # ── P2: fence scan cap ──────────────────────────────────────

    def test_unmatched_fence_does_not_rescan_forever(self):
        # A sequence of long backtick lines with no real closer
        # must not cause O(n²) scanning.  The scan cap kicks in
        # and treats them as ordinary prose.
        lines = ["`" * n for n in range(1000, 3, -1)]
        text = "\n".join(lines)
        parts = delivery._content_aware_chunks(text, 368)
        assert "".join(parts) == text
        # Must finish in reasonable time (test timeout handles that).

    # ── live defect p12: mid-word cuts + whitespace-only parts ──

    def test_oversized_prose_line_splits_at_word_boundary(self):
        # Live defect (p12 1/7→2/7): "…a small dec" / "ision burden…".
        # An oversized prose line must cut at a space, never mid-word.
        line = " ".join(f"word{i}" for i in range(80)) + "\n"
        parts = delivery._split_oversized_line(line, 300)
        assert "".join(parts) == line
        assert all(len(p.encode("utf-8")) <= 300 for p in parts)
        for a, b in zip(parts, parts[1:]):
            assert not (
                a[-1] not in " \t\n\r" and b[0] not in " \t\n\r"
            ), f"mid-word cut: ...{a[-8:]!r} | {b[:8]!r}..."

    def test_oversized_word_falls_back_to_codepoint(self):
        # A single token longer than the budget (URL, base64 blob)
        # has no boundary in reach: codepoint split, as before.
        token = "https://example.com/" + "x" * 500
        parts = delivery._split_oversized_line(token, 300)
        assert "".join(parts) == token
        assert all(len(p.encode("utf-8")) <= 300 for p in parts)

    def test_line_terminator_never_becomes_its_own_part(self):
        # A line at/over budget used to emit its trailing "\n" as a
        # separate whitespace-only part (empty bubble on the phone).
        line = "b" * 300 + "\n"
        parts = delivery._split_oversized_line(line, 300)
        assert "".join(parts) == line
        assert all(len(p.encode("utf-8")) <= 300 for p in parts)
        assert all(p.strip() for p in parts), "no whitespace-only parts"

    def test_no_whitespace_only_parts_over_oversized_lines(self):
        # Blank line between two oversized lines: the old code flushed
        # the blank run as its own part ("\\n\\n") — rendered live as an
        # empty bubble between p12 2/7 and 3/7.
        line1 = ("word " * 120)[:600] + "\n"
        line2 = "b" * 300 + "\n"
        text = line1 + "\n" + line2
        parts = delivery._content_aware_chunks(text, 300)
        assert "".join(parts) == text
        assert all(len(p.encode("utf-8")) <= 300 for p in parts)
        assert all(p.strip() for p in parts), (
            f"whitespace-only part in {[p[:20] for p in parts]!r}"
        )

    def test_chunk_for_send_never_cuts_prose_words(self):
        # End-to-end: labeled parts must not split a word at the seam.
        para = ("the quick brown fox jumps over the lazy dog " * 12) + "\n\n"
        para += ("pack my box with five dozen liquor jugs " * 12) + "\n"
        parts = delivery.chunk_for_send(para, "p12")
        assert len(parts) > 1
        for a, b in zip(parts, parts[1:]):
            assert a[-1] in " \t\n" or b[0] in " \t\n", (
                f"mid-word cut: ...{a[-8:]!r} | {b[:8]!r}..."
            )
        assert all(p.strip() for p in parts)

    # ── review round 2: fold budgeting, indents, unicode spaces ──

    def test_whitespace_blob_stays_within_budget(self):
        # Review P1: the fold used to append the accumulated blank run
        # as one part without a budget check — " "*500 came back as a
        # single 500-byte part, which sequence_chunks truncates.
        for text in (" " * 500, "\n" * 500, "\n" * 800 + "hi\n"):
            parts = delivery._content_aware_chunks(text, 368)
            assert "".join(parts) == text
            assert all(len(p.encode("utf-8")) <= 368 for p in parts), (
                f"over-budget part for {text[:12]!r}"
            )
            assert all(p for p in parts), "no empty parts"

    def test_blank_run_between_blobs_folds_not_bubbles(self):
        # Review P2: a blank run between two near-full lines should be
        # packed into the neighbours' slack (forward then backward),
        # not shipped as its own whitespace-only part.
        line1 = "b" * 290 + "\n"
        line2 = "c" * 290 + "\n"
        text = line1 + "\n" * 8 + line2
        parts = delivery._content_aware_chunks(text, 300)
        assert "".join(parts) == text
        assert all(len(p.encode("utf-8")) <= 300 for p in parts)
        assert all(p.strip() for p in parts), (
            f"whitespace-only part in {[p[:20] for p in parts]!r}"
        )

    def test_indented_line_first_part_carries_content(self):
        # Review P2: a word cut right after a pure-indent run emits a
        # whitespace-only first part (empty bubble).
        line = " " * 40 + "word " * 200 + "\n"
        parts = delivery._split_oversized_line(line, 368)
        assert "".join(parts) == line
        assert all(len(p.encode("utf-8")) <= 368 for p in parts)
        assert parts[0].strip(), f"whitespace-only head: {parts[0]!r}"

    def test_nbsp_prose_splits_at_nbsp(self):
        # Review P3: only space/tab counted as word boundaries, so
        # NBSP-separated prose cut mid-word.
        line = "a" * 200 + "\xa0" + "b" * 200
        parts = delivery._split_oversized_line(line, 300)
        assert "".join(parts) == line
        assert all(len(p.encode("utf-8")) <= 300 for p in parts)
        assert parts[0][-1] == "\xa0", f"seam: {parts[0][-8:]!r}"

    def test_property_lossless_bounded_parts(self):
        # Review P2: randomized corpus over the whole chunker — the
        # cheapest net for a chunker. Lossless, non-empty, <= budget.
        import random

        rng = random.Random(20261114)
        pieces = [
            lambda: " ".join(
                rng.choice(["hi", "there", "wörd", "—", "你好"]) for _ in range(rng.randint(1, 40))
            ),
            lambda: "x" * rng.randint(1, 900),
            lambda: " " * rng.randint(0, 60) + "tok" * rng.randint(1, 150),
            lambda: "\n" * rng.randint(0, 500),
            lambda: "🙂" * rng.randint(1, 200),
            lambda: "line\r\n" * rng.randint(1, 60),
        ]
        for _ in range(120):
            text = "".join(
                rng.choice(pieces)() + rng.choice(["\n", "\n\n", ""])
                for _ in range(rng.randint(1, 6))
            )
            budget = rng.choice([300, 368])
            parts = delivery._content_aware_chunks(text, budget)
            assert "".join(parts) == text, f"lossy for {text[:40]!r}"
            assert all(len(p.encode("utf-8")) <= budget for p in parts)
            assert all(p for p in parts), f"empty part for {text[:40]!r}"
