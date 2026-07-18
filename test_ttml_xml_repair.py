#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Regression tests for pure TTML XML repair (no backend import)."""

from __future__ import annotations

import xml.dom.minidom

from ttml_xml_repair import repair_ttml_xml_text


def _parse_text(repaired: str) -> str:
    dom = xml.dom.minidom.parseString(repaired)
    spans = dom.getElementsByTagName("span")
    assert spans, "expected a span"
    return "".join(
        (c.nodeValue or "")
        for c in spans[0].childNodes
        if c.nodeType == c.TEXT_NODE
    )


def _wrap_span(inner: str) -> str:
    return (
        '<tt xmlns="http://www.w3.org/ns/ttml">'
        "<body><div><p>"
        f"<span>{inner}</span>"
        "</p></div></body></tt>"
    )


def test_issue_junk_apostrophe_recovery():
    bad = "There&#39 [歌词提交/修正] 白从一 / 慕容韶 - 果汁分你一半;s"
    repaired = repair_ttml_xml_text(_wrap_span(bad))
    assert "[歌词提交" not in repaired
    assert _parse_text(repaired) == "There's"


def test_incomplete_decimal_entity():
    repaired = repair_ttml_xml_text(_wrap_span("There&#39s"))
    assert _parse_text(repaired) == "There's"


def test_incomplete_hex_entity():
    repaired = repair_ttml_xml_text(_wrap_span("There&#x27s"))
    assert _parse_text(repaired) == "There's"


def test_bare_ampersand_safety_escape():
    repaired = repair_ttml_xml_text(_wrap_span("A & B"))
    assert "A &amp; B" in repaired
    assert _parse_text(repaired) == "A & B"


def test_well_formed_entities_unchanged():
    original = _wrap_span("A &amp; B &lt; C &gt; D")
    repaired = repair_ttml_xml_text(original)
    assert repaired == original
    assert _parse_text(repaired) == "A & B < C > D"


def test_normal_ttml_unchanged():
    original = _wrap_span("hello world")
    assert repair_ttml_xml_text(original) == original


def test_cdata_ampersand_preserved():
    original = (
        '<tt xmlns="http://www.w3.org/ns/ttml">'
        "<body><div><p>"
        "<span><![CDATA[A & B]]></span>"
        "</p></div></body></tt>"
    )
    repaired = repair_ttml_xml_text(original)
    assert "<![CDATA[A & B]]>" in repaired
    dom = xml.dom.minidom.parseString(repaired)
    span = dom.getElementsByTagName("span")[0]
    assert span.firstChild.nodeValue == "A & B"


def test_named_entity_nbsp_escaped_for_well_formedness():
    repaired = repair_ttml_xml_text(_wrap_span("A&nbsp;B"))
    assert "A&amp;nbsp;B" in repaired
    xml.dom.minidom.parseString(repaired)
    assert _parse_text(repaired) == "A&nbsp;B"


def test_repair_idempotent():
    original = _wrap_span("A & B")
    once = repair_ttml_xml_text(original)
    twice = repair_ttml_xml_text(once)
    assert once == twice
    assert _parse_text(twice) == "A & B"