"""
Reticulum Platform Adapter for the Hermes Gateway.

Implements the gateway ``BasePlatformAdapter`` contract so the mesh becomes
a first-class platform: inbound LXMF messages are bridged into the asyncio
event loop and replies go back out over the mesh.

Foundation stage (design: docs/spec-reticulum-platform-adapter.md):

- The transport sits behind the ``Transport`` protocol (spec section 5/10).
  A minimal ``FakeTransport`` in this module proves the wiring without a
  live mesh; the real LXMF implementation is a later ticket and is NOT
  imported here.
- ``connect()`` is idempotent, re-initialises from scratch when
  ``is_reconnect=True``, and returns True when the local transport is up.
- ``send()`` returns a well-formed failing ``SendResult`` for every
  failure class known at this stage; the full DELIVERED/FAILED/SENT receipt
  mapping (spec section 6/14) lands with the real transport.
- Deferred by design (later tickets): identity/display-name map beyond a
  trivial stub, the propagation pending set, chunking, error_kind mapping.
"""

import asyncio
import contextlib
import datetime
import heapq
import inspect
import logging
import math
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol, runtime_checkable

from gateway.config import Platform
from gateway.platforms._shared import get_scoped_secret as _get_scoped_secret
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.platforms.event import MessageEvent, MessageType

from hermes_reticulum.core.acl import AccessControl
from hermes_reticulum.plugin import delivery
from hermes_reticulum.plugin.identity import (
    IdentityMap,
    is_valid_destination,
)
from hermes_reticulum.plugin.transport import ReticulumTransport

logger = logging.getLogger("hermes_reticulum.adapter")

# A3 (docs/specs/adapter/a3-spec.md): the canned rejection. A literal string
# constant, never model output (I2). It carries no identity and no explanation
# (spec section 6 out-of-scope).
REJECTION_TEXT = "⛔ Access not authorized."

# A3 4.2: default per-destination rejection cooldown, and the bound on the
# cooldown-cleanup loop period (capped at the cooldown window, spec 4.2).
REJECT_COOLDOWN_DEFAULT_S = 300.0
REJECT_CLEANUP_PERIOD_S = 60.0

# Part C (Greptile P1-2/P1-3): the rejection send runs on a DEDICATED single
# worker with a BOUNDED queue, so a flood of denied peers can never starve
# allowed replies (replies never wait behind the rejection backlog). When the
# queue is full the rejection is dropped — the cooldown is already armed, so
# the peer stays silent for the whole window regardless.
REJECT_QUEUE_MAX = 8


@runtime_checkable
class Transport(Protocol):
    """Test seam for the Reticulum/LXMF transport (spec sections 5 and 10).

    The real implementation wraps an LXMF router; tests substitute a fake.
    Callbacks fire from the transport's own thread, never the asyncio loop.
    """

    def send_to(self, destination_hash: str, payload: str) -> bool:
        """Attempt to send ``payload`` to a destination hash. False when no
        interface can carry the packet (maps to a transient failure)."""
        ...

    def last_send_was_direct(self) -> bool:
        """True when the most recent send went over a live path.

        Optional: a transport that cannot answer returns True, so the adapter
        paces as if the link were fast rather than inventing a delay. Used to
        choose the gap between chunks of a multi-part reply.
        """
        ...

    def register_inbound_callback(self, callback) -> None:
        """Register ``callback(source_hash, payload)`` for messages from peers.

        Fired when a message is addressed to US, on the transport's own
        thread. This is inbound traffic only. It is deliberately a different
        slot from the outbound outcome callback: one slot serving both ran the
        inbound parser over our own outgoing message and echoed every reply
        back into the inbound queue (see the 2026-09-25 findings doc).
        """
        ...

    def register_outbound_callback(self, callback) -> None:
        """Register ``callback(payload, state)`` for our own sent messages.

        Fired when a message WE sent reaches a terminal state, so the adapter
        can report delivery honestly instead of treating acceptance as
        delivery. Distinct from inbound for the reason above.
        """
        ...

    def register_failed_callback(self, callback) -> None:
        """Register ``callback(reason)`` fired on delivery failure."""
        ...

    def start(self) -> None:
        """Start the transport (RNS instance / LXMF router)."""
        ...

    def stop(self) -> None:
        """Stop the transport and release its resources."""
        ...


class FakeTransport:
    """In-repo no-op transport used to prove adapter wiring (spec section 10).

    Not a dependency of the real adapter: it is injected for tests. The
    real LXMF transport comes in a later ticket behind this same protocol.
    """

    def __init__(self):
        self.started = False
        self.stopped = False
        self.sent = []
        # Two slots, mirroring the real transport. Collapsing them is what
        # echoed every reply back in as inbound traffic.
        self._inbound_cb = None
        self._outbound_cb = None
        self._failed_cb = None

    def send_to(self, destination_hash: str, payload: str) -> bool:
        self.sent.append((destination_hash, payload))
        return True

    def last_send_was_direct(self) -> bool:
        return True

    def register_inbound_callback(self, callback):
        self._inbound_cb = callback

    def register_outbound_callback(self, callback):
        self._outbound_cb = callback

    def register_failed_callback(self, callback):
        self._failed_cb = callback

    # Test affordances: drive the slots the way the real transport would.
    def deliver_inbound(self, source_hash: str, payload: str):
        if self._inbound_cb is not None:
            self._inbound_cb(source_hash, payload)

    def report_outbound(self, payload: str, state: int):
        if self._outbound_cb is not None:
            self._outbound_cb(payload, state)

    def report_failed(self, payload: str, reason: str):
        if self._failed_cb is not None:
            self._failed_cb(payload, reason)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


class ReticulumPlatformAdapter(BasePlatformAdapter):
    """Gateway platform adapter bridging Reticulum/LXMF into the agent.

    Capability flags follow spec section 2; ``supports_status_text`` and
    ``REQUIRES_EDIT_FINALIZE`` intentionally stay at their inherited
    ``False`` defaults (no typing indicator, no edit support on LXMF).

    ``SUPPORTS_MESSAGE_EDITING`` is ``False`` because LXMF has no edit API: a
    sent message cannot be altered or retracted. This is a fact about the
    platform, not a preference. With it unset the gateway assumed editable
    (``getattr(adapter, "SUPPORTS_MESSAGE_EDITING", True)``) and streamed the
    turn into the adapter, so every delta went out as its own LXMF message,
    cut off mid-sentence and still carrying the streaming cursor.
    """

    # LXMF cannot edit or retract a message once sent. Declaring this stops the
    # gateway choosing a streaming transport for this platform.
    SUPPORTS_MESSAGE_EDITING = False
    # No native streaming transport either (no cumulative-update message type).
    SUPPORTS_NATIVE_STREAMING = False
    # Do NOT declare edit_message(): there is nothing to edit. A stub that
    # silently succeeded would let the consumer believe its edits landed.
    supports_status_text = False

    REJECTION_TEXT = REJECTION_TEXT

    # The mesh can start a fresh turn after a previous one ended.
    supports_async_delivery = True
    # LXMF over radio benefits from chunking; the gateway otherwise truncates
    # at MAX_MESSAGE_LENGTH (4096), which is larger than one packet carries.
    splits_long_messages = True
    # Reticulum clients render markdown, including fenced code blocks.
    supports_code_blocks = True

    # Gap between chunks of a multi-part reply. Direct sends burst; propagated
    # sends are paced, because the node still has to sync them and a burst
    # spends LoRa airtime for no gain.
    INTER_CHUNK_DIRECT_SECONDS = 0.0
    INTER_CHUNK_PROPAGATED_SECONDS = 2.0

    def __init__(self, config, transport_factory=None, **kwargs):
        # Plugin platforms have no static Platform enum member: Platform._missing_
        # accepts names registered in platform_registry, which the gateway fills
        # in only after this plugin registers itself (registration.py). Until
        # then (tests, standalone imports) we fall back to the local "local"
        # member. The gateway rebinds platform identity from the registry entry
        # at construction time; the pseudo-member lookup here is just so
        # super().__init__ gets a valid member.
        try:
            platform = Platform("reticulum")
        except ValueError:
            platform = Platform.LOCAL
        super().__init__(config=config, platform=platform)
        extra = getattr(config, "extra", {}) or {}

        # Display name (spec section 12) — env is read through the scoped
        # reader, never os.getenv, per spec section 2.
        self.display_name = (
            _get_scoped_secret("RETICULUM_DISPLAY_NAME")
            or extra.get("display_name")
            or "Hermes for Reticulum"
        )
        # Announce interval, minutes; 0 disables periodic re-announce.
        raw_interval = _get_scoped_secret("RETICULUM_ANNOUNCE_INTERVAL")
        if raw_interval is None:
            raw_interval = extra.get("announce_interval", 60)
        self.announce_interval = float(raw_interval)
        self.home_channel = extra.get("home_channel")

        # The transport is a seam, but the DEFAULT must be the real one: the
        # gateway constructs this adapter with no transport_factory, so
        # defaulting to FakeTransport silently wires production to a no-op
        # (adapter logs "connected", RNS is never imported, nothing is sent).
        # FakeTransport stays available for tests, which pass it explicitly.
        self._transport_factory = transport_factory or ReticulumTransport
        self._transport = None

        # Propagation pending set (spec section 6): the authoritative delivery
        # record for propagated (SENT) sends. Constructed ONCE here, not in
        # connect(), so it survives disconnect()/connect() on the same
        # instance — the reconnect path tears down and rebuilds everything
        # else, and a set rebuilt in connect() would be lost exactly when it
        # is needed. The gateway's ledger is wrong in both directions on a
        # mesh; this set is the record that stays true (spec section 16).
        self._pending = delivery.PropagationPendingSet()
        # Map each outbound payload (the exact text handed to the transport) to
        # the destination it was addressed to. Outcome callbacks fire with the
        # message, not the chat id, so a receipt is matched by its payload. A
        # single "last chat" slot misattributes receipts when two peers are sent
        # to before either receipt arrives; this map does not.
        self._outbound_by_payload: dict[str, str] = {}
        # Fallback only, for a payload the map does not know (e.g. a transport
        # that reports a transformed payload). New sends use the map.
        self._last_outbound_chat: str | None = None
        # In-flight sends. disconnect() waits for these so teardown never stops
        # a transport out from under a send that is still probing its path.
        self._send_lock = threading.Lock()
        self._sends_in_flight = 0

        # Per-send tag for the ``[p<N> i/N]`` chunk prefixes. Monotonic for the
        # life of the adapter so a recipient can spot a dropped tail. Kept
        # across reconnects alongside the pending set, for the same reason.
        self._chunk_tag: int | None = None

        # Inbound handler slot. Declared here rather than assumed from the base
        # class: see deliver_inbound() for why the adapter owns this hop.
        self._message_handler = None

        # Thread bridge (spec section 5): transport callbacks arrive on the
        # RNS thread and are shuttled to the asyncio loop through a queue
        # drained by a task.
        self._queue = None
        self._drain_task: asyncio.Task | None = None

        # Identity map (spec section 4): chat_id is the destination hash
        # itself; the adapter owns the hash → display-name map.
        # ``_names`` is exposed as a property for the (existing) tests that
        # write to ``adapter._names[...]`` directly.
        self._identity = IdentityMap()

        # A3 (docs/specs/adapter/a3-spec.md): gate decision is core/acl.py
        # AccessControl behind the seam (4.1); cooldown state lives on the
        # adapter, expiry-ordered (4.2), pruned by a dedicated task.
        self._acl = self._build_access_control()
        self._reject_cooldown_s = self._validate_reject_cooldown()
        self._reject_deadlines: dict[str, float] = {}
        self._reject_heap: list[tuple[float, str]] = []
        self._cleanup_task = None
        self._cleanup_stop = asyncio.Event()

        # Part C (Greptile P1-2/P1-3): rejection-send lifecycle. A bounded queue
        # fed by _send_rejection, drained by a single worker task onto a
        # DEDICATED single-thread executor — kept entirely apart from reply
        # traffic so a flood of denied peers can never starve allowed replies.
        # Created in connect(), torn down in disconnect().
        self._reject_queue: asyncio.Queue | None = None
        self._reject_in_flight: set[asyncio.Future] = set()
        self._reject_stop = asyncio.Event()
        self._reject_sender_task: asyncio.Task | None = None
        self._reject_executor: ThreadPoolExecutor | None = None

    @property
    def name(self) -> str:
        return "Reticulum"

    @property
    def _names(self) -> dict[str, str]:
        # Backward-compatible view of the identity map so existing tests
        # (and future code) can read/write ``adapter._names[hash]`` directly.
        return self._identity._names

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        """Start the transport and the inbound drain loop.

        Idempotent: a second call while up is a no-op returning True.
        ``is_reconnect=True`` tears down and re-initialises from scratch,
        as the gateway's reconnect watcher does after a transport death.
        """
        if self.is_connected and not is_reconnect:
            logger.info("Reticulum: already connected (idempotent connect)")
            return True
        if is_reconnect:
            # Re-initialise from scratch: the watcher calls this after the
            # transport (or process) died, so leftover state is suspect.
            with contextlib.suppress(Exception):
                await self.disconnect()

        transport = self._build_transport()
        # RNS callbacks arrive from the transport thread; push events onto the
        # asyncio queue for the drain task (spec section 5). Two separate
        # registrations: inbound (a peer messaged us) and outbound (a message
        # we sent reached a terminal state). Sharing one slot made every reply
        # we delivered re-enter as if a peer had sent it.
        self._queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        transport.register_inbound_callback(
            lambda source_hash, payload: loop.call_soon_threadsafe(
                self._queue.put_nowait, (source_hash, payload)
            )
        )
        transport.register_outbound_callback(
            lambda payload, state: loop.call_soon_threadsafe(
                self._queue.put_nowait, ("outbound", payload, state)
            )
        )
        transport.register_failed_callback(
            lambda payload, reason: loop.call_soon_threadsafe(
                self._queue.put_nowait, ("failed", payload, reason)
            )
        )
        transport.start()
        self._transport = transport
        self._drain_task = asyncio.create_task(self._drain_loop())
        # A3 4.2: dedicated cooldown-cleanup task — prunes expired rejection
        # entries on its own schedule, independent of inbound traffic.
        # Clear the stop-event first (P1-1): disconnect() sets it on teardown,
        # and a reconnect must not leave the new task exiting immediately —
        # that would strand _reject_deadlines/_reject_heap growth (Greptile P1-1).
        self._cleanup_stop.clear()
        self._cleanup_task = asyncio.create_task(self._prune_reject_cooldowns_loop())
        # Part C: the rejection worker + its dedicated executor are per-connect.
        self._reject_stop.clear()
        self._reject_queue = asyncio.Queue(maxsize=REJECT_QUEUE_MAX)
        self._reject_in_flight = set()
        self._reject_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="reject"
        )
        self._reject_sender_task = asyncio.create_task(self._reject_sender_loop())
        self._mark_connected()
        if self._acl.mode == "closed":
            # A3 4.4: closed default with nothing allowlisted — a misconfigured
            # boot; name the env var the operator must set.
            logger.warning(
                "Reticulum: allowlist is empty and HERMES_RETICULUM_ALLOW_ALL is "
                "not set — the adapter denies every peer and sends no rejection. "
                "Set HERMES_RETICULUM_ALLOWED_USERS (or opt into "
                "HERMES_RETICULUM_ALLOW_ALL=true) to admit peers."
            )
        logger.info(
            "Reticulum adapter connected (display name %s, announce interval %ss)",
            self.display_name, self.announce_interval * 60,
        )
        return True

    def _build_transport(self):
        """Create the transport, handing it the platform settings.

        ``PlatformConfig.extra`` carries display_name, announce_interval,
        storage_path, require_shared_instance and propagation_node. Without
        passing them the transport reads only env or defaults, so a config.yaml
        setting is silently ignored (spec section 12). A test factory that takes
        no arguments is still supported.
        """
        extra = getattr(self.config, "extra", {}) or {}
        try:
            return self._transport_factory(extra=extra)
        except TypeError:
            # Injectable fakes (tests) take no arguments.
            return self._transport_factory()

    async def disconnect(self) -> None:
        """Stop the drain task and the transport. Safe to call before connect."""
        self._mark_disconnected()
        # Stop pulling new rejections FIRST (so nothing new is submitted), then
        # wait for what already went to the executor, then shut it down. A
        # rejection send counts in _sends_in_flight like any other, so the idle
        # wait below covers it with no new machinery (Part C, P1-3).
        if self._reject_sender_task is not None:
            self._reject_stop.set()
            self._reject_sender_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reject_sender_task
            self._reject_sender_task = None
        if self._reject_queue is not None:
            pending = self._reject_queue.qsize()
            if pending:
                # Bounded queue dropped, not drained: the cooldowns were already
                # armed, so those peers stay silent regardless.
                logger.debug("Reticulum: dropping %d queued rejection(s)", pending)
            self._reject_queue = None
        # Wait for any in-flight send to finish before tearing the transport
        # down. send_to() runs in a worker thread; stopping the transport under
        # it makes an in-flight reply fail after its connection check passed.
        # (Covers already-submitted rejection sends too.)
        await self._await_sends_idle()
        if self._drain_task is not None:
            self._drain_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._drain_task
            self._drain_task = None
        if self._cleanup_task is not None:
            self._cleanup_stop.set()
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._cleanup_task
            self._cleanup_task = None
        if self._reject_executor is not None:
            self._reject_executor.shutdown(wait=True)
            self._reject_executor = None
        if self._transport is not None:
            with contextlib.suppress(Exception):
                self._transport.stop()
            self._transport = None
        self._queue = None
        logger.info("Reticulum adapter disconnected")

    async def _await_sends_idle(self, timeout: float = 30.0) -> None:
        """Yield until no send is in flight, or ``timeout`` elapses.

        The send loop checks ``self._transport is not transport`` before each
        part, so a send that outlives this wait aborts cleanly rather than
        calling into a stopped transport.
        """
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            with self._send_lock:
                if self._sends_in_flight == 0:
                    return
            if asyncio.get_running_loop().time() >= deadline:
                logger.warning(
                    "Reticulum: %d send(s) still in flight after %.0fs; disconnecting",
                    self._sends_in_flight, timeout,
                )
                return
            await asyncio.sleep(0.05)

    # ── Inbound thread bridge ────────────────────────────────────────────

    async def _drain_loop(self) -> None:
        """Drain transport events from the queue and hand them to the base
        class ``handle_message``. One event in, one dispatch out."""
        try:
            while True:
                event = await self._queue.get()
                try:
                    if (
                        isinstance(event, tuple)
                        and len(event) == 3
                        and event[0] == "outbound"
                    ):
                        # A message WE sent reached a terminal state. Not
                        # inbound: no source peer, nothing to dispatch.
                        self._handle_outbound_outcome(event[1], event[2])
                    elif (
                        isinstance(event, tuple)
                        and len(event) == 3
                        and event[0] == "failed"
                    ):
                        # A message WE sent failed outright. Before this was
                        # handled it fell through to _dispatch_inbound and was
                        # dropped, so a failed send left no record.
                        self._handle_outbound_failure(event[1], event[2])
                    else:
                        await self._dispatch_inbound(event)
                except Exception as e:
                    # Never let one malformed inbound kill the drain loop
                    # (spec section 14).
                    logger.warning("Reticulum: inbound dispatch failed: %s", e)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Reticulum: drain loop error: %s", e)

    async def _dispatch_inbound(self, event) -> None:
        """Turn one transport callback event into a gateway MessageEvent.

        Foundation-stage parsing: ``event`` is a ``(source_hash, text)``
        tuple (or a dict with ``source``/``text``) produced by the callback
        registration in ``connect()``. The real transport will deliver
        LXMF receipt objects; this seam keeps the drain shape testable now.
        """
        source_hash, text = self._normalize_inbound(event)
        if source_hash is None:
            # Malformed inbound: drop with a log line and no reply
            # (spec section 14; A3 D8 keeps this silent-drop path).
            logger.debug("Reticulum: dropping unparseable inbound %r", event)
            return
        # A3 gate (spec 4.1): core/acl.py AccessControl decides — blocklist
        # priority, allow-all only on explicit opt-in, closed default (D3).
        if not self._acl.is_allowed(source_hash):
            await self._handle_denied_inbound(source_hash)
            return
        source = self.build_source(
            chat_id=source_hash,
            chat_name=self._identity.get_name(source_hash, source_hash),
            chat_type="dm",
            user_id=source_hash,
            user_name=self._identity.get_name(source_hash, source_hash),
        )
        event_obj = MessageEvent(
            text=text,
            message_type=MessageType.TEXT,
            source=source,
            message_id=str(uuid.uuid4()),
            timestamp=datetime.datetime.now(),
        )
        await self.deliver_inbound(event_obj)

    async def _handle_denied_inbound(self, source_hash: str) -> None:
        """A3 deny path (spec 4.1/4.2/4.3, D4/D5/D6).

        Log host-local with the FULL source hash and the message text never.
        D6: a closed boot with an empty allowlist replies to nobody.
        D4: at most one canned rejection per destination per window.
        """
        logger.info(
            "Reticulum: access denied from %s (not permitted by the ACL)",
            source_hash,
        )
        if self._acl.mode == "closed":
            logger.debug(
                "Reticulum: rejection suppressed for %s (empty allowlist, D6)",
                source_hash,
            )
            return
        if self._cooldown_suppressed(source_hash, time.monotonic()):
            logger.info(
                "Reticulum: rejection to %s suppressed by cooldown window",
                source_hash,
            )
            return
        await self._send_rejection(source_hash)

    async def deliver_inbound(self, event) -> None:
        """Hand one inbound event to the gateway's message handler.

        The adapter owns this hop rather than calling the inherited
        ``handle_message`` directly: that method and its ``_message_handler``
        slot are gateway-version surface, and an adapter that only works when
        the base class happens to provide them breaks silently — inbound goes
        deaf — on a gateway that renames either. Falling back to the inherited
        call keeps compatibility with a gateway that supplies its own handler.

        A3 4.5 (D7): the delivery outcome is inspected, not discarded. The
        gateway's ``handle_message`` marks the event with ``_gateway_accepted``
        (base.py: False when its own authz/ingress gate dropped the event,
        True once the event is claimed or queued for a session); a deny there
        is a desync against the adapter gate, so the adapter logs it and sends
        the canned rejection through the transport seam under the same D4/D6
        rules. A custom handler's own falsy return is treated the same way.
        """
        source_hash = getattr(getattr(event, "source", None), "user_id", None)
        if self._message_handler is not None:
            result = self._message_handler(event)
            if inspect.isawaitable(result):
                result = await result
            if source_hash and result is not None and not result:
                await self._note_gateway_desync(source_hash)
            return
        await self.handle_message(event)
        if source_hash and getattr(event, "_gateway_accepted", True) is False:
            await self._note_gateway_desync(source_hash)

    async def _note_gateway_desync(self, source_hash: str) -> None:
        """A3 4.5: log the gateway deny and reply per D7 (cooldown + D6 apply)."""
        logger.warning(
            "Reticulum: gateway desync — adapter admitted %s but the gateway "
            "authz layer denied the delivered event",
            source_hash,
        )
        await self._handle_denied_inbound(source_hash)

    def set_message_handler(self, handler) -> None:
        """Install the gateway's inbound handler (``MessageEvent`` in).

        Defined here so the adapter does not depend on the base class for a
        method it requires (see :meth:`deliver_inbound`).
        """
        self._message_handler = handler

    @staticmethod
    def _normalize_inbound(event):
        """Best-effort ``(source_hash, text)`` extraction; (None, None) to drop.

        Spec section 14: also drops when the source hash is not 32 hex chars
        (the gateway's authz layer applies the allowlist separately; this
        predicate here keeps the drain loop safe from unparseable input).
        """
        if isinstance(event, dict):
            source_hash = event.get("source") or event.get("destination")
            text = event.get("text") or event.get("payload")
        elif isinstance(event, (tuple, list)) and len(event) >= 2:
            source_hash, text = event[0], event[1]
        else:
            return None, None
        if not is_valid_destination(source_hash):
            return None, None
        if not isinstance(text, str) or not text:
            return None, None
        return source_hash, text

    # ── Outbound ─────────────────────────────────────────────────────────

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SendResult:
        """Send text to a destination hash.

        The content is split before transmission: a reply that fits goes out as
        a single packet unchanged, a longer one goes out as several parts
        prefixed ``[p<N> i/N]`` (spec section 14). Every failure returns a
        well-formed failing ``SendResult`` — never a raised exception. On
        success the send is recorded in the propagation pending set, because
        node acceptance is not proof of delivery (spec section 6).
        """
        if not self.is_connected or self._transport is None:
            return SendResult(
                success=False,
                error="Reticulum adapter is not connected",
                retryable=True,
                error_kind="transient",
            )
        if not content or not content.strip():
            # An empty reply is a bug in the turn, not something to send
            # (spec section 14): log it and send nothing.
            logger.warning("Reticulum: refusing to send empty message")
            return SendResult(success=False, error="empty message", error_kind="bad_format")
        if not is_valid_destination(chat_id):
            # A malformed destination is a content error, not a transport one.
            return SendResult(
                success=False,
                error="malformed destination hash",
                retryable=False,
                error_kind="bad_format",
            )
        # One packet carries less than a full reply. Split before sending, so a
        # long reply arrives as several parts instead of being refused or
        # silently truncated (spec section 14). A reply that fits comes back as
        # a single part and is sent unchanged.
        parts = self._chunk_content(content)
        if parts is None:
            return delivery.map_chunk_overflow(len(content.encode("utf-8")))
        if not parts:
            logger.warning("Reticulum: chunking produced no parts; sending none")
            return SendResult(
                success=False, error="empty message", error_kind="bad_format"
            )
        # Remember which peer each payload went to, so an asynchronous delivery
        # receipt (which carries the message, not the chat id) is matched to the
        # right destination. Register every part: receipts can name any of them.
        self._last_outbound_chat = chat_id
        for part in parts:
            self._outbound_by_payload[part] = chat_id

        # Record the pending entry BEFORE sending, so a delivery callback that
        # fires while send_to() is still in flight (a direct send confirms fast)
        # finds the entry and clears it. Recording it after the await left an
        # already-delivered reply marked unconfirmed forever.
        entry = self._pending.record_propagated(chat_id, content)
        # Track this send so disconnect() can wait for it instead of tearing the
        # transport down underneath it.
        transport = self._transport
        with self._send_lock:
            self._sends_in_flight += 1
        cancel_requested = False
        try:
            for index, part in enumerate(parts):
                # send_to() can block: with no cached path it polls for up to 15s
                # waiting for an announce (transport._probe_path). Run it off the
                # event loop so a cold send cannot stall the gateway's other work,
                # including inbound messages.
                if self._transport is not transport:
                    # disconnect()/connect() replaced the transport while this
                    # send was queued: do not call into a torn-down transport.
                    # The message did not go out, so fail THIS entry (by
                    # identity, not by content: a newer send of the same text to
                    # the same peer must not be cleared instead).
                    if entry is not None:
                        self._pending.note_failed_entry(entry)
                    return SendResult(
                        success=False,
                        error="transport was torn down during the send",
                        retryable=True,
                        error_kind="transient",
                    )
                # The worker sends in a thread, off the event loop (send_to can
                # block up to 15s probing for a path).
                #
                # A cancelled coroutine cannot stop that worker thread:
                # asyncio.to_thread is not interruptible. So on cancellation we
                # do NOT abandon the send. We mark the cancellation, wait for the
                # worker's real result, record the entry's true fate, and only
                # then honour the cancellation. Abandoning mid-flight would leave
                # the entry's fate unknown (and possibly stranded), because the
                # packet may already be on the mesh and cannot be recalled.
                task = asyncio.ensure_future(
                    asyncio.to_thread(transport.send_to, chat_id, part)
                )
                sent = False
                while True:
                    try:
                        sent = await asyncio.shield(task)
                        break
                    except asyncio.CancelledError:
                        cancel_requested = True
                        if task.done():
                            # The worker already finished: take its REAL result.
                            # Breaking without reading it left sent=False and
                            # recorded a success as a failure.
                            try:
                                sent = bool(task.result())
                            except Exception as e:
                                logger.warning(
                                    "Reticulum: worker raised during send: %s", e
                                )
                                sent = False
                            break
                        # Keep waiting for the worker, but a second cancel must
                        # not tear us away either: loop again.
                        continue
                if not sent:
                    # No path / no interface: transient, let the base-class retry
                    # logic and the ledger sweep behave (spec section 6). With
                    # propagation configured, a pathless send still leaves the
                    # message on the node, so reaching here means it did not leave
                    # at all -- abort the rest rather than spray the mesh.
                    self._pending.note_failed_entry(entry)
                    entry = None
                    if cancel_requested:
                        raise asyncio.CancelledError()
                    return SendResult(
                        success=False,
                        error="no interface could carry the packet",
                        retryable=True,
                        error_kind="transient",
                    )
                # Pace multi-part sends. A burst of chunks is the fastest way to
                # congest a LoRa link, and the transport already told us whether
                # this send went direct (fast) or via a propagation node (slow).
                # Only the parts after the first pay it.
                if index + 1 < len(parts):
                    pause = self._inter_chunk_delay()
                    if pause > 0:
                        await asyncio.sleep(pause)
        finally:
            with self._send_lock:
                self._sends_in_flight -= 1

        if cancel_requested:
            # The send completed and the entry's fate is recorded; now surface
            # the cancellation the caller asked for.
            raise asyncio.CancelledError()

        # If a delivery callback already resolved this entry during the send,
        # do NOT re-record it: the send is confirmed, not pending.
        if entry is not None and self._pending.was_resolved(entry):
            return SendResult(success=True)

        # The packet(s) reached the transport. The real delivery outcome
        # (DELIVERED vs SENT vs FAILED) arrives as a state callback from the
        # LXMF transport; this foundation-stage seam maps the node-acceptance
        # path. The real transport fires the state through _record_receipt,
        # which calls delivery.map_receipt against the pending set.
        #
        # The pending entry is recorded against the FINAL part under the full
        # original content, so a propagated multi-part send stays inspectable as
        # one unconfirmed message rather than N fragments.
        return SendResult(success=True)

    def _inter_chunk_delay(self) -> float:
        """Seconds to wait between chunks of one multi-part reply.

        A direct send over a live path can take a burst. A propagated send
        travels to a node that then has to sync, so back-to-back chunks there
        buy nothing and cost mesh airtime. Reads the transport's answer when it
        can give one; a transport without the method is treated as direct.
        """
        asker = getattr(self._transport, "last_send_was_direct", None)
        if not callable(asker):
            return self.INTER_CHUNK_DIRECT_SECONDS
        try:
            direct = bool(asker())
        except Exception as e:
            logger.debug("Reticulum: path state unavailable (%s); pacing as direct", e)
            return self.INTER_CHUNK_DIRECT_SECONDS
        return (
            self.INTER_CHUNK_DIRECT_SECONDS
            if direct
            else self.INTER_CHUNK_PROPAGATED_SECONDS
        )

    def _chunk_content(self, content: str) -> list | None:
        """Split outbound content for transmission.

        Returns the parts (a single-element list when the content fits), an
        empty list when chunking degenerates, or ``None`` when the content
        cannot be carried at all. The tag is per-adapter monotonic so a
        recipient can spot a dropped tail without any protocol change.
        """
        if self._chunk_tag is None:
            self._chunk_tag = 0
        self._chunk_tag += 1
        tag = f"p{self._chunk_tag}"
        return delivery.chunk_for_send(content, tag)

    @staticmethod
    def _is_valid_hash(destination_hash: str) -> bool:
        """A destination is 32 hex characters (LXMF hash). Kept for compat."""
        return is_valid_destination(destination_hash)

    def _handle_outbound_outcome(self, payload: str, state: int) -> None:
        """Record the terminal state of a message WE sent.

        ``payload`` is the text we handed to the transport; ``state`` is the
        integer LXMF state. The transport passes the VALUE, not a name: LXMF
        exposes state constants but no name function of its own, so the
        mapping lives in :mod:`hermes_reticulum.plugin.delivery`.

        This resolves the pending entry so a send can be reported as delivered
        rather than merely accepted. Never raises: it runs on the drain task
        and one bad receipt must not stop the loop.
        """
        try:
            state_int = state if isinstance(state, int) else delivery.state_from_name(state)
            # Match the receipt to the peer that payload was addressed to. Fall
            # back to the last chat only when the payload is unknown (a transport
            # that reports transformed text); never guess from the most recent
            # send, which is what misattributes receipts under concurrency.
            chat_id = self._outbound_by_payload.get(payload)
            if chat_id is None:
                chat_id = self._last_outbound_chat or ""
                logger.warning(
                    "Reticulum: outcome for unknown payload; attributing to the "
                    "last send (%s); receipt may be misattributed",
                    chat_id or "none",
                )
            result = self._record_receipt(chat_id, payload, state_int, None)
            if chat_id:
                self._outbound_by_payload.pop(payload, None)
            logger.info(
                "Reticulum: outbound message reached %s (%s)",
                delivery.state_name(state_int),
                "delivered" if result.success else "not delivered",
            )
        except Exception as e:
            logger.warning("Reticulum: could not record outbound outcome: %s", e)

    def _handle_outbound_failure(self, payload: str, reason: str) -> None:
        """Record a message WE sent that failed outright.

        The transport's failed callback carries the payload and the reason; this
        maps it through the same receipt path as a state callback so the failure
        is counted and any matching pending entry is cleared. Before the drain
        loop handled this event it was treated as inbound and dropped, so a
        failed send left no record anywhere.
        """
        try:
            chat_id = self._outbound_by_payload.get(payload)
            if chat_id is None:
                chat_id = self._last_outbound_chat or ""
            self._record_receipt(chat_id, payload, delivery.STATE_FAILED, reason)
            if chat_id:
                self._outbound_by_payload.pop(payload, None)
            logger.info("Reticulum: outbound message failed (%s)", reason)
        except Exception as e:
            logger.warning("Reticulum: could not record outbound failure: %s", e)

    # ── Delivery mapping (spec section 6) ───────────────────────────────

    def _record_receipt(
        self,
        chat_id: str,
        content: str,
        state: int,
        reason: str | None = None,
    ) -> SendResult:
        """Map one LXMF delivery-state callback into a ``SendResult``.

        This is the seam the real transport will drive. ``state`` is an LXMF
        state value; ``reason`` carries the failure string for ``FAILED``.
        The mapping is owned by :func:`hermes_reticulum.plugin.delivery.map_receipt`
        and runs against the adapter's own pending set.
        """
        return delivery.map_receipt(
            self._pending, state, chat_id, content, reason
        )

    @property
    def delivery_stats(self) -> dict[str, int]:
        """Counters for the propagation pending set (spec section 6): the
        unconfirmed set plus delivered/failed tallies. This is the surface a
        user inspects to answer "did that message actually go"."""
        return self._pending.stats()

    def unconfirmed_for(self, chat_id: str) -> list:
        """Unconfirmed (propagated) entries for a destination hash."""
        return self._pending.pending_for(chat_id)

    # ── Chat info ────────────────────────────────────────────────────────

    async def get_chat_info(self, chat_id: str) -> dict[str, Any]:
        """Name and type for a destination hash. Never raises; unknown
        hashes get a fallback built from the hash itself (spec section 10).
        """
        with contextlib.suppress(Exception):
            name = self._identity.get_name(chat_id, str(chat_id))
        return {"name": name, "type": "dm"}

    # ── Access control helpers (A3 spec docs/specs/adapter/a3-spec.md) ───

    def _build_access_control(self) -> AccessControl:
        """Construct the gate decision from the platform gate env (A3 4.1).

        ``AccessControl`` owns the precedence: blocklist outranks everything,
        allow-all only on an explicit opt-in, closed default otherwise (D3).
        The injected reader is the gateway's ``platform_gate_env`` so every ACL
        knob comes from the SAME profile-scoped source the registration/authz
        gate uses (D1, spec.md:99-101). One reader means the two gates agree by
        construction, so there is no second reader to reconcile afterwards — the
        old compare-and-override block is gone (a single reader + one shared
        parser, A3 4.1). Standalone/CI-without-a-gateway falls back to
        ``os.getenv`` (the default reader) and behaviour is unchanged.
        """
        try:
            from gateway.platforms._shared import platform_gate_env
            return AccessControl(get_env=platform_gate_env)
        except ImportError:
            return AccessControl()

    def _validate_reject_cooldown(self) -> float:
        """Read HERMES_RETICULUM_REJECT_COOLDOWN_S (4.2). Invalid or
        non-positive values clamp to the default instead of crashing."""
        try:
            from gateway.platforms._shared import platform_gate_env
            raw = platform_gate_env("HERMES_RETICULUM_REJECT_COOLDOWN_S", "")
        except ImportError:
            raw = os.environ.get("HERMES_RETICULUM_REJECT_COOLDOWN_S", "")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return REJECT_COOLDOWN_DEFAULT_S
        if not math.isfinite(value) or value <= 0:
            return REJECT_COOLDOWN_DEFAULT_S
        return value

    def _reject_cooldown_period(self) -> float:
        """Cleanup loop period: bounded so it never exceeds the window (4.2)."""
        return min(REJECT_CLEANUP_PERIOD_S, self._reject_cooldown_s)

    def _cooldown_suppressed(self, source_hash: str, now: float) -> bool:
        """True when a rejection to ``source_hash`` is inside its window."""
        deadline = self._reject_deadlines.get(source_hash)
        return deadline is not None and deadline > now

    def _prune_reject_cooldowns(self) -> int:
        """Pop expired cooldown entries (4.2).

        Cost is O(expired), not O(all peers): the map is keyed by expiry, and
        the heap orders entries by expiry so the bound is known in O(1). The
        heap and map are updated atomically in ``_record_rejection``, so the
        heap is the complete authority on what is live; there is deliberately
        NO fallback full-map scan (P2-4) — it could never find an entry the
        heap missed, it just paid an O(all peers) pass every tick. Stale heap
        entries (a deadline rewritten externally, e.g. a test fast-forwarding
        the clock) are discarded lazily on pop and never cost a scan. Runs on
        the cleanup task, not the drain task; the drain task's per-deny cost
        stays a dict lookup plus one heap push.
        """
        now = time.monotonic()
        count = 0
        while self._reject_heap and self._reject_heap[0][0] <= now:
            deadline, source_hash = heapq.heappop(self._reject_heap)
            if self._reject_deadlines.get(source_hash) == deadline:
                del self._reject_deadlines[source_hash]
                count += 1
            else:
                # A stale heap entry for a rewritten deadline: the live deadline
                # in the map belongs to a newer heap entry, so this pop is an
                # overcount, not a drop. (Kept under DEBUG: silent in prod.)
                logger.debug("Reticulum: dropped stale cooldown heap entry for %s", source_hash)
        return count

    def _record_rejection(self, source_hash: str) -> None:
        """Arm the cooldown window for ``source_hash`` (4.2)."""
        deadline = time.monotonic() + self._reject_cooldown_s
        self._reject_deadlines[source_hash] = deadline
        heapq.heappush(self._reject_heap, (deadline, source_hash))

    async def _prune_reject_cooldowns_loop(self) -> None:
        """Dedicated cleanup task (4.2): prunes expired cooldown entries on
        its own schedule, independent of inbound traffic and the drain task.

        The wait is a stop-event race, not a bare sleep: disconnect() sets the
        event and the task returns on its own, so teardown never has to
        interrupt a timer that a test patched."""
        while not self._cleanup_stop.is_set():
            try:
                await asyncio.wait_for(self._cleanup_stop.wait(),
                                       timeout=self._reject_cooldown_period())
            except TimeoutError:
                pass
            self._prune_reject_cooldowns()

    async def _send_rejection(self, source_hash: str) -> None:
        """Enqueue the canned rejection (4.3) — never blocks the drain task.

        The blocking ``send_to`` runs on a DEDICATED single-thread executor
        (Part C, P1-2/P1-3), not the shared default pool replies use, so a flood
        of denied peers cannot starve an allowed reply. The queue is bounded
        (``REJECT_QUEUE_MAX``): on overflow the rejection is dropped with a
        warning. That is the deliberate degradation — the cooldown was already
        armed, so the peer stays silent for the whole window either way, and a
        reply must never queue behind the rejection backlog.
        """
        self._record_rejection(source_hash)
        queue = self._reject_queue
        if queue is None:
            # Never connected (or already torn down): nothing to schedule on.
            return
        try:
            queue.put_nowait(source_hash)
        except asyncio.QueueFull:
            logger.warning(
                "Reticulum: rejection queue full (%d) dropping rejection to %s",
                REJECT_QUEUE_MAX, source_hash,
            )

    async def _reject_sender_loop(self) -> None:
        """Single worker draining the bounded rejection queue (Part C).

        One hash in, one blocking ``send_to`` onto the dedicated executor. The
        send is counted in ``_sends_in_flight`` (via ``_send_lock``) and held in
        ``_reject_in_flight`` until it finishes — so a rejection send is visible
        to ``disconnect()``'s idle wait instead of being fire-and-forget. A
        dedicated one-worker executor means P1-2's unbounded backlog is gone:
        the queue caps what can wait, and there is only ever one thread to run
        on anyway.
        """
        loop = asyncio.get_running_loop()
        queue = self._reject_queue
        if queue is None:  # pragma: no cover - worker only exists after connect
            return
        while not self._reject_stop.is_set():
            source_hash = await queue.get()
            transport = self._transport
            if transport is None:
                # Transport torn down while this was queued; drop the send.
                continue
            try:
                fut = loop.run_in_executor(
                    self._reject_executor, transport.send_to,
                    source_hash, self.REJECTION_TEXT,
                )
                with self._send_lock:
                    self._sends_in_flight += 1
                self._reject_in_flight.add(fut)
                fut.add_done_callback(self._reject_send_done)
                await fut
            except asyncio.CancelledError:
                # disconnect(): the already-submitted send keeps running on its
                # thread; the done-callback still decrements the count. Re-raise
                # so the worker task ends (no new pulls).
                raise

    def _reject_send_done(self, fut: asyncio.Future) -> None:
        """Rejection send finished (loop thread). Releases its accounting."""
        with contextlib.suppress(asyncio.CancelledError, Exception):
            fut.result()
        self._reject_in_flight.discard(fut)
        with self._send_lock:
            self._sends_in_flight -= 1

    # ── Typing ───────────────────────────────────────────────────────────

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        """LXMF has no typing indicator (spec section 2)."""
