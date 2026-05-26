"""Compatibility alias for :mod:`codex_cadence.candidates`."""

from importlib import import_module
import sys

sys.modules[__name__] = import_module("codex_cadence.candidates")
