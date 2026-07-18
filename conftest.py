"""Pytest hooks shared by the repository test suite."""

from __future__ import annotations

import os

# Prevent backend import side effects (index rebuild threads) during collection.
os.environ.setdefault("FAMYLIAM_SKIP_INDEX_INIT", "1")
