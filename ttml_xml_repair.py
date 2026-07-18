# -*- coding: utf-8 -*-
"""Pure TTML XML pre-parse repairs (no Flask / index side effects)."""

from __future__ import annotations

import logging
import re
from typing import Callable, Optional

# AMLL submit-issue title junk sometimes lands inside a broken numeric/hex entity,
# e.g. There&#39 [歌词提交/修正] …;s  →  There&#39;s
_TTML_ISSUE_JUNK_IN_ENTITY_RE = re.compile(
    r'&#(x?[0-9a-fA-F]+)\s*\[歌词提交/[^\]]*\][^&<]*?;',
    re.IGNORECASE,
)
# Incomplete numeric / hex character references missing the trailing ';'.
_TTML_INCOMPLETE_DEC_ENTITY_RE = re.compile(r'&#(\d{1,7})(?!\d)(?!;)')
_TTML_INCOMPLETE_HEX_ENTITY_RE = re.compile(
    r'&#x([0-9a-fA-F]{1,6})(?![0-9a-fA-F])(?!;)',
    re.IGNORECASE,
)
# Only XML's five predefined entities are valid without a DTD.
_TTML_INVALID_AMP_RE = re.compile(
    r'&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)',
)
# CDATA, comments, and processing instructions must not be modified.
_TTML_LITERAL_BLOCK_RE = re.compile(
    r'<!\[CDATA\[.*?\]\]>|<!--.*?-->|<\?.*?\?>',
    re.DOTALL,
)

_LOG = logging.getLogger(__name__)


def _repair_repairable_segment(segment: str) -> str:
    repaired = _TTML_ISSUE_JUNK_IN_ENTITY_RE.sub(r'&#\1;', segment)
    repaired = _TTML_INCOMPLETE_DEC_ENTITY_RE.sub(r'&#\1;', repaired)
    repaired = _TTML_INCOMPLETE_HEX_ENTITY_RE.sub(r'&#x\1;', repaired)
    repaired = _TTML_INVALID_AMP_RE.sub('&amp;', repaired)
    return repaired


def repair_ttml_xml_text(
    ttml_text: str,
    *,
    warn: Optional[Callable[[str], None]] = None,
) -> str:
    """
    Pre-parse repair for common TTML XML malformations so minidom can parse.

    1. Strip AMLL "[歌词提交/…]" junk injected between &#… and its closing ';'.
    2. Close incomplete decimal/hex character references (e.g. &#39s → &#39;s).
    3. Escape remaining bare '&' outside CDATA/comments/PI (safety only).
    """
    if not ttml_text:
        return ttml_text
    original = ttml_text
    parts: list[str] = []
    last = 0
    for match in _TTML_LITERAL_BLOCK_RE.finditer(ttml_text):
        if match.start() > last:
            parts.append(_repair_repairable_segment(ttml_text[last:match.start()]))
        parts.append(match.group(0))
        last = match.end()
    if last < len(ttml_text):
        parts.append(_repair_repairable_segment(ttml_text[last:]))
    repaired = ''.join(parts)
    if repaired == original:
        return repaired
    log = warn or _LOG.warning
    if repaired != original:
        if _TTML_ISSUE_JUNK_IN_ENTITY_RE.search(original) or (
            _TTML_INCOMPLETE_DEC_ENTITY_RE.search(original)
            or _TTML_INCOMPLETE_HEX_ENTITY_RE.search(original)
        ):
            log("TTML XML auto-repair: semantic entity recovery applied")
        if _TTML_INVALID_AMP_RE.search(original):
            log("TTML XML auto-repair: bare ampersand safety escape applied")
    return repaired
