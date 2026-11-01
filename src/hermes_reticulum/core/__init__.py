"""Shared core modules for Hermes for Reticulum.

The standalone bridge now lives in :mod:`hermes_reticulum.bridge`. What
remains here is the code both architectures still need: the access-control
gate the platform adapter imports, and the tool-emoji table the bridge's step
push uses.
"""

from hermes_reticulum.core.acl import AccessControl

__all__ = ["AccessControl"]
