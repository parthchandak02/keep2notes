"""Turn Keep's styled HTML (or plain text) into a small, ENML-safe HTML fragment."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from xml.sax.saxutils import escape, quoteattr

URL_RE = re.compile(r"https?://[^\s<>\"']+")
_TRAILING = ".,;:!?'\""

BLOCK_TAGS = {"p", "div"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
INLINE_MAP = {"b": "b", "strong": "b", "i": "i", "em": "i", "u": "u", "s": "s", "strike": "s", "del": "s"}
LIST_TAGS = {"ul", "ol", "li"}


def _split_url(url: str) -> tuple[str, str]:
    tail = ""
    while url and (url[-1] in _TRAILING or (url[-1] == ")" and url.count("(") < url.count(")"))):
        tail = url[-1] + tail
        url = url[:-1]
    return url, tail


def text_to_enml(text: str, autolink: bool = True) -> str:
    """Escape text for ENML, preserving double spaces and optionally linking bare URLs."""
    if not autolink:
        return _escape_spaces(escape(text))
    parts = []
    pos = 0
    for m in URL_RE.finditer(text):
        url, tail = _split_url(m.group(0))
        parts.append(_escape_spaces(escape(text[pos : m.start()])))
        parts.append(f"<a href={quoteattr(url)}>{escape(url)}</a>")
        parts.append(_escape_spaces(escape(tail)))
        pos = m.start() + len(url) + len(tail)
    parts.append(_escape_spaces(escape(text[pos:])))
    return "".join(parts)


def _escape_spaces(s: str) -> str:
    return s.replace("\t", "\u00a0\u00a0\u00a0\u00a0").replace("  ", " \u00a0")


def plain_to_enml(text: str) -> str:
    lines = text.replace("\r\n", "\n").split("\n")
    return "".join(f"<div>{text_to_enml(line)}</div>" if line.strip() else "<div><br/></div>" for line in lines)


def _style_tags(style: str) -> list[str]:
    s = style.replace(" ", "").lower()
    tags = []
    m = re.search(r"font-weight:(\w+)", s)
    if m and (m.group(1) == "bold" or (m.group(1).isdigit() and int(m.group(1)) >= 600)):
        tags.append("b")
    if "font-style:italic" in s:
        tags.append("i")
    deco = re.search(r"text-decoration:([^;]+)", s)
    if deco and "underline" in deco.group(1):
        tags.append("u")
    if deco and "line-through" in deco.group(1):
        tags.append("s")
    return tags


class _Cleaner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[tuple[str, list[str]]] = []
        self.link_depth = 0

    @property
    def depth(self) -> int:
        return sum(1 for _, closers in self.stack if closers)

    def _open(self, tag: str, openers: list[str], closers: list[str]) -> None:
        self.out.extend(openers)
        self.stack.append((tag, closers))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "br":
            self.handle_startendtag(tag, list(attrs.items()))
        elif tag in BLOCK_TAGS:
            self._open(tag, ["<div>"], ["</div>"])
        elif tag in HEADING_TAGS:
            self._open(tag, ["<div>", "<b>"], ["</b>", "</div>"])
        elif tag == "span":
            tags = _style_tags(attrs.get("style") or "")
            self._open(tag, [f"<{t}>" for t in tags], [f"</{t}>" for t in reversed(tags)])
        elif tag in INLINE_MAP:
            t = INLINE_MAP[tag]
            self._open(tag, [f"<{t}>"], [f"</{t}>"])
        elif tag in LIST_TAGS:
            self._open(tag, [f"<{tag}>"], [f"</{tag}>"])
        elif tag == "a" and re.match(r"(?i)^(https?|mailto):", attrs.get("href") or ""):
            self.link_depth += 1
            self._open(tag, [f"<a href={quoteattr(attrs['href'])}>"], ["</a>"])
        else:
            self.stack.append((tag, []))

    def handle_startendtag(self, tag, attrs):
        if tag == "br":
            self.out.append("<br/>" if self.depth else "<div><br/></div>")

    def handle_endtag(self, tag):
        if not any(t == tag for t, _ in self.stack):
            return
        while self.stack:
            t, closers = self.stack.pop()
            if t == "a" and closers:
                self.link_depth -= 1
            if closers == ["</div>"] and self.out and self.out[-1] == "<div>":
                self.out.append("<br/>")
            self.out.extend(closers)
            if t == tag:
                break

    def handle_data(self, data):
        if not data:
            return
        segments = data.split("\n")
        for i, seg in enumerate(segments):
            if i:
                self.out.append("<br/>")
            if seg:
                self.out.append(text_to_enml(seg, autolink=self.link_depth == 0))

    def result(self) -> str:
        while self.stack:
            self.handle_endtag(self.stack[-1][0])
        return "".join(self.out)


def html_to_enml(fragment: str) -> str:
    cleaner = _Cleaner()
    cleaner.feed(fragment)
    cleaner.close()
    return cleaner.result()


def inline_html_to_enml(fragment: str) -> str:
    """Like html_to_enml but unwraps block tags, for single-line content such as checklist items."""
    enml = html_to_enml(fragment)
    enml = re.sub(r"</div><div>", "<br/>", enml)
    enml = re.sub(r"</?div>", "", enml)
    enml = re.sub(r"(<br/>)+$", "", enml)
    return enml


_TAG_RE = re.compile(r"<[^>]+>")


def enml_to_text(enml: str) -> str:
    """Rough plain-text rendering used to compare converted output against Keep's textContent."""
    text = re.sub(r"<br/>|</div>|</li>", "\n", enml)
    text = html.unescape(_TAG_RE.sub("", text)).replace("\u00a0", " ")
    return normalize_ws(text)


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()
