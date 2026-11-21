"""
Reticulum Platform Adapter for the Hermes Gateway.

Implements the gateway ``BasePlatformAdapter`` contract so the mesh becomes
a first-class platform: inbound LXMF messages are bridged into the asyncio
event loop and replies go back out over the mesh.

Design: ``docs/specs/adapter/spec.md``; build cards and acceptance evidence:
``docs/specs/adapter/tasks.md`` and ``verification.md``.

- The transport sits behind the ``Transport`` protocol. The real LXMF
  implementation is ``ReticulumTransport`` (``plugin/transport.py``); the
  minimal ``FakeTransport`` in this module is used by the test suite to
  exercise the wiring without a live mesh.
- ``connect()`` is idempotent, re-initialises from scratch when
  ``is_reconnect=True``, and returns True when the local transport is up.
- Outbound send accounting lives in ``plugin/delivery.py``: chunking, the
  propagation pending set, and the DELIVERED/FAILED/SENT receipt mapping
  (``delivery.map_receipt``) consumed by the outbound callbacks below.
- Access control is ``core.acl.AccessControl``, seeded from the Hermes
  profile env by ``plugin/registration.py``.
"""

import asyncio
import contextlib
import contextvars
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

# A12 (docs/specs/adapter/tasks.md): evidence for the D7 deny discriminator.
# ``deliver_inbound`` installs a fresh attempts list into this ContextVar
# around the ``handle_message`` await; ``send()`` records the attempt at
# entry while a marker is set. Tasks created BEFORE the marker hold their
# own context and never see it, so an unrelated turn already in flight
# (a cron delivery, the rejection worker) cannot tick this event's list.
# Sends made from inside the event's own pipeline call — including tasks
# that call spawns — DO tick it, by design: that is exactly the inline
# ack the discriminator must see. The read additionally matches the
# recorded destinations against the event's own peer, so a send routed
# elsewhere is no evidence this event was handled.
_SEND_ATTEMPTS: contextvars.ContextVar["list[str] | None"] = contextvars.ContextVar(
    "hermes_reticulum_send_attempts", default=None
)

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
        interface can carry the packet (maps to a transient failure).

        The renderer declaration is inferred from the payload by the transport
        itself (see ReticulumTransport._stamp_renderer): an unsplit reply
        declares RENDERER_MARKDOWN, a part carrying a ``[pN i/N]`` label
        declares RENDERER_PLAIN. No renderer parameter here — the adapter's
        send loop hands over chunks verbatim and must not have to re-derive
        which ones are parts.
        """
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

    # Cap on supersede marks. A mark names one payload of one chat; FIFO
    # eviction bounds the map when a callback never arrives.
    SUPERSEDED_KEEP_MAX = 512

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
        # The pending entry recorded for each payload, when the adapter knows
        # it. Receipts carry the payload, not the entry, so this is what lets
        # a receipt resolve by IDENTITY: two peers sent the same text, the
        # older entry is not the one a receipt resolves. Keyed on text, so a
        # SECOND send of the same text to the SAME chat overwrites the mapping
        # and leaves the older entry with no identity path — the "full-content
        # payload keys" card covers that case.
        self._pending_by_payload: dict[str, object] = {}
        # Payloads of sends that were superseded by a different send (a
        # link-mode whole body re-sent as chunk parts), mapped to the chat the
        # replaced attempt was addressed to. Their late delivery/failure
        # callbacks must not count against the replacement's record, and a mark
        # for one chat must never swallow another chat's receipt for the same
        # text (the handler compares the chat). FIFO-capped so a callback that
        # never arrives cannot leak.
        self._superseded_payloads: dict[str, str] = {}
        # Fallback only, for a payload the map does not know (e.g. a transport
        # that reports a transformed payload). New sends use the map.
        self._last_outbound_chat: str | None = None
        # In-flight sends. disconnect() waits for these so teardown never stops
        # a transport out from under a send that is still probing its path.
        self._send_lock = threading.Lock()
        self._sends_in_flight = 0

        # Per-conversation tag for the ``[p<N> i/N]`` chunk prefixes. Monotonic
        # per destination hash so a recipient can spot a dropped tail, and the
        # numbering stays meaningful within a chat instead of climbing across
        # every chat on the adapter. Kept across reconnects alongside the
        # pending set, for the same reason.
        self._chunk_tags: dict[str, int] = {}

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
        # A /new starts a fresh conversation; restart this chat's chunk
        # numbering so p stays meaningful within a session instead of
        # climbing across every /new on the same chat.
        if self._is_new_session_command(text):
            self._chunk_tags.pop(source_hash.lower(), None)
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
        """Hand one inbound event to the gateway's inbound pipeline.

        Delegates to the inherited ``handle_message`` whenever it exists: that
        is the gateway path that both RUNS the installed ``_message_handler``
        and DELIVERS its returned response (``_process_message_background`` ->
        ``_send_final_text``). This hop must NOT call the raw ``_message_handler``
        itself — that handler only computes the turn and returns the text; it
        never sends. Calling it directly therefore runs the agent (the reply
        lands in ``state.db`` and logs as "response ready") but silently drops
        the reply, so the mesh shows only status pings (which travel through a
        separate ``send()`` path) while every LLM answer goes missing. That is
        exactly the failure this method must not produce.

        The raw-handler fallback below keeps compatibility with a gateway
        version that renames/ removes ``handle_message``: the turn still runs,
        and a falsy-but-not-None return is the gateway-side deny described under
        A3 4.5 (D7). On the normal path the deny is read from the
        ``_gateway_accepted`` flag the gateway sets on the event (False when its
        own authz/ingress gate dropped the event); a desync there is logged and
        answered through the transport seam under the same D4/D6 rules.

        A12 refines that read: ``_gateway_accepted is False`` is a deny signal
        ONLY when the gateway attempted no send to THIS event's peer during
        the ``handle_message`` call. The base's busy-session inline dispatch
        (``/stop``, ``/new``, clarify answers) handles the event inline, sends
        its own ack through this adapter's ``send()``, and returns without ever
        setting the flag — reading that False as a deny produced the false
        ``⛔ Access not authorized.`` after every inline command (findings
        2026-11-01). A genuine gate drop attempts no send to the peer, so the
        two cases are separated by a per-call send-attempt marker: a fresh
        list goes into ``_SEND_ATTEMPTS`` around the call and ``send()`` ticks
        it at entry, recording the destination; the deny read requires the
        event's own peer to be ABSENT from it (a send routed to another peer
        is not evidence this event was handled). The marker records the
        ATTEMPT, never its result, so an ack whose send failed or was still in
        flight at return still counts as evidence the gateway handled the
        event. Known limit (card A14): an inline path that handles the event
        yet sends nothing — the handler yields no text, or the base swallows
        an inline-dispatch exception — leaves no evidence either and still
        reads as a deny until the upstream flag fix lands.
        """
        source_hash = getattr(getattr(event, "source", None), "user_id", None)
        handle = getattr(self, "handle_message", None)
        if callable(handle):
            send_attempts: list[str] = []
            token = _SEND_ATTEMPTS.set(send_attempts)
            try:
                result = handle(event)
                if inspect.isawaitable(result):
                    result = await result
            finally:
                _SEND_ATTEMPTS.reset(token)
            if (
                source_hash
                and source_hash not in send_attempts
                and getattr(event, "_gateway_accepted", True) is False
            ):
                await self._note_gateway_desync(source_hash)
            return
        result = None
        if self._message_handler is not None:
            result = self._message_handler(event)
            if inspect.isawaitable(result):
                result = await result
        if source_hash and result is not None and not result:
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
        # A12: record the attempt while a ``deliver_inbound`` marker is set —
        # at entry, before any validation, because the deny discriminator
        # reads the attempt itself, not its outcome (see deliver_inbound).
        attempts = _SEND_ATTEMPTS.get()
        if attempts is not None:
            attempts.append(chat_id)
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
        #
        # Link mode (default on, HERMES_RNS_LINK_MODE=off to disable):
        # over-budget replies skip chunking and travel as one
        # link-delivered message; LXMF sequences it as an RNS resource — over
        # a link when a path exists, via the propagation node otherwise — and
        # the peer's router reassembles it whole, delivering a single
        # RENDERER_MARKDOWN body (docs/mesh-bridge-findings-2026-11-19). The
        # size+knob decision is made ONCE, here; the transport only picks the
        # method (path-aware DIRECT vs PROPAGATED). If that single send fails
        # outright (no path after the probe, interface gone), we re-chunk and
        # send the labeled parts instead — the bot does not go dark over a
        # delivery preference.
        link_single = delivery.needs_link_delivery(content)
        if link_single:
            parts = [content]
        else:
            parts = self._chunk_content(content, chat_id)
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
        #
        # Loop-affine: this map, ``_pending_by_payload`` and
        # ``_superseded_payloads`` are written from send() and from the drain
        # task's handlers, both on the event loop. The transport's own
        # callbacks hop threads through call_soon_threadsafe before reaching
        # them (connect(), spec section 5), so no lock is needed here — and
        # adding one around a send would deadlock the loop it protects.
        self._last_outbound_chat = chat_id
        for part in parts:
            self._outbound_by_payload[part] = chat_id
            # A fresh send of a payload we once superseded: this attempt IS
            # the record now — a stale supersede mark must not swallow the
            # new delivery's receipts. Scoped to THIS chat: another peer's
            # mark for the same text is not ours to clear.
            if self._superseded_payloads.get(part) == chat_id:
                self._superseded_payloads.pop(part, None)

        # Record the pending entry BEFORE sending, so a delivery callback that
        # fires while send_to() is still in flight (a direct send confirms fast)
        # finds the entry and clears it. Recording it after the await left an
        # already-delivered reply marked unconfirmed forever.
        entry = self._pending.record_propagated(chat_id, content)
        if parts[0] == content:
            # Link mode sends the full content as its own payload; receipts for
            # it name this entry and resolve it by identity.
            self._pending_by_payload[content] = entry
        # Track this send so disconnect() can wait for it instead of tearing the
        # transport down underneath it.
        transport = self._transport
        with self._send_lock:
            self._sends_in_flight += 1
        cancel_requested = False
        fallback_entries: dict[int, object] = {}
        try:
            index = 0
            fell_back = False
            while index < len(parts):
                part = parts[index]
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
                    if link_single and not fell_back:
                        if cancel_requested:
                            # A new /new cancelled this send: the fallback
                            # must not start a fresh chunked crawl after it.
                            # Nothing left on the failed attempt — retire the
                            # body's record and surface the cancellation.
                            self._retire_superseded_send(entry, content, chat_id)
                            entry = None
                            raise asyncio.CancelledError()
                        # The one-body send could not leave at all (no path
                        # after the probe, interface gone): fall back to the
                        # labeled chunk parts rather than failing the reply.
                        # send_to returning False can also follow a resource
                        # transfer that failed mid-way, so the rare partial
                        # can duplicate: a visible overlap beats silence.
                        fallback = self._chunk_content(content, chat_id)
                        if fallback:
                            logger.warning(
                                "Reticulum: link-mode send failed for %s; "
                                "retrying as %d chunked parts",
                                chat_id[:8],
                                len(fallback),
                            )
                            # Retire the whole-body record honestly (whatever
                            # fate its callbacks reported), then let the
                            # chunks' own receipts do the counting from here.
                            self._retire_superseded_send(entry, content, chat_id)
                            entry = None
                            for chunk in fallback:
                                self._outbound_by_payload[chunk] = chat_id
                            # One pending entry per part actually attempted —
                            # the record holds exactly the payloads on the wire.
                            fallback_entries = {}
                            for i, chunk in enumerate(fallback):
                                fe = self._pending.record_propagated(chat_id, chunk)
                                fallback_entries[i] = fe
                                self._pending_by_payload[chunk] = fe
                            parts = fallback
                            fell_back = True
                            index = 0
                            continue
                    # No path / no interface: transient, let the base-class retry
                    # logic and the ledger sweep behave (spec section 6). With
                    # propagation configured, a pathless send still leaves the
                    # message on the node, so reaching here means it did not leave
                    # at all -- abort the rest rather than spray the mesh.
                    target = fallback_entries.get(index, entry)
                    if target is not None:
                        self._pending.note_failed_entry(target)
                    entry = None
                    # Parts after this one will never be attempted: retire
                    # their records too, so nothing unattempted waits forever.
                    for j in range(index + 1, len(parts)):
                        self._pending.abandon_entry(fallback_entries.get(j))
                    # No receipt will ever key these payloads again (the run
                    # aborted): pop them now instead of for the process life.
                    for part in parts[index:]:
                        self._outbound_by_payload.pop(part, None)
                        self._pending_by_payload.pop(part, None)
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
                index += 1
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
        # What the record holds depends on the path taken: a send that fit one
        # part, or went whole in link mode, is one entry under its own payload;
        # a fallback holds one entry per chunk actually attempted, each mapped
        # in _pending_by_payload. Nothing here invents an entry for a part that
        # never went out.
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

    def _chunk_content(self, content: str, destination_hash: str) -> list | None:
        """Split outbound content for transmission.

        Returns the parts (a single-element list when the content fits), an
        empty list when chunking degenerates, or ``None`` when the content
        cannot be carried at all. The tag is per-conversation monotonic,
        keyed by destination hash, so a recipient can spot a dropped tail
        without any protocol change and the numbering starts at p1 for every
        chat instead of climbing across the whole adapter.
        """
        # LXMF destination hashes are case-insensitive hex; normalize so the
        # same peer never gets two counters (and two p1 labels).
        destination_hash = destination_hash.lower()
        n = self._chunk_tags.get(destination_hash, 0) + 1
        self._chunk_tags[destination_hash] = n
        tag = f"p{n}"
        return delivery.chunk_for_send(content, tag)

    @staticmethod
    def _is_new_session_command(text: str | None) -> bool:
        """True when ``text`` is the gateway's /new command (with or without args).

        Mirrors the gateway's command extraction (``event.get_command``:
        first token after ``/``, lowercased, ``@``-suffix stripped) so the
        adapter resets the chunk counter on exactly the command the gateway
        treats as a new session.
        """
        if not text:
            return False
        stripped = text.lstrip()
        if not stripped.startswith("/"):
            return False
        name = stripped.split(maxsplit=1)[0][1:].lower().split("@", 1)[0]
        return name == "new"

    @staticmethod
    def _is_valid_hash(destination_hash: str) -> bool:
        """A destination is 32 hex characters (LXMF hash). Kept for compat."""
        return is_valid_destination(destination_hash)

    def _retire_superseded_send(self, entry, content: str, chat_id: str) -> None:
        """Take a superseded send's record out of the books, counter-neutral.

        Called when a link-mode body is replaced by the chunked retry, or the
        send is cancelled. The replacement owns the record from here, so the
        body's counters must end exactly where they started.

        The counters are NOT inferred. The pending set remembers the fate of
        every entry a receipt resolved: a body that delivered retracts
        ``delivered``, one that failed retracts ``failed``, one still pending
        retracts only ``propagated``. An entry with no known fate leaves the
        counters alone — one count too high is a far smaller lie than a ledger
        reading ``failed: -1``.

        In practice the FAILED event usually lands first: LXMF fires it the
        moment it sets the terminal state, the adapter re-queues it with
        ``call_soon_threadsafe``, and the send coroutine only resumes once its
        poll notices that state. If the event is still queued when we get here,
        the mark below drops it instead. Either way the record nets zero.

        The mark is ``payload -> chat`` and is NOT popped on use: LXMF can
        report failed and later delivered for one message, and BOTH belong to
        the replaced attempt. Keying the chat (rather than dropping the mark on
        its first callback) is what stops a second callback from being counted
        against whichever chat spoke last. Another peer sending the same text
        keeps its own record because the handler compares the chat. FIFO
        eviction bounds the map.
        """
        self._pending.retract_entry(entry)
        if self._pending_by_payload.get(content) is entry:
            self._pending_by_payload.pop(content, None)
        if self._outbound_by_payload.get(content) == chat_id:
            # The receipt's chat now comes from the mark. Popping the routing
            # key keeps the "no payload key for the process life" property; a
            # newer send of this text to another peer owns the key by then.
            self._outbound_by_payload.pop(content, None)
        self._superseded_payloads[content] = chat_id
        while len(self._superseded_payloads) > self.SUPERSEDED_KEEP_MAX:
            self._superseded_payloads.pop(next(iter(self._superseded_payloads)))

    def _is_superseded(self, chat_id: str | None, payload: str) -> bool:
        """True when this payload belongs to a send that was replaced.

        The mark names the chat the replaced attempt went to, so a receipt is
        dropped only when it is that attempt's: the same text sent to another
        peer is a different send with a record of its own.
        """
        return (
            chat_id is not None
            and self._superseded_payloads.get(payload) == chat_id
        )

    def _attribute_payload(self, payload: str) -> tuple[str, bool]:
        """Best attribution of a receipt to a chat: ``(chat_id, confident)``.

        The routing map is authoritative while it holds the payload. It can be
        gone (a superseded send removes its own key) or overwritten (another
        peer sent the same text later), and the receipt itself carries only the
        text.

        A surviving supersede mark outranks the pending set. The mark names the
        chat whose attempt was replaced, and the retire path leaves it behind
        precisely so late callbacks for that payload can be recognised. Guessing
        a chat from a pending entry that merely shares the text would let a
        dead attempt's receipt land on a live peer's record.

        Only with no routing key and no mark does the pending set speak: an
        entry with exactly this content and one destination names the chat.
        Two destinations means the text is genuinely ambiguous, and the caller
        warns instead of inventing an answer.
        """
        chat_id = self._outbound_by_payload.get(payload)
        if chat_id is not None:
            return chat_id, True
        marked = self._superseded_payloads.get(payload)
        if marked is not None:
            # Known owner of a replaced send; the handlers drop its receipts.
            return marked, True
        destinations = {
            e.destination for e in self._pending.all_pending() if e.content == payload
        }
        if len(destinations) == 1:
            return destinations.pop(), True
        return "", False

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
            chat_id, confident = self._attribute_payload(payload)
            if not confident:
                # Fall back to the last chat only when the payload cannot be
                # attributed at all (a transport that reports transformed
                # text). The mark is checked against whichever chat we land
                # on, so a replaced send's callback is still dropped.
                chat_id = self._last_outbound_chat or ""
            if self._is_superseded(chat_id, payload):
                # This send was replaced (link-mode body retried as chunks);
                # its late receipt must not count against the retry's record.
                # The mark stays: a second callback for the same message is
                # still the replaced attempt's.
                logger.info(
                    "Reticulum: receipt for a superseded send dropped; the "
                    "chunked retry owns the record"
                )
                return
            if not confident:
                logger.warning(
                    "Reticulum: outcome for unknown payload; attributing to the "
                    "last send (%s); receipt may be misattributed",
                    chat_id or "none",
                )
            result = self._record_receipt(chat_id, payload, state_int, None)
            if chat_id:
                self._outbound_by_payload.pop(payload, None)
            if result.success:
                logger.info(
                    "Reticulum: outbound message reached %s (%s)",
                    delivery.state_name(state_int),
                    "delivered" if state_int == delivery.STATE_DELIVERED else "sent",
                )
            else:
                # A FAILED/REJECTED/CANCELLED terminal state IS the lost
                # delivery the peer never got — card A4's send truth: this
                # surface used to log INFO like a success receipt. An
                # unrecognised state is NOT known loss: LXMF's delivery
                # callback also fires for PAPER (a success), so unknown
                # states get an INFO naming them, not a "lost delivery" cry.
                if state_int in (
                    delivery.STATE_FAILED,
                    delivery.STATE_REJECTED,
                    delivery.STATE_CANCELLED,
                ):
                    logger.warning(
                        "Reticulum: lost delivery to %s: outbound message "
                        "reached %s: %.80r",
                        chat_id or "unknown peer",
                        delivery.state_name(state_int),
                        payload,
                    )
                else:
                    logger.info(
                        "Reticulum: outbound receipt in unrecognised state "
                        "%s for %s: %.80r",
                        delivery.state_name(state_int),
                        chat_id or "unknown peer",
                        payload,
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
            chat_id, confident = self._attribute_payload(payload)
            attributed = ""
            if not confident:
                chat_id = self._last_outbound_chat or ""
                attributed = " (attributed to last send)"
            if self._is_superseded(chat_id, payload):
                # Replaced send (see the outcome handler): the retry owns the
                # record; a late failure of the superseded attempt is noise.
                # The mark stays for the same reason it does there.
                return
            self._record_receipt(chat_id, payload, delivery.STATE_FAILED, reason)
            if chat_id:
                self._outbound_by_payload.pop(payload, None)
            # Card A4 send truth: a lost send is a WARNING naming the lost
            # delivery, not the INFO it logged at before. When the payload
            # could not be attributed, say so — the name is a guess.
            logger.warning(
                "Reticulum: lost delivery to %s%s: outbound message failed "
                "(%s): %.80r",
                chat_id or "unknown peer",
                attributed,
                reason,
                payload,
            )
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

        When the payload is one this adapter recorded, its entry goes along so
        the receipt resolves by identity. Two peers sent the same text, or one
        peer sent it twice, must never resolve each other's record.
        """
        entry = self._pending_by_payload.get(content)
        result = delivery.map_receipt(
            self._pending, state, chat_id, content, reason, entry=entry
        )
        if entry is not None and self._pending.was_resolved(entry):
            if self._pending_by_payload.get(content) is entry:
                self._pending_by_payload.pop(content, None)
        return result

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
                inner = loop.run_in_executor(
                    self._reject_executor, transport.send_to,
                    source_hash, self.REJECTION_TEXT,
                )
                with self._send_lock:
                    self._sends_in_flight += 1
                self._reject_in_flight.add(inner)
                inner.add_done_callback(self._reject_send_done)
                # Shield keeps the future alive under cancellation: disconnect()
                # cancels this worker task mid-send, and without the shield the
                # cancel ALSO cancels the run_in_executor future, so the
                # done-callback zeroes _sends_in_flight before the worker
                # thread finishes and shutdown(wait=True) would block the whole
                # event loop until send_to() returns (Part C, P1 shutdown pause).
                # Shielded, the callback fires only on real completion, so the
                # idle wait below holds until the thread is done.
                await asyncio.shield(inner)
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
