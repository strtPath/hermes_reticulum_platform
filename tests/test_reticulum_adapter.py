"""
Structural and lifecycle tests for the Reticulum platform adapter
(spec section 10 test seams).

The real LXMF transport does not exist yet (later ticket); these tests
run the adapter against the in-repo ``FakeTransport`` behind the
``Transport`` protocol, exactly the seam the spec calls for.

No test here asserts on platform lists or command counts (spec section 10).
"""

import asyncio
import os
import sys
import threading
import time
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from gateway.platforms.base import BasePlatformAdapter, SendResult

from hermes_reticulum.plugin.adapter import (
    FakeTransport,
    ReticulumPlatformAdapter,
    Transport,
)


class PlatformConfig:
    """Minimal stand-in for the gateway's PlatformConfig dataclass."""

    def __init__(self, extra=None):
        self.extra = extra or {}
        self.enabled = True
        self.token = None
        self.api_key = None
        self.home_channel = None


class CountingFactory:
    """Transport factory that hands out a single shared fake transport."""

    def __init__(self):
        self.transport = FakeTransport()
        self.constructions = 0

    def __call__(self):
        self.constructions += 1
        return self.transport


class HandlerRecorder:
    """Captures the events the base-class handle_message forwards."""

    def __init__(self):
        self.events = []

    async def __call__(self, event):
        self.events.append(event)


def make_adapter(recorder=None):
    adapter = ReticulumPlatformAdapter(
        PlatformConfig(), transport_factory=CountingFactory()
    )
    if recorder is not None:
        adapter.set_message_handler(recorder)
    return adapter


# ── Structure ──────────────────────────────────────────────────────────────


def test_instantiation_satisfies_abstract_interface():
    """All four abstract methods are implemented: instantiation succeeds."""
    adapter = ReticulumPlatformAdapter(PlatformConfig())
    assert isinstance(adapter, BasePlatformAdapter)


def test_transport_protocol_is_structural():
    assert isinstance(FakeTransport(), Transport)


@pytest.mark.asyncio
async def test_connect_passes_platform_extra_to_transport():
    """config.yaml settings must reach the transport.

    The transport reads display_name, announce_interval, storage_path,
    require_shared_instance and propagation_node from ``extra``; if connect()
    builds it with no arguments those settings are silently ignored and the
    transport falls back to env or defaults (spec section 12).
    """
    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return FakeTransport()

    adapter = ReticulumPlatformAdapter(
        PlatformConfig(extra={"propagation_node": "off"}),
        transport_factory=factory,
    )
    assert await adapter.connect() is True
    await adapter.disconnect()
    assert captured.get("extra") == {"propagation_node": "off"}


# ── Lifecycle (spec section 10) ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connect_twice_is_idempotent():
    factory = CountingFactory()
    adapter = ReticulumPlatformAdapter(PlatformConfig(), transport_factory=factory)
    assert await adapter.connect() is True
    assert await adapter.connect() is True
    assert factory.constructions == 1
    assert adapter.is_connected
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_disconnect_then_connect_works():
    adapter = make_adapter()
    assert await adapter.connect() is True
    await adapter.disconnect()
    assert not adapter.is_connected
    assert await adapter.connect() is True
    assert adapter.is_connected
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_disconnect_without_connect_does_not_raise():
    adapter = make_adapter()
    await adapter.disconnect()  # must not raise
    assert not adapter.is_connected


@pytest.mark.asyncio
async def test_is_reconnect_reinitialises_from_scratch():
    factory = CountingFactory()
    adapter = ReticulumPlatformAdapter(PlatformConfig(), transport_factory=factory)
    assert await adapter.connect() is True
    assert await adapter.connect(is_reconnect=True) is True
    assert factory.constructions == 2
    assert adapter.is_connected
    await adapter.disconnect()


# ── Default transport must be the REAL one ────────────────────────────────
# The gateway constructs the adapter with no transport_factory, so the
# default decides what runs in production. Defaulting to FakeTransport
# silently wires production to a no-op: the adapter logs "connected", RNS is
# never imported, nothing is sent, and nothing looks wrong.


def test_default_transport_factory_is_the_real_transport():
    from hermes_reticulum.plugin.adapter import ReticulumPlatformAdapter
    from hermes_reticulum.plugin.transport import ReticulumTransport

    adapter = ReticulumPlatformAdapter(PlatformConfig())
    assert adapter._transport_factory is ReticulumTransport


def test_fake_transport_is_still_injectable():
    """Tests pass the fake explicitly; that seam must keep working."""
    from hermes_reticulum.plugin.adapter import FakeTransport, ReticulumPlatformAdapter

    adapter = ReticulumPlatformAdapter(PlatformConfig(), transport_factory=FakeTransport)
    assert adapter._transport_factory is FakeTransport


# ── Capability flags: the streaming gate ──────────────────────────────────
# The gateway asks the adapter whether it can EDIT a sent message before it
# chooses a streaming transport:
#
#     _adapter_supports_edit = getattr(adapter, "SUPPORTS_MESSAGE_EDITING", True)
#
# The default is True. LXMF cannot edit or retract anything, so leaving this
# unset made the gateway stream the turn into the adapter: every delta left as
# its own LXMF message, cut off mid-sentence, still carrying the streaming
# cursor, and the clean final was suppressed because the consumer believed its
# content had been delivered. Tool calls went the same way.
#
# These flags are facts about the platform, not preferences, so they are
# asserted rather than left to config.


def test_adapter_declares_it_cannot_edit_messages():
    adapter = ReticulumPlatformAdapter(PlatformConfig())
    assert adapter.SUPPORTS_MESSAGE_EDITING is False


def test_adapter_declares_no_native_streaming():
    """No cumulative-update message type exists in LXMF."""
    adapter = ReticulumPlatformAdapter(PlatformConfig())
    assert adapter.SUPPORTS_NATIVE_STREAMING is False


def test_adapter_does_not_claim_the_gateway_default_for_editing():
    """Guard the exact mechanism: the gateway's getattr default is True.

    If the class attribute is ever removed, the gateway silently reverts to
    treating this platform as editable. Asserting the attribute EXISTS (not
    just that getattr is falsy) is the difference between catching that and
    passing on a class that has quietly lost the flag.
    """
    assert "SUPPORTS_MESSAGE_EDITING" in vars(ReticulumPlatformAdapter), (
        "SUPPORTS_MESSAGE_EDITING must be declared on the adapter class itself; "
        "an inherited or absent attribute makes the gateway's "
        "'getattr(adapter, \"SUPPORTS_MESSAGE_EDITING\", True)' fall back to True "
        "and stream the turn into a platform that cannot edit."
    )
    assert getattr(ReticulumPlatformAdapter, "SUPPORTS_MESSAGE_EDITING", True) is False


def test_adapter_does_not_override_edit_message_with_a_lying_stub():
    """The inherited edit_message reports failure, which is the truth.

    LXMF has no edit API. The base class returns
    ``SendResult(success=False, error="Not supported")`` and callers fall back
    to sending anew. An override that reported success would be worse than no
    edit support at all: the stream consumer would finalize against a message
    that never changed.
    """
    assert "edit_message" not in vars(ReticulumPlatformAdapter), (
        "the adapter must not override edit_message: the inherited "
        "implementation reports failure, and a success-reporting stub would "
        "let the stream consumer finalize against an unedited message"
    )


# ── Chunk pacing ──────────────────────────────────────────────────────────
# A multi-part reply is a burst. On a live path that is fine; through a
# propagation node the message still has to sync, so a burst spends LoRa
# airtime for nothing. The pace follows the transport's own report of how the
# last send went.


class _TransportReportingPath(FakeTransport):
    """FakeTransport whose path answer a test can flip."""

    def __init__(self, direct=True):
        super().__init__()
        self.direct = direct

    def last_send_was_direct(self):
        return self.direct


def test_direct_path_does_not_pace_chunks():
    adapter = make_adapter()
    adapter._transport = _TransportReportingPath(direct=True)
    assert adapter._inter_chunk_delay() == adapter.INTER_CHUNK_DIRECT_SECONDS


def test_propagated_path_paces_chunks():
    adapter = make_adapter()
    adapter._transport = _TransportReportingPath(direct=False)
    assert adapter._inter_chunk_delay() == adapter.INTER_CHUNK_PROPAGATED_SECONDS


def test_transport_without_path_state_is_treated_as_direct():
    """An older/other transport must not invent a delay it cannot justify."""
    adapter = make_adapter()

    class _NoAnswer:
        def send_to(self, *a):
            return True

    adapter._transport = _NoAnswer()
    assert adapter._inter_chunk_delay() == adapter.INTER_CHUNK_DIRECT_SECONDS


def test_path_state_error_is_treated_as_direct():
    """A raising transport must not stall the send on an exception."""
    adapter = make_adapter()

    class _Raises:
        def send_to(self, *a):
            return True

        def last_send_was_direct(self):
            raise RuntimeError("no idea")

    adapter._transport = _Raises()
    assert adapter._inter_chunk_delay() == adapter.INTER_CHUNK_DIRECT_SECONDS


@pytest.mark.asyncio
async def test_multi_part_send_paces_between_chunks():
    """The gap is applied between parts, and not after the last one."""
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    adapter = make_adapter()
    assert await adapter.connect() is True
    # Swap the transport AFTER connecting so send()'s is_connected gate passes,
    # while the path answer still comes from this fake.
    adapter._transport = _TransportReportingPath(direct=False)
    long_text = "x" * 400  # forces more than one part

    with mock.patch("asyncio.sleep", fake_sleep):
        result = await adapter.send("ab" * 16, long_text)

    assert result.success is True
    assert len(adapter._transport.sent) > 1, "expected a multi-part send"
    expected = len(adapter._transport.sent) - 1
    assert len(sleeps) == expected, (
        f"expected a pause between {expected} gaps, got {len(sleeps)}"
    )
    assert all(s == adapter.INTER_CHUNK_PROPAGATED_SECONDS for s in sleeps)


# ── Inbound thread bridge (spec section 10) ────────────────────────────────


@pytest.mark.asyncio
async def test_foreign_callback_reaches_handle_message_exactly_once(monkeypatch):
    """An event pushed from a foreign thread (the way RNS would) is
    delivered to handle_message exactly once, then the drain task ends
    cleanly on disconnect.

    The allowlist is pinned empty: the adapter consults it on every inbound
    event, so an operator's configured allowlist would otherwise decide this
    test's verdict and their peer hash would appear in the failure output.
    """
    monkeypatch.delenv("HERMES_RETICULUM_ALLOWED_USERS", raising=False)
    monkeypatch.delenv("HERMES_RETICULUM_ALLOW_ALL", raising=False)
    recorder = HandlerRecorder()
    factory = CountingFactory()
    adapter = ReticulumPlatformAdapter(
        PlatformConfig(), transport_factory=factory,
    )
    adapter.set_message_handler(recorder)
    assert await adapter.connect() is True

    # connect() installed the delivery callback on the fake transport and
    # stashed it in FakeTransport._inbound_cb; fire it from a foreign
    # thread to simulate the RNS callback thread.
    def fire():
        cb = factory.transport._inbound_cb
        if cb:
            cb("0" * 32, "hello mesh")

    t = threading.Thread(target=fire)
    t.start()
    t.join()

    deadline = time.monotonic() + 5.0
    while not recorder.events and time.monotonic() < deadline:
        await asyncio.sleep(0.05)

    await adapter.disconnect()
    assert len(recorder.events) == 1
    event = recorder.events[0]
    assert event.text == "hello mesh"
    assert event.source.chat_id == "0" * 32


@pytest.mark.asyncio
async def test_drain_task_ends_cleanly_on_disconnect():
    adapter = make_adapter()
    assert await adapter.connect() is True
    drain_task = adapter._drain_task
    assert drain_task is not None
    assert not drain_task.done()
    await adapter.disconnect()
    assert drain_task.cancelled()
    # Nothing left over on the adapter.
    assert adapter._drain_task is None
    assert adapter._queue is None


# ── get_chat_info (spec section 10) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_chat_info_known_hash_uses_name_map():
    adapter = make_adapter()
    adapter._names["0" * 32] = "trusted peer"
    info = await adapter.get_chat_info("0" * 32)
    assert info == {"name": "trusted peer", "type": "dm"}


@pytest.mark.asyncio
async def test_get_chat_info_unknown_hash_falls_back():
    adapter = make_adapter()
    info = await adapter.get_chat_info("0" * 32)
    assert info["type"] == "dm"
    assert info["name"] == "0" * 32


@pytest.mark.asyncio
async def test_get_chat_info_never_raises():
    adapter = make_adapter()
    for weird in ("", "not-a-hash", "zz" * 16, "0" * 32):
        info = await adapter.get_chat_info(weird)
        assert "name" in info and "type" in info


# ── send (foundation-stage seam) ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_not_connected_fails_well_formed():
    adapter = make_adapter()
    result = await adapter.send("0" * 32, "hi")
    assert isinstance(result, SendResult)
    assert result.success is False
    assert result.retryable is True
    assert result.error_kind == "transient"


@pytest.mark.asyncio
async def test_send_malformed_hash_fails_bad_format():
    adapter = make_adapter()
    await adapter.connect()
    result = await adapter.send("not-a-real-hash", "hi")
    assert result.success is False
    assert result.retryable is False
    assert result.error_kind == "bad_format"
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_send_empty_content_rejected():
    adapter = make_adapter()
    await adapter.connect()
    result = await adapter.send("0" * 32, "   ")
    assert result.success is False
    assert result.error_kind == "bad_format"
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_send_through_fake_transport_returns_node_acceptance():
    """send() exercises the seam and maps node acceptance (SENT) to
    success=True with an entry in the pending set — never raises. The real
    delivery outcome (DELIVERED vs FAILED) arrives as a state callback."""
    adapter = make_adapter()
    await adapter.connect()
    result = await adapter.send("0" * 32, "hello")
    assert isinstance(result, SendResult)
    # Node acceptance: the gateway's SendResult is binary, so accepted maps
    # to success=True, and the send is recorded as unconfirmed in the
    # adapter's own pending set (spec section 6).
    assert result.success is True
    entries = adapter.unconfirmed_for("0" * 32)
    assert len(entries) == 1
    assert entries[0].content == "hello"
    # The fake transport saw the packet.
    assert adapter._transport.sent == [("0" * 32, "hello")]
    await adapter.disconnect()


# ── Allowlist gating (spec section 14) ────────────────────────────────────
# The behaviour the live phone test depends on: a listed peer gets through,
# an unlisted one is dropped without a reply.


@pytest.mark.asyncio
async def test_allowlisted_peer_is_admitted(monkeypatch):
    monkeypatch.setenv("HERMES_RETICULUM_ALLOWED_USERS", "a" * 32)
    monkeypatch.setenv("HERMES_RETICULUM_ALLOW_ALL", "false")
    recorder = HandlerRecorder()
    factory = CountingFactory()
    adapter = ReticulumPlatformAdapter(PlatformConfig(), transport_factory=factory)
    adapter.set_message_handler(recorder)
    assert await adapter.connect() is True

    factory.transport._inbound_cb("a" * 32, "let me in")
    deadline = time.monotonic() + 5.0
    while not recorder.events and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    await adapter.disconnect()
    assert len(recorder.events) == 1


@pytest.mark.asyncio
async def test_unlisted_peer_is_dropped_without_a_reply(monkeypatch):
    monkeypatch.setenv("HERMES_RETICULUM_ALLOWED_USERS", "a" * 32)
    monkeypatch.setenv("HERMES_RETICULUM_ALLOW_ALL", "false")
    recorder = HandlerRecorder()
    factory = CountingFactory()
    adapter = ReticulumPlatformAdapter(PlatformConfig(), transport_factory=factory)
    adapter.set_message_handler(recorder)
    assert await adapter.connect() is True

    factory.transport._inbound_cb("b" * 32, "not on the list")
    await asyncio.sleep(0.5)
    await adapter.disconnect()
    assert recorder.events == []


@pytest.mark.asyncio
async def test_allowlist_matching_is_case_and_colon_tolerant(monkeypatch):
    """Peers copy hashes from clients that render them in either case,
    sometimes colon-separated; the comparison must not be literal."""
    formatted = ":".join(("ab" * 16).upper()[i:i + 2] for i in range(0, 32, 2))
    monkeypatch.setenv("HERMES_RETICULUM_ALLOWED_USERS", formatted.upper())
    monkeypatch.setenv("HERMES_RETICULUM_ALLOW_ALL", "false")
    recorder = HandlerRecorder()
    factory = CountingFactory()
    adapter = ReticulumPlatformAdapter(PlatformConfig(), transport_factory=factory)
    adapter.set_message_handler(recorder)
    assert await adapter.connect() is True

    factory.transport._inbound_cb(("ab" * 16).lower(), "hi")
    deadline = time.monotonic() + 5.0
    while not recorder.events and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    await adapter.disconnect()
    assert len(recorder.events) == 1


# ── Receipt attribution (spec section 6) ──────────────────────────────────
# Outcome callbacks carry the message, not the chat id. Matching a receipt to
# the peer it was addressed to must use the payload, not "the most recent
# send": with two peers in flight, a single last-chat slot attributes peer A's
# receipt to peer B, and B's send is reported delivered when it is not.


@pytest.mark.asyncio
async def test_receipt_attributed_to_payload_not_last_send():
    adapter = make_adapter()
    assert await adapter.connect() is True
    peer_a, peer_b = "a" * 32, "b" * 32
    await adapter.send(peer_a, "for a")
    await adapter.send(peer_b, "for b")

    # A's receipt arrives AFTER B was sent. Attributing by "last send" would
    # clear B's pending entry and leave A's; matching by payload clears A's.
    adapter._transport.report_outbound("for a", 0x08)  # DELIVERED
    await asyncio.sleep(0.2)

    assert adapter.unconfirmed_for(peer_a) == []
    remaining_b = adapter.unconfirmed_for(peer_b)
    assert len(remaining_b) == 1 and remaining_b[0].content == "for b"
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_failed_callback_is_recorded_not_dropped():
    """A failed send must leave a record. The failed event used to arrive
    without a payload, fall through the drain loop's outbound branch, and be
    dispatched as inbound — where it was dropped, so the failure vanished."""
    adapter = make_adapter()
    assert await adapter.connect() is True
    peer = "c" * 32
    await adapter.send(peer, "doomed")
    assert len(adapter.unconfirmed_for(peer)) == 1

    adapter._transport.report_failed("doomed", "no path")
    await asyncio.sleep(0.2)

    assert adapter.unconfirmed_for(peer) == []
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_delivery_during_send_does_not_leave_it_pending():
    """A direct send can be confirmed while send() is still awaiting the worker
    thread. The entry is recorded before the send, so that callback resolves it
    and the reply is not left marked unconfirmed forever."""
    class ImmediateConfirmTransport(FakeTransport):
        def send_to(self, destination_hash, payload):
            ok = super().send_to(destination_hash, payload)
            # The transport reports delivery synchronously, as a fast direct
            # send would: the callback fires before send() resumes.
            self.report_outbound(payload, 0x08)  # DELIVERED
            return ok

    adapter = ReticulumPlatformAdapter(
        PlatformConfig(), transport_factory=ImmediateConfirmTransport
    )
    assert await adapter.connect() is True
    peer = "d" * 32
    result = await adapter.send(peer, "confirmed fast")
    assert result.success is True
    assert adapter.unconfirmed_for(peer) == [], (
        "a reply confirmed during the send must not remain pending"
    )
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_disconnect_waits_for_in_flight_send():
    """disconnect() must not stop the transport while a send is probing."""
    import threading as _threading

    started = _threading.Event()
    release = _threading.Event()

    class SlowTransport(FakeTransport):
        def send_to(self, destination_hash, payload):
            started.set()
            release.wait(timeout=5)
            return super().send_to(destination_hash, payload)

    adapter = ReticulumPlatformAdapter(
        PlatformConfig(), transport_factory=SlowTransport
    )
    assert await adapter.connect() is True

    send_task = asyncio.create_task(adapter.send("e" * 32, "slow"))
    await asyncio.to_thread(started.wait, 5)
    # Queue the disconnect while the send is in flight, then let it finish.
    disconnect_task = asyncio.create_task(adapter.disconnect())
    await asyncio.sleep(0.1)
    assert not disconnect_task.done(), "disconnect must wait for the in-flight send"
    release.set()
    await asyncio.wait_for(send_task, timeout=5)
    await asyncio.wait_for(disconnect_task, timeout=5)
    assert adapter._transport is None


@pytest.mark.asyncio
async def test_cancelled_send_still_records_true_outcome():
    """Option A: a cancel request does not abandon an in-flight send.

    asyncio.to_thread cannot stop the worker, so the send is allowed to finish,
    its real outcome recorded, and the cancellation surfaced afterwards. The
    entry must not be stranded: a worker returning True leaves it for the
    delivery callback; the caller still sees CancelledError.
    """
    import threading as _threading

    started = _threading.Event()
    release = _threading.Event()

    class SlowTransport(FakeTransport):
        def send_to(self, destination_hash, payload):
            started.set()
            release.wait(timeout=10)
            return super().send_to(destination_hash, payload)

    adapter = ReticulumPlatformAdapter(
        PlatformConfig(), transport_factory=SlowTransport
    )
    assert await adapter.connect() is True
    peer = "f" * 32

    send_task = asyncio.create_task(adapter.send(peer, "slow"))
    await asyncio.to_thread(started.wait, 5)
    send_task.cancel()
    # The worker is still running; the send must not have been torn away, so
    # its entry is still present and its fate not yet decided.
    await asyncio.sleep(0.1)
    assert len(adapter.unconfirmed_for(peer)) == 1

    # Let the worker finish (True = handed to the transport).
    release.set()
    try:
        await asyncio.wait_for(send_task, timeout=5)
    except asyncio.CancelledError:
        pass  # the cancellation is surfaced after the send completes

    # The worker reported success, so the entry stays pending for the delivery
    # callback to resolve; a late delivery then clears it.
    adapter._transport.report_outbound("slow", 0x08)  # DELIVERED
    await asyncio.sleep(0.2)
    assert adapter.unconfirmed_for(peer) == []
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_cancelled_send_that_failed_is_not_left_pending():
    """The other half of Option A: if the worker returns False (no path), the
    send did not leave, so its entry is failed even though the caller was
    cancelled. It must not remain unconfirmed forever."""
    import threading as _threading

    started = _threading.Event()
    release = _threading.Event()

    class FailingTransport(FakeTransport):
        def send_to(self, destination_hash, payload):
            started.set()
            release.wait(timeout=10)
            return False  # no path / no interface

    adapter = ReticulumPlatformAdapter(
        PlatformConfig(), transport_factory=FailingTransport
    )
    assert await adapter.connect() is True
    peer = "1" * 32
    send_task = asyncio.create_task(adapter.send(peer, "fails"))
    await asyncio.to_thread(started.wait, 5)
    send_task.cancel()
    release.set()
    try:
        await asyncio.wait_for(send_task, timeout=5)
    except asyncio.CancelledError:
        pass

    assert adapter.unconfirmed_for(peer) == [], (
        "a failed send must not be left pending, even when cancelled"
    )
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_cancel_arriving_as_worker_succeeds_records_success(monkeypatch):
    """The race the bot named: a cancel lands just as send_to() returns True.

    asyncio.shield only raises CancelledError to the awaiter when the inner task
    is NOT yet done; if the worker completed and the cancel is delivered in the
    same tick, the shield resumes into CancelledError while the worker's result
    is already available. Breaking out without reading that result left
    sent=False and recorded a success as a failure. The result must be read.

    Forced deterministically: to_thread is replaced with a future we complete,
    then the send task is cancelled in the same loop turn.
    """
    import hermes_reticulum.plugin.adapter as adapter_mod

    inner = asyncio.get_running_loop().create_future()

    def fake_to_thread(fn, *args):
        # Do not run the worker here; hand back the controlled future.
        return inner

    monkeypatch.setattr(adapter_mod.asyncio, "to_thread", fake_to_thread)

    adapter = ReticulumPlatformAdapter(PlatformConfig(), transport_factory=FakeTransport)
    assert await adapter.connect() is True
    peer = "2" * 32

    send_task = asyncio.create_task(adapter.send(peer, "quick"))
    await asyncio.sleep(0)  # let send() reach the shield

    # Complete the worker AND cancel the awaiting send in the same tick, exactly
    # the window the bot describes.
    inner.set_result(True)
    send_task.cancel()
    try:
        await asyncio.wait_for(send_task, timeout=5)
    except asyncio.CancelledError:
        pass

    # The worker returned True: the entry must be pending (awaiting delivery),
    # NOT failed.
    assert len(adapter.unconfirmed_for(peer)) == 1, (
        "a successful send must not be recorded as failed by a racing cancel"
    )
    assert adapter.delivery_stats["failed"] == 0
    await adapter.disconnect()
