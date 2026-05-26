"""Compatibility namespace for pre-Cadence imports.

New integrations should import from :mod:`codex_cadence`.
"""

from codex_cadence import PROTOCOL_VERSION

__all__ = ["PROTOCOL_VERSION"]
