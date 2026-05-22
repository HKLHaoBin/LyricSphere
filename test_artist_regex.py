"""Tests for composite artist separator regex (Python 3.11+ safe flags)."""

import re

import pytest


def test_composite_artist_sep_re_compiles_like_backend():
    pattern = re.compile(
        r'\s*[,，、;；/|]\s*|\s*&\s*|\b(?:feat\.?|ft\.?|vs\.?)\b\s*',
        re.IGNORECASE,
    )
    assert pattern.split('A & B') == ['A', 'B']


def test_backend_import_and_expand_composite_artist_string():
    from backend import _COMPOSITE_ARTIST_SEP_RE, _expand_composite_artist_string

    assert _COMPOSITE_ARTIST_SEP_RE is not None
    assert _expand_composite_artist_string('A & B') == ['A', 'B']
    assert _expand_composite_artist_string('A feat B') == ['A', 'B']
    assert _expand_composite_artist_string('A FEAT B') == ['A', 'B']
    assert _expand_composite_artist_string('A，B') == ['A', 'B']
