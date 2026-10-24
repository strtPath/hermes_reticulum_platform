"""
Access Control — filters incoming messages by sender identity hash.

Supports:
  - Allowlist mode: only listed hashes can interact
  - Open mode: everyone can interact
  - Blocklist mode: listed hashes are rejected
"""

import logging
import os
from collections.abc import Callable

logger = logging.getLogger("hermes_reticulum.acl")

# RNS truncated hash: 16 bytes = 32 hex chars.
RNS_TRUNCATED_HASH_HEX_LEN = 32


class AccessControl:
    """
    Controls which LXMF senders can interact with the Hermes agent.

    Configuration via environment variables:
      - HERMES_RETICULUM_ALLOW_ALL=true      → open mode (opt-in; NOT the default)
      - HERMES_RETICULUM_ALLOWED_USERS=hex1,hex2 → allowlist (default mode)
      - HERMES_RETICULUM_BLOCKED_USERS=hex1,hex2 → blocklist
    """

    # The one set of truthy values for HERMES_RETICULUM_ALLOW_ALL. Every reader
    # goes through parse_allow_all: the env load here and the adapter's
    # injected profile-scoped reader must agree, so selecting a profile cannot
    # flip the meaning of the setting (A3 4.1).
    ALLOW_ALL_TRUTHY = ("true", "1", "yes", "on")

    def __init__(self, get_env: Callable[[str, str], str] | None = None):
        # The config reader is injected, so this core module never imports the
        # gateway. The adapter passes the gateway's profile-scoped
        # ``platform_gate_env`` (multiplex-safe, spec.md:99-101); the default is
        # plain ``os.getenv`` for standalone use and tests. Both share the same
        # (name, default) call shape, so a reader can be swapped without the
        # parse logic changing.
        self._get = get_env if get_env is not None else os.getenv
        self._load_config()

    @classmethod
    def parse_allow_all(cls, raw: str | None) -> bool:
        """Parse the allow-all setting; empty or unknown values stay closed."""
        return str(raw or "").strip().lower() in cls.ALLOW_ALL_TRUTHY

    def _load_config(self):
        """Load ACL config from the injected reader (default: env vars)."""
        # Allow-all mode — FAIL CLOSED. Default is deny-by-default (allowlist
        # mode), matching README/env.example. Open mode must be opted into
        # explicitly with HERMES_RETICULUM_ALLOW_ALL=true.
        self.allow_all = self.parse_allow_all(
            self._get("HERMES_RETICULUM_ALLOW_ALL", "false")
        )

        # Allowlist
        allowed_raw = self._get("HERMES_RETICULUM_ALLOWED_USERS", "")
        self.allowed_users: set[str] = self._parse_hash_set(allowed_raw)

        # Blocklist
        blocked_raw = self._get("HERMES_RETICULUM_BLOCKED_USERS", "")
        self.blocked_users: set[str] = self._parse_hash_set(blocked_raw)

        logger.info(
            "ACL loaded: allow_all=%s, allowed=%d, blocked=%d",
            self.allow_all,
            len(self.allowed_users),
            len(self.blocked_users),
        )

    @staticmethod
    def _normalize_hash(h: str) -> str:
        """Lowercase and strip colons/spaces from a hex hash."""
        return h.strip().lower().replace(" ", "").replace(":", "")

    @classmethod
    def _parse_hash_set(cls, raw: str) -> set[str]:
        """Parse comma-separated hex hashes into a normalized set."""
        if not raw.strip():
            return set()
        hashes = set()
        for h in raw.split(","):
            h = cls._normalize_hash(h)
            if h and len(h) == RNS_TRUNCATED_HASH_HEX_LEN:
                hashes.add(h)
            elif h:
                logger.warning(
                    "Ignoring invalid hash in ACL: %s (expected 32 hex chars)", h
                )
        return hashes

    def is_allowed(self, sender_hash: str) -> bool:
        """
        Check if a sender is allowed to interact with the agent.

        Args:
            sender_hash: Hex string of the sender's LXMF hash (with or without colons).

        Returns:
            True if the sender is permitted.
        """
        # Normalize
        normalized = self._normalize_hash(sender_hash)

        # Blocklist takes priority
        if normalized in self.blocked_users:
            logger.info("Blocked sender: %s", sender_hash)
            return False

        # Allow-all mode
        if self.allow_all:
            return True

        # Allowlist mode
        if normalized in self.allowed_users:
            return True

        logger.info("Sender not in allowlist: %s", sender_hash)
        return False

    @property
    def mode(self) -> str:
        """Human-readable ACL mode."""
        if self.allow_all:
            return "open"
        return "allowlist" if self.allowed_users else "closed"

    def __repr__(self) -> str:
        return (
            f"AccessControl(mode={self.mode}, "
            f"allowed={len(self.allowed_users)}, "
            f"blocked={len(self.blocked_users)})"
        )
