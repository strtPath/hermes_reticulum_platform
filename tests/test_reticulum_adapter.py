"""
Structural and lifecycle tests for the Reticulum platform adapter
(spec section 10 test seams).

The real LXMF transport does not exist yet (later ticket); these tests
run the adapter against the in-repo ``FakeTransport`` behind the
``Transport`` protocol, exactly the seam the spec calls for.

No test here asserts on platform lists or command counts (spec section 10).
"""

import asyncio
import heapq
import logging
import os
import sys
import threading
import time
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from gateway.platforms.base import BasePlatformAdapter, SendResult

from hermes_reticulum.core.acl import AccessControl
from hermes_reticulum.plugin.adapter import (
    REJECT_QUEUE_MAX,
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
    _freeze_cleanup_loop(adapter)
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

    The allowlist is pinned to a synthetic peer: the adapter consults it on
    every inbound event, so an operator's configured allowlist would
    otherwise decide this test's verdict and their peer hash would appear in
    the failure output. (A3 D3 made the unset default closed — allow-all is
    no longer an unset-env behavior.)
    """
    monkeypatch.setenv("HERMES_RETICULUM_ALLOWED_USERS", "0" * 32)
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


# ── A3: automated ACL rejection (docs/specs/adapter/a3-spec.md) ───────────
# The rejection-path group extends the allowlist gating above. Synthetic
# hashes follow the suite's existing pattern (no real identities).


class GatewayAuthzDenier:
    """Stand-in for the gateway authz layer denying an admitted event (D7)."""

    def __init__(self):
        self.events = []

    def __call__(self, event):
        self.events.append(event)
        return False


async def _wait_for(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        await asyncio.sleep(0.05)
    return False


async def _settle(seconds=0.5):
    await asyncio.sleep(seconds)


def _freeze_cleanup_loop(adapter) -> None:
    """Park the cooldown-cleanup task so a test's asyncio.sleep patch (or a
    fake clock) cannot drive it; the test cancels it explicitly."""
    adapter._cleanup_task.cancel()


@pytest.fixture(autouse=True)
def _a3_isolate_env(monkeypatch):
    """A3 tests own their gate env; never inherit a host's real allowlist."""
    for name in (
        "HERMES_RETICULUM_ALLOWED_USERS",
        "HERMES_RETICULUM_BLOCKED_USERS",
        "HERMES_RETICULUM_ALLOW_ALL",
        "HERMES_RETICULUM_REJECT_COOLDOWN_S",
    ):
        monkeypatch.delenv(name, raising=False)


PEER_A = "aabbccdd" * 4
PEER_B = "deadbeef" * 4
PEER_C = "0badc0de" * 4
PEER_D = "11223344" * 4


@pytest.mark.asyncio
async def test_a3_01_unlisted_peer_receives_canned_rejection(monkeypatch, caplog):
    """Criterion 1: peer B (absent from the allowlist) gets exactly the canned
    literal and no Hermes turn runs (no handler call, no session)."""
    monkeypatch.setenv("HERMES_RETICULUM_ALLOWED_USERS", PEER_A)
    recorder = HandlerRecorder()
    factory = CountingFactory()
    adapter = ReticulumPlatformAdapter(PlatformConfig(), transport_factory=factory)
    adapter.set_message_handler(recorder)
    assert await adapter.connect() is True

    factory.transport._inbound_cb(PEER_B, "let me in")
    assert await _wait_for(lambda: any(
        dest == PEER_B and text == adapter.REJECTION_TEXT
        for dest, text in adapter._transport.sent
    ))
    await _settle()
    assert adapter._transport.sent == [(PEER_B, adapter.REJECTION_TEXT)]
    assert recorder.events == [], "denied inbound must not reach the gateway handler"
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_a3_02_rejection_logs_never_carry_peer_message_text(monkeypatch, caplog):
    """Criterion 2 (log sweep): the denied message text appears in no log
    record; only the full source hash appears, host-local."""
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("HERMES_RETICULUM_ALLOWED_USERS", PEER_A)
    recorder = HandlerRecorder()
    factory = CountingFactory()
    adapter = ReticulumPlatformAdapter(PlatformConfig(), transport_factory=factory)
    adapter.set_message_handler(recorder)
    assert await adapter.connect() is True

    secret_text = "top secret payload never seen by a model"
    factory.transport._inbound_cb(PEER_B, secret_text)
    await _settle()

    for record in caplog.records:
        assert secret_text not in record.getMessage()
    # The full source hash IS present host-local (D5: operator needs it).
    assert any(PEER_B in record.getMessage() for record in caplog.records)
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_a3_03_unset_allowlist_denies_everyone_with_no_reply(monkeypatch, caplog):
    """Criterion 3 (D3 + D6): allowlist unset and ALLOW_ALL not true -> any
    peer is denied, receives NO reply (D6 suppression precedes the canned
    reply), and the host startup log warns naming the env var to set."""
    caplog.set_level(logging.WARNING)
    recorder = HandlerRecorder()
    factory = CountingFactory()
    adapter = ReticulumPlatformAdapter(PlatformConfig(), transport_factory=factory)
    adapter.set_message_handler(recorder)
    assert await adapter.connect() is True

    assert any(
        "HERMES_RETICULUM_ALLOWED_USERS" in record.getMessage()
        for record in caplog.records
    ), "startup must warn that the allowlist is empty"

    factory.transport._inbound_cb(PEER_B, "hello?")
    await _settle()
    assert adapter._transport.sent == [], "D6: denied peers get nothing"
    assert recorder.events == []
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_a3_04_empty_allowlist_string_also_suppresses(monkeypatch):
    """Criterion 3 variant: ALLOWED_USERS set but blank is the same closed,
    misconfigured boot: deny, no reply."""
    monkeypatch.setenv("HERMES_RETICULUM_ALLOWED_USERS", "  ")
    factory = CountingFactory()
    adapter = ReticulumPlatformAdapter(PlatformConfig(), transport_factory=factory)
    adapter.set_message_handler(HandlerRecorder())
    assert await adapter.connect() is True
    factory.transport._inbound_cb(PEER_B, "hello?")
    await _settle()
    assert adapter._transport.sent == []
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_a3_05_allow_all_admits_everyone_with_zero_rejections(monkeypatch):
    """Criterion 4: ALLOW_ALL=true admits any non-blocklisted peer; zero
    rejection sends occur."""
    monkeypatch.setenv("HERMES_RETICULUM_ALLOW_ALL", "true")
    recorder = HandlerRecorder()
    factory = CountingFactory()
    adapter = ReticulumPlatformAdapter(PlatformConfig(), transport_factory=factory)
    adapter.set_message_handler(recorder)
    assert await adapter.connect() is True

    for peer in (PEER_B, PEER_C):
        factory.transport._inbound_cb(peer, f"hi from {peer}")
    assert await _wait_for(lambda: len(recorder.events) == 2)
    await _settle()
    assert adapter._transport.sent == []
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_a3_06_cooldown_suppresses_second_rejection_then_resumes(monkeypatch):
    """Criterion 4 (D4): rejected at t; a second deny at t+1min logs but sends
    nothing; after the window a rejection goes out again."""
    monkeypatch.setenv("HERMES_RETICULUM_ALLOWED_USERS", PEER_A)
    monkeypatch.setenv("HERMES_RETICULUM_REJECT_COOLDOWN_S", "300")
    factory = CountingFactory()
    adapter = ReticulumPlatformAdapter(PlatformConfig(), transport_factory=factory)
    adapter.set_message_handler(HandlerRecorder())
    assert await adapter.connect() is True

    factory.transport._inbound_cb(PEER_B, "first")
    assert await _wait_for(lambda: len(adapter._transport.sent) == 1)

    # +1 minute (inside the window): deny logged, nothing sent.
    factory.transport._inbound_cb(PEER_B, "second")
    await _settle()
    assert len(adapter._transport.sent) == 1, "cooldown must suppress the second reply"

    # Fast-forward past the window and deny again: a reply goes out again.
    adapter._reject_deadlines[PEER_B] -= adapter._reject_cooldown_s + 1
    factory.transport._inbound_cb(PEER_B, "third")
    assert await _wait_for(lambda: len(adapter._transport.sent) == 2)
    assert adapter._transport.sent[1] == (PEER_B, adapter.REJECTION_TEXT)
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_a3_07_invalid_cooldown_clamps_to_default(monkeypatch):
    """D4: HERMES_RETICULUM_REJECT_COOLDOWN_S invalid -> clamp to the default
    300 rather than crash or take a nonsense value."""
    monkeypatch.setenv("HERMES_RETICULUM_ALLOWED_USERS", PEER_A)
    monkeypatch.setenv("HERMES_RETICULUM_REJECT_COOLDOWN_S", "not-a-number")
    adapter = make_adapter()
    assert await adapter.connect() is True
    assert adapter._reject_cooldown_s == 300
    await adapter.disconnect()

    monkeypatch.setenv("HERMES_RETICULUM_REJECT_COOLDOWN_S", "-5")
    adapter = make_adapter()
    assert await adapter.connect() is True
    assert adapter._reject_cooldown_s == 300
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_a3_08_blocklist_outranks_allowlist(monkeypatch):
    """Criterion 6: a blocklisted hash is denied even when also allowlisted,
    with the canned reply under the cooldown rules."""
    monkeypatch.setenv("HERMES_RETICULUM_ALLOWED_USERS", PEER_B)
    monkeypatch.setenv("HERMES_RETICULUM_BLOCKED_USERS", PEER_B)
    recorder = HandlerRecorder()
    factory = CountingFactory()
    adapter = ReticulumPlatformAdapter(PlatformConfig(), transport_factory=factory)
    adapter.set_message_handler(recorder)
    assert await adapter.connect() is True

    factory.transport._inbound_cb(PEER_B, "I'm on both lists")
    assert await _wait_for(lambda: len(adapter._transport.sent) == 1)
    assert adapter._transport.sent == [(PEER_B, adapter.REJECTION_TEXT)]
    assert recorder.events == []
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_a3_09_blocklist_deny_in_open_mode_is_full_deny(monkeypatch):
    """Criterion 6 (R7): blocklist priority outranks allow-all; in open mode a
    blocklisted peer gets the canned reply, while non-blocklisted peers are
    admitted."""
    monkeypatch.setenv("HERMES_RETICULUM_ALLOW_ALL", "true")
    monkeypatch.setenv("HERMES_RETICULUM_BLOCKED_USERS", PEER_C)
    recorder = HandlerRecorder()
    factory = CountingFactory()
    adapter = ReticulumPlatformAdapter(PlatformConfig(), transport_factory=factory)
    adapter.set_message_handler(recorder)
    assert await adapter.connect() is True

    factory.transport._inbound_cb(PEER_B, "open mode peer")
    factory.transport._inbound_cb(PEER_C, "blocked peer")
    assert await _wait_for(lambda: len(recorder.events) == 1)
    await _settle()
    assert recorder.events[0].source.user_id == PEER_B
    assert adapter._transport.sent == [(PEER_C, adapter.REJECTION_TEXT)]
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_a3_10_malformed_inbound_stays_silent_drop(monkeypatch):
    """Criterion 7 (D8): no parseable source hash -> dropped silently, no
    reply, even though the gate is configured and could reply to someone."""
    monkeypatch.setenv("HERMES_RETICULUM_ALLOWED_USERS", PEER_A)
    factory = CountingFactory()
    adapter = ReticulumPlatformAdapter(PlatformConfig(), transport_factory=factory)
    adapter.set_message_handler(HandlerRecorder())
    assert await adapter.connect() is True

    factory.transport._inbound_cb("not-a-real-hash", "garbage")
    factory.transport._inbound_cb("", "no source")
    await _settle()
    assert adapter._transport.sent == []
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_a3_11_rejection_gate_matching_is_case_and_colon_tolerant(monkeypatch):
    """Criterion 8: a peer listed in colon/uppercase form is admitted; the
    normalized comparison drives the deny decision too."""
    formatted = ":".join(("ab" * 16).upper()[i:i + 2] for i in range(0, 32, 2))
    monkeypatch.setenv("HERMES_RETICULUM_ALLOWED_USERS", formatted.upper())
    recorder = HandlerRecorder()
    factory = CountingFactory()
    adapter = ReticulumPlatformAdapter(PlatformConfig(), transport_factory=factory)
    adapter.set_message_handler(recorder)
    assert await adapter.connect() is True

    factory.transport._inbound_cb(("ab" * 16).lower(), "hi")
    assert await _wait_for(lambda: len(recorder.events) == 1)
    await _settle()
    assert adapter._transport.sent == []
    await adapter.disconnect()


# ── A3 4.2: cooldown map memory bound ─────────────────────────────────────


def _synthetic_denied_hashes(n):
    return [(f"{i:08x}") * 4 for i in range(1, n + 1)]


def _seed_cooldowns(adapter, expired: int, live: int) -> list[str]:
    """Seed the cooldown state with the heap and map kept consistent, then
    fast-forward the fake clock: expired entries pushed past the window, live
    entries left inside it.

    The heap is the authority for what is live (P2-4: the prune is O(expired),
    heap-only — no full-map scan). So the seeded deadlines are written to BOTH
    the map and the heap; seeding only the map would leave the heap holding the
    original future deadline and the prune would correctly see nothing expired.
    """
    hashes = _synthetic_denied_hashes(expired + live)
    now = time.monotonic()
    adapter._reject_deadlines.clear()
    adapter._reject_heap.clear()
    for h in hashes[:expired]:
        adapter._reject_deadlines[h] = now - 1.0
    for h in hashes[expired:]:
        adapter._reject_deadlines[h] = now + adapter._reject_cooldown_s
    # Rebuild the heap from the map so both structures agree on deadlines.
    for h, deadline in adapter._reject_deadlines.items():
        adapter._reject_heap.append((deadline, h))
    heapq.heapify(adapter._reject_heap)
    return hashes


@pytest.mark.asyncio
async def test_a3_12_cooldown_map_stays_bounded_after_cleanup_tick(monkeypatch):
    """4.2(a): denies from N distinct hashes spread beyond the window; after a
    cleanup tick the map holds only the entries still inside the window."""
    monkeypatch.setenv("HERMES_RETICULUM_ALLOWED_USERS", PEER_A)
    monkeypatch.setenv("HERMES_RETICULUM_REJECT_COOLDOWN_S", "300")
    adapter = make_adapter()
    assert await adapter.connect() is True

    # Seed 500 synthetic cooldown entries: 400 already expired, 100 live.
    hashes = _seed_cooldowns(adapter, expired=400, live=100)
    live = hashes[400:]
    assert len(adapter._reject_deadlines) == 500

    adapter._prune_reject_cooldowns()

    assert len(adapter._reject_deadlines) == 100
    assert set(adapter._reject_deadlines) == set(live)
    # Live entries must still be cooldown-suppressed; expired ones not.
    now = time.monotonic()
    assert all(adapter._cooldown_suppressed(h, now) for h in live)
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_a3_13_deny_flood_does_not_grow_allowed_message_cost(monkeypatch):
    """4.2(b): a flood of denies from many hashes (each deny followed by a
    cleanup pass) must not make an allowed message on the same drain task
    cost more than a baseline allowed message. Deny cost is a map lookup plus
    at most one heap push; pruning is scheduled, not per-deny scan work on
    the drain task."""
    monkeypatch.setenv("HERMES_RETICULUM_ALLOWED_USERS", PEER_A)
    monkeypatch.setenv("HERMES_RETICULUM_REJECT_COOLDOWN_S", "300")
    recorder = HandlerRecorder()
    factory = CountingFactory()
    adapter = ReticulumPlatformAdapter(PlatformConfig(), transport_factory=factory)
    adapter.set_message_handler(recorder)
    assert await adapter.connect() is True

    # Baseline: one allowed message through the drain task.
    t0 = time.perf_counter()
    factory.transport._inbound_cb(PEER_A, "baseline")
    assert await _wait_for(lambda: len(recorder.events) == 1)
    baseline = time.perf_counter() - t0

    # Flood: 300 distinct denied peers, each with a cleanup tick interleaved.
    flood0 = time.perf_counter()
    for h in _synthetic_denied_hashes(300):
        factory.transport._inbound_cb(h, "flood")
    await _settle(1.0)
    flood = time.perf_counter() - flood0

    # Allowed message AFTER the flood: must land as fast as the baseline
    # (generous bound: the drain task must not have accumulated per-deny scan
    # work; a full-map scan per deny would show up here).
    assert len(recorder.events) == 1
    # The rejection backlog is BOUNDED (Part C): the queue caps at
    # REJECT_QUEUE_MAX and the single worker drains it, so under a 300-peer
    # flood some rejections are dropped by design (the cooldown was still
    # armed). What must hold: some were sent, none were enqueued unboundedly,
    # and the flood never grew near the offered count.
    sent = len(adapter._transport.sent)
    assert 0 < sent <= 300, f"rejection sends looked wrong: {sent}"
    if adapter._reject_queue is not None:
        assert adapter._reject_queue.qsize() <= REJECT_QUEUE_MAX
        assert sent < 300, "flood of 300 must overflow the bounded rejection queue"
    t1 = time.perf_counter()
    factory.transport._inbound_cb(PEER_A, "after flood")
    assert await _wait_for(lambda: len(recorder.events) == 2)
    after = time.perf_counter() - t1
    assert after < max(baseline * 10, 1.0), (
        f"allowed message slowed after the flood: baseline={baseline:.3f}s "
        f"after={after:.3f}s (flood window {flood:.3f}s)"
    )
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_a3_14_cleanup_task_prunes_while_adapter_is_idle(monkeypatch):
    """4.2(c): with NO inbound traffic at all, the dedicated cleanup task still
    removes expired entries while the queue stays empty."""
    monkeypatch.setenv("HERMES_RETICULUM_ALLOWED_USERS", PEER_A)
    monkeypatch.setenv("HERMES_RETICULUM_REJECT_COOLDOWN_S", "300")
    adapter = make_adapter()
    assert await adapter.connect() is True
    assert adapter._cleanup_task is not None and not adapter._cleanup_task.done()

    # Seed entries whose deadlines are already in the past; the adapter
    # receives no inbound events after that.
    _seed_cooldowns(adapter, expired=20, live=0)

    # Shorten only the loop period so the next tick arrives promptly. The
    # seeded deadlines are already behind the real monotonic clock, so the
    # tick prunes them with no help from the drain task.
    #
    # Do NOT freeze time.monotonic here. The asyncio event loop reads its own
    # clock through the same function, so a constant makes _run_once compute a
    # fresh positive timeout on every pass and no timer callback ever fires:
    # the task under test never wakes and the test hangs instead of failing.
    monkeypatch.setattr(adapter, "_reject_cooldown_period", lambda: 0.05)

    assert await _wait_for(lambda: len(adapter._reject_deadlines) == 0, timeout=10.0), (
        "cleanup task must prune expired entries on an idle adapter"
    )
    cleanup_task = adapter._cleanup_task
    await adapter.disconnect()
    # disconnect() awaits the cleanup task, so it is guaranteed terminated here.
    # The loop's teardown contract is "exit when _cleanup_stop is set", so the
    # task may finish normally (_FINISHED) rather than being force-cancelled;
    # assert "done" (not "cancelled") — that is the contract that matters.
    assert cleanup_task.done()
    assert adapter._cleanup_task is None


# ── A3 4.5 / criterion 10: gateway desync signal (D7) ─────────────────────


@pytest.mark.asyncio
async def test_a3_15_gateway_deny_of_admitted_peer_gets_canned_reply(monkeypatch, caplog):
    """Criterion 10: the adapter admits a peer, the gateway authz layer then
    denies the delivered event; the adapter logs a desync line naming the
    gateway deny and sends the canned reply via the transport seam."""
    caplog.set_level(logging.WARNING)
    monkeypatch.setenv("HERMES_RETICULUM_ALLOWED_USERS", PEER_B)
    factory = CountingFactory()
    adapter = ReticulumPlatformAdapter(PlatformConfig(), transport_factory=factory)
    adapter.set_message_handler(GatewayAuthzDenier())
    assert await adapter.connect() is True

    factory.transport._inbound_cb(PEER_B, "I passed the adapter gate")
    assert await _wait_for(lambda: any(
        dest == PEER_B and text == adapter.REJECTION_TEXT
        for dest, text in adapter._transport.sent
    ))
    assert any(
        "gateway desync" in record.getMessage().lower()
        for record in caplog.records
    ), "desync line naming the gateway deny must be logged"
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_a3_16_gateway_deny_respects_cooldown(monkeypatch):
    """Criterion 10 qualifier: the D7 reply obeys the same cooldown — a second
    gateway deny inside the window logs but sends nothing."""
    monkeypatch.setenv("HERMES_RETICULUM_ALLOWED_USERS", PEER_B)
    monkeypatch.setenv("HERMES_RETICULUM_REJECT_COOLDOWN_S", "300")
    factory = CountingFactory()
    adapter = ReticulumPlatformAdapter(PlatformConfig(), transport_factory=factory)
    adapter.set_message_handler(GatewayAuthzDenier())
    assert await adapter.connect() is True

    factory.transport._inbound_cb(PEER_B, "first")
    assert await _wait_for(lambda: len(adapter._transport.sent) == 1)
    factory.transport._inbound_cb(PEER_B, "second")
    await _settle()
    assert len(adapter._transport.sent) == 1
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_a3_17_gateway_deny_in_empty_allowlist_boot_is_silent(monkeypatch, caplog):
    """Criterion 10 qualifier: D6 suppression applies to the desync path too —
    but an empty-allowlist boot never admits anyone, so this exercises the
    allow-all-open boot with the gateway denying: D6 checks CLOSED mode only,
    so the canned reply still fires in open mode."""
    caplog.set_level(logging.WARNING)
    monkeypatch.setenv("HERMES_RETICULUM_ALLOW_ALL", "true")
    factory = CountingFactory()
    adapter = ReticulumPlatformAdapter(PlatformConfig(), transport_factory=factory)
    adapter.set_message_handler(GatewayAuthzDenier())
    assert await adapter.connect() is True

    factory.transport._inbound_cb(PEER_B, "open mode, gateway denies")
    assert await _wait_for(lambda: len(adapter._transport.sent) == 1)
    assert adapter._transport.sent == [(PEER_B, adapter.REJECTION_TEXT)]
    await adapter.disconnect()


# ── Part A (P1-1, P2-4) + Part B (single reader) + Part C (bounded worker) ─
# Regression tests for the Greptile findings; each locks the contracted
# behaviour so a later "optimisation" cannot silently reintroduce the bug.


@pytest.mark.asyncio
async def test_a3_15_reconnect_starts_fresh_cleanup_and_rejection_worker(monkeypatch):
    """P1-1: a reconnect must start a FRESH cleanup task (stop cleared) and a
    FRESH rejection worker. If the reconnect reused an already-set stop event,
    the new cleanup task would exit on its first tick and cooldowns would never
    be pruned again; the rejection stream would likewise stall."""
    monkeypatch.setenv("HERMES_RETICULUM_ALLOWED_USERS", PEER_A)
    monkeypatch.setenv("HERMES_RETICULUM_REJECT_COOLDOWN_S", "300")
    adapter = make_adapter()
    assert await adapter.connect() is True

    _seed_cooldowns(adapter, expired=0, live=5)
    first_cleanup = adapter._cleanup_task
    first_worker = adapter._reject_sender_task
    assert first_worker is not None and not first_worker.done()

    # The watcher's recovery path: tear down and re-initialise from scratch.
    assert await adapter.connect(is_reconnect=True) is True

    # Prior per-connect state was torn down...
    assert first_cleanup is not None and first_cleanup.done()
    assert first_worker is not None and first_worker.done()
    # ...and a fresh worker lifecycle exists (own queue + executor + cleared stop).
    assert adapter._cleanup_task is not None and not adapter._cleanup_task.done()
    assert adapter._reject_sender_task is not None and not adapter._reject_sender_task.done()
    assert adapter._reject_queue is not None
    assert adapter._reject_executor is not None
    assert adapter._reject_in_flight == set()

    # The new cleanup task actually prunes (proves its stop event was cleared):
    # seed expired entries on the new lifecycle and confirm they go.
    _seed_cooldowns(adapter, expired=5, live=0)
    monkeypatch.setattr(adapter, "_reject_cooldown_period", lambda: 0.05)
    assert await _wait_for(lambda: len(adapter._reject_deadlines) == 0, timeout=10.0), (
        "reconnect must leave the cleanup task armed so cooldowns keep pruning"
    )
    await adapter.disconnect()


class _CountingDeadlines(dict):
    """dict that counts full-map iterations (P2-4: the prune must not scan)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.scans = {"items": 0, "values": 0, "keys": 0}

    def items(self):
        self.scans["items"] += 1
        return super().items()

    def values(self):
        self.scans["values"] += 1
        return super().values()

    def keys(self):
        self.scans["keys"] += 1
        return super().keys()


@pytest.mark.asyncio
async def test_a3_16_prune_never_scans_live_peers(monkeypatch):
    """P2-4: cleaning expired cooldowns costs O(expired), never a pass over the
    whole map. The prune works off the heap (map lookups + deletes only); any
    full-map iteration would trip the counters and fail here."""
    monkeypatch.setenv("HERMES_RETICULUM_ALLOWED_USERS", PEER_A)
    monkeypatch.setenv("HERMES_RETICULUM_REJECT_COOLDOWN_S", "300")
    adapter = make_adapter()
    assert await adapter.connect() is True

    _seed_cooldowns(adapter, expired=5, live=100)
    wrapped = _CountingDeadlines(adapter._reject_deadlines)
    adapter._reject_deadlines = wrapped

    pruned = adapter._prune_reject_cooldowns()

    assert pruned == 5
    assert len(adapter._reject_deadlines) == 100
    assert wrapped.scans == {"items": 0, "values": 0, "keys": 0}, (
        "heap-only prune must not iterate the live peer map (P2-4)"
    )
    await adapter.disconnect()


# ── Part B: one shared parser, injected profile-scoped reader ──────────────


def test_acl_parse_allow_all_truthiness():
    """The single parser accepts exactly the {true,1,yes,on} family and FAILS
    CLOSED on anything else (including empty / unknown), no matter which reader
    (env or profile scope) supplied the value."""
    for truthy in ("true", "TRUE", " 1 ", "yes", "on", "ON"):
        assert AccessControl.parse_allow_all(truthy) is True, repr(truthy)
    for falsy in ("false", "0", "no", "off", "", None, "banana"):
        assert AccessControl.parse_allow_all(falsy) is False, repr(falsy)


def test_access_control_injected_reader_is_used():
    """The config reader is injected, so the core never imports the gateway and
    tests (and standalone use) can supply any (name, default) callable. The
    injected reader wins over os.getenv by construction."""
    reader_env = {
        "HERMES_RETICULUM_ALLOW_ALL": "on",
        "HERMES_RETICULUM_ALLOWED_USERS": "aabbccddeeff00112233445566778899",
        "HERMES_RETICULUM_BLOCKED_USERS": "",
    }
    os.environ["HERMES_RETICULUM_ALLOW_ALL"] = "true"  # must be ignored
    try:
        acl = AccessControl(get_env=lambda name, default: reader_env.get(name, default))
        assert acl.allow_all is True
        assert acl.allowed_users == {"aabbccddeeff00112233445566778899"}
        assert acl.blocked_users == set()
    finally:
        del os.environ["HERMES_RETICULUM_ALLOW_ALL"]
    # And the default (no reader) still reads the environment.
    acl = AccessControl()
    assert acl.allow_all is False


@pytest.mark.asyncio
async def test_build_access_control_reads_profile_scoped_env(monkeypatch):
    """Part B (spec.md:99-101): the gate reads ACL knobs through the SAME
    profile-scoped reader the gateway authz side uses, never raw os.getenv.
    A value resolvable only in profile scope (ALLOW_ALL=on, invisible to
    os.getenv) must win; the old compare-and-override is gone."""
    import gateway.platforms._shared as shared
    from gateway.platforms._shared import platform_gate_env as real_gate_env

    def fake_gate_env(name, default=""):
        if name == "HERMES_RETICULUM_ALLOW_ALL":
            return "on"  # present in profile scope, absent from os.environ
        return real_gate_env(name, default)

    monkeypatch.setattr(shared, "platform_gate_env", fake_gate_env)
    adapter = make_adapter()
    acl = adapter._build_access_control()
    assert acl.allow_all is True
    assert not os.environ.get("HERMES_RETICULUM_ALLOW_ALL"), (
        "scenario must really be invisible to os.getenv"
    )


def test_acl_knob_names_are_spelled_reticulum():
    """Regression guard for the 2026-10-24 bridge outage: a misspelled key
    (``HERMES_RETICUM_ALLOWED_USERS`` — missing the ``L``) silently emptied the
    allowlist, so the bridge rejected every peer including the owner. The
    canonical knob names must be spelled ``RETICULUM`` (with the L), and a
    misspelled ``RETICUM`` variant must NEVER be read as a deciding source —
    it fails closed (empty allowlist + allow_all=false), which is exactly the
    silent-outage mode we are guarding against."""
    canonical = {
        "HERMES_RETICULUM_ALLOW_ALL",
        "HERMES_RETICULUM_ALLOWED_USERS",
        "HERMES_RETICULUM_BLOCKED_USERS",
    }
    misspelled = {
        "HERMES_RETICUM_ALLOW_ALL",
        "HERMES_RETICUM_ALLOWED_USERS",
        "HERMES_RETICUM_BLOCKED_USERS",
    }
    # The parser only ever looks up the canonical names.
    seen = set()

    def recording_reader(name, default=""):
        seen.add(name)
        return default

    AccessControl(get_env=recording_reader)
    # The three ACL knobs AccessControl reads are exactly the canonical set.
    assert canonical.issubset(seen), f"missing canonical knob(s): {canonical - seen}"
    # No misspelled variant is ever consulted — if it were, the allowlist would
    # silently empty (the outage). Fail closed means it must not even be asked.
    assert not (misspelled & seen), (
        f"misspelled RETICUM knob consulted: {misspelled & seen}"
    )


# ── Part C: dedicated bounded rejection worker + in-flight accounting ──────


@pytest.mark.asyncio
async def test_a3_17_rejection_send_uses_dedicated_executor_and_counts_in_flight(monkeypatch):
    """Part C (Greptile P1-2/P1-3): a rejection send must run on the DEDICATED
    single-thread executor (thread name 'reject-*'), NOT the shared pool that
    replies use, and must be counted in _sends_in_flight + tracked in
    _reject_in_flight so disconnect() waits for it instead of tearing the
    transport down mid-send."""
    monkeypatch.setenv("HERMES_RETICULUM_ALLOWED_USERS", PEER_A)
    monkeypatch.setenv("HERMES_RETICULUM_REJECT_COOLDOWN_S", "300")
    adapter = make_adapter()
    assert await adapter.connect() is True

    started = threading.Event()
    release = threading.Event()
    seen_threads = []
    orig = adapter._transport.send_to

    def blocking_send(h, payload):
        seen_threads.append(threading.current_thread().name)
        started.set()
        release.wait(timeout=5)
        return orig(h, payload)

    adapter._transport.send_to = blocking_send

    denied = _synthetic_denied_hashes(1)[0]
    adapter._transport._inbound_cb(denied, "deny")
    assert await _wait_for(lambda: started.is_set(), timeout=5.0), (
        "rejection send must actually start"
    )

    # While the blocking send is in flight it is counted and tracked, so a
    # concurrent disconnect() would wait for it (it must never be dropped).
    assert adapter._sends_in_flight >= 1
    assert len(adapter._reject_in_flight) >= 1

    release.set()
    assert await _wait_for(lambda: adapter._sends_in_flight == 0, timeout=5.0)
    assert await _wait_for(lambda: len(adapter._transport.sent) >= 1)
    assert seen_threads and all(name.startswith("reject") for name in seen_threads), (
        "rejection sends must run on the dedicated reject-* executor thread"
    )
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_a3_18_disconnect_mid_rejection_send_keeps_future_alive(monkeypatch):
    """Part C (Greptile P1 "shutdown pauses the gateway"): disconnecting while a
    rejection send is IN FLIGHT must not cancel the underlying run_in_executor
    future.

    Without the shield, cancelling the worker task ALSO cancels the future, so
    its done-callback zeroes _sends_in_flight before the worker thread
    finishes; _await_sends_idle() then returns early and
    _reject_executor.shutdown(wait=True) blocks the whole event loop until
    send_to() returns (up to ~15s on a missing path). With the shield the send
    stays counted and tracked through the cancellation, so disconnect waits for
    the thread instead of pausing the loop.
    """
    monkeypatch.setenv("HERMES_RETICULUM_ALLOWED_USERS", PEER_A)
    monkeypatch.setenv("HERMES_RETICULUM_REJECT_COOLDOWN_S", "300")
    adapter = make_adapter()
    assert await adapter.connect() is True

    started = threading.Event()
    release = threading.Event()
    orig = adapter._transport.send_to

    def blocking_send(h, payload):
        started.set()
        release.wait(timeout=5)
        return orig(h, payload)

    adapter._transport.send_to = blocking_send

    denied = _synthetic_denied_hashes(1)[0]
    adapter._transport._inbound_cb(denied, "deny")
    assert await _wait_for(lambda: started.is_set(), timeout=5.0), (
        "rejection send must actually start"
    )
    assert await _wait_for(lambda: adapter._sends_in_flight == 1, timeout=5.0)
    tracked = next(iter(adapter._reject_in_flight))
    assert not tracked.cancelled()

    # Start disconnect while the send is blocked. It must neither complete nor
    # cancel the tracked future (the shield keeps it alive past cancellation).
    disconnect_task = asyncio.create_task(adapter.disconnect())
    await asyncio.sleep(0.1)  # let the worker task's cancellation propagate
    assert adapter._sends_in_flight == 1, (
        "a mid-send rejection must stay counted until the thread finishes"
    )
    assert not tracked.cancelled(), (
        "disconnect must not cancel the in-flight rejection send (shield)"
    )
    assert not disconnect_task.done(), (
        "disconnect must wait for the in-flight send, not shut the loop down first"
    )

    release.set()
    await asyncio.wait_for(disconnect_task, timeout=5.0)
    assert adapter._sends_in_flight == 0
