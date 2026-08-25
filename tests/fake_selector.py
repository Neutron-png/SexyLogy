"""
Minimal stand-in for Scrapling's Selector/Adaptor, built on BeautifulSoup,
used ONLY in tests so app/core/engine/extractor.py can be verified without
installing Scrapling. It implements the exact slice of the interface
extractor.py relies on: .css(selector) / .xpath(selector) returning a
list of elements, each exposing .get() (text), .attrib (dict-like) and
.html (outer HTML) - mirroring Scrapling's real Selector API
(https://github.com/D4Vinci/Scrapling, parser.Selector).

XPath support here is intentionally tiny (only what the tests need) since
BeautifulSoup has no native XPath engine; production XPath goes through
Scrapling's real Selector, not this stand-in.
"""
from __future__ import annotations

from bs4 import BeautifulSoup


class _FakeTextNode:
    """A bare text node - what a real Scrapling/parsel `::text` match
    returns (as opposed to FakeElement, which represents a tag match).
    Only .get() is meaningful on it, mirroring the real API."""

    def __init__(self, text: str):
        self._text = str(text)

    def get(self):
        return self._text


class FakeElement:
    def __init__(self, node):
        self._node = node

    def css(self, selector: str) -> "FakeElementList":
        """Scrapling elements support .css()/.xpath() scoped to themselves
        (see README 'Advanced Navigation': `first_quote.css('.text::text')`).
        bs4's Tag.select() is descendant-scoped the same way.

        `::text` (with nothing before it) is handled specially here, the
        same way real Scrapling/parsel treat it when scoped from an
        already-matched element: it returns every descendant text node,
        at any depth - not just direct children. That's exactly the
        pattern app/core/engine/extractor.py's _element_text() relies on
        to pull "Holy Drilling" out of
        `<a class="business-name"><span>Holy Drilling</span></a>` (real
        yellowpages.com markup - see builtin_templates.py), and is the
        regression test coverage for a bug that used to return the
        element's raw outer HTML instead of its text."""
        selector = selector.strip()
        if selector == "::text":
            return FakeElementList(_FakeTextNode(t) for t in self._node.find_all(string=True) if str(t).strip())
        if "::text" in selector or "::attr" in selector:
            raise NotImplementedError("only bare '::text' is supported by this test double")
        return FakeElementList(FakeElement(n) for n in self._node.select(selector))

    def xpath(self, selector: str) -> "FakeElementList":
        raise NotImplementedError("scoped xpath not needed by these tests")

    def get(self):
        """Mirrors real Scrapling/parsel: .get() on an ELEMENT match
        returns that element's outer HTML, not its text - callers that
        want text must query '::text' (see .css() above). Do NOT
        "helpfully" return get_text() here; that was the whole point of
        the bug this test double now exists to catch."""
        return str(self._node)

    def getall(self):
        return [self.get()]

    @property
    def attrib(self):
        return dict(getattr(self._node, "attrs", {}))

    @property
    def html(self):
        return str(self._node)


class FakeElementList(list):
    def get(self):
        return self[0].get() if self else None

    def getall(self):
        return [e.get() for e in self]


class FakeSelector:
    def __init__(self, html: str):
        self._soup = BeautifulSoup(html, "html.parser")

    def css(self, selector: str) -> FakeElementList:
        if "::text" in selector or "::attr" in selector:
            raise NotImplementedError("pseudo-selectors aren't needed by these tests")
        return FakeElementList(FakeElement(n) for n in self._soup.select(selector))

    def xpath(self, selector: str) -> FakeElementList:
        # Tiny subset: "//tag[@class='x']" and "//tag" - enough for the
        # container-repeat test. Real XPath goes through Scrapling in prod.
        import re
        m = re.match(r"^//(\w+)(?:\[@class=['\"]([\w-]+)['\"]\])?$", selector.strip())
        if not m:
            raise ValueError(f"unsupported test xpath: {selector}")
        tag, cls = m.groups()
        if cls:
            nodes = self._soup.find_all(tag, class_=cls)
        else:
            nodes = self._soup.find_all(tag)
        return FakeElementList(FakeElement(n) for n in nodes)
