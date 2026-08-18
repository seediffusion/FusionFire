"""The HTML copy of the documentation that ships in the release folder.

Markdown is the wrong thing to hand a player: Windows has nothing
registered to open a .md file, so double-clicking it does nothing. HTML
opens in the browser they already have -- and because that browser is
frequently driven by a screen reader, the page has to be navigable rather
than merely rendered.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

readme_html = pytest.importorskip("readme_html")


class Scan(HTMLParser):
    """Enough of a parser to ask structural questions of the output."""

    def __init__(self) -> None:
        super().__init__()
        self.headings: list[tuple[int, str]] = []
        self.tags: list[str] = []
        self.risky: list[str] = []
        self.links = 0
        self.headers = 0
        self.scoped_headers = 0
        self.lang: str | None = None
        self.title = ""
        self._in_title = False
        self._heading: tuple[int, str] | None = None

    def handle_starttag(self, tag, attrs):
        found = dict(attrs)
        self.tags.append(tag)
        if tag == "html":
            self.lang = found.get("lang")
        if tag == "title":
            self._in_title = True
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._heading = (int(tag[1]), "")
        if tag == "th":
            self.headers += 1
            self.scoped_headers += bool(found.get("scope"))
        if tag == "a":
            self.links += 1
        if tag in ("script", "iframe", "object", "embed"):
            self.risky.append(f"<{tag}>")
        for key in ("src", "href"):
            value = found.get(key, "")
            if tag in ("script", "link", "img") and value.startswith(
                ("http://", "https://", "//")
            ):
                self.risky.append(f"{tag} {key}={value}")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag.startswith("h") and self._heading and tag[1:].isdigit():
            self.headings.append(self._heading)
            self._heading = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._heading:
            self._heading = (self._heading[0], self._heading[1] + data)


@pytest.fixture(scope="module")
def page() -> str:
    return readme_html.build(version="1.0.0")


@pytest.fixture(scope="module")
def scan(page: str) -> Scan:
    parser = Scan()
    parser.feed(page)
    return parser


# ----------------------------------------------------------------------
# The basics a browser and a screen reader both need
# ----------------------------------------------------------------------
def test_it_is_a_complete_document(page: str):
    assert page.lstrip().lower().startswith("<!doctype html>")
    assert "charset=\"utf-8\"" in page.lower()


def test_it_declares_its_language(scan: Scan):
    """Without this a screen reader reads English with whatever voice and
    pronunciation rules it happens to be set to."""
    assert scan.lang == "en"


def test_it_has_a_title_worth_reading(scan: Scan):
    assert scan.title.strip() == "Fusion Fire"


# ----------------------------------------------------------------------
# Getting around it
# ----------------------------------------------------------------------
def test_there_is_a_main_landmark(scan: Scan):
    assert "main" in scan.tags


def test_the_contents_are_a_nav_landmark(page: str, scan: Scan):
    assert "nav" in scan.tags
    assert 'aria-labelledby="toc-heading"' in page


def test_the_skip_link_goes_somewhere_real(page: str):
    assert 'href="#contents"' in page
    assert 'id="contents"' in page


def test_the_title_is_the_first_heading(scan: Scan):
    """The contents block carries its own heading, and putting it above the
    document's h1 would mean the first thing a heading-by-heading reader
    finds is the furniture rather than the title."""
    assert scan.headings, "no headings at all"
    level, text = scan.headings[0]
    assert level == 1, f"the page opens on an h{level}: {text.strip()!r}"
    assert text.strip() == "Fusion Fire"


def test_there_is_exactly_one_h1(scan: Scan):
    assert [level for level, _ in scan.headings].count(1) == 1


def test_no_heading_level_is_skipped(scan: Scan):
    levels = [level for level, _ in scan.headings]
    jumps = [(a, b) for a, b in zip(levels, levels[1:]) if b > a + 1]
    assert jumps == [], f"h{jumps[0][0]} followed by h{jumps[0][1]}" if jumps else ""


def test_the_structure_is_worth_navigating(scan: Scan):
    assert len(scan.headings) > 10


# ----------------------------------------------------------------------
# Tables
# ----------------------------------------------------------------------
def test_every_table_header_has_a_scope(scan: Scan):
    """The keyboard reference is a table. Without scope a screen reader has
    to guess which header belongs to the cell it is announcing, and in a
    table of keys and actions the header is half the meaning.

    Python-Markdown does not add it, so the generator has to.
    """
    assert scan.headers > 0, "no table headers found"
    assert scan.scoped_headers == scan.headers, (
        f"only {scan.scoped_headers} of {scan.headers} headers are scoped"
    )


def test_the_keyboard_table_survived_the_conversion(page: str):
    for key in ("Crack whip", "Restore health", "Open the cheat prompt"):
        assert key in page


# ----------------------------------------------------------------------
# It has to work off a disk with no internet
# ----------------------------------------------------------------------
def test_nothing_is_fetched_from_the_network(scan: Scan):
    assert scan.risky == [], f"external or active content: {scan.risky[:3]}"


def test_the_styles_travel_with_it(page: str):
    assert "<style>" in page
    assert "stylesheet" not in page, "a linked stylesheet would not resolve offline"


def test_it_follows_the_readers_theme(page: str):
    """Same reasoning as the game's own dark mode."""
    assert "prefers-color-scheme: dark" in page
    assert "color-scheme: light dark" in page


def test_focus_stays_visible(page: str):
    assert "focus-visible" in page


# ----------------------------------------------------------------------
# The content actually made it
# ----------------------------------------------------------------------
def test_the_links_are_carried_over(scan: Scan):
    assert scan.links > 5


def test_no_markdown_is_left_unconverted(page: str):
    leftovers = re.findall(r"^\s*(#{1,4} |\|\s*---)", page, re.MULTILINE)
    assert leftovers == [], f"raw markdown in the output: {leftovers[:3]}"


def test_the_version_is_stated(page: str):
    assert "1.0.0" in page


def test_a_readme_without_headings_still_produces_a_page():
    """Defensive: the generator should not depend on the shape of the file."""
    body, toc = readme_html.render("Just a sentence, no headings at all.")
    assert "Just a sentence" in readme_html.insert_contents(body, toc)


def test_the_generator_points_at_the_real_readme():
    assert readme_html.SOURCE.name == "README.md"
    assert readme_html.SOURCE.is_file()


def test_the_build_places_it_beside_the_game():
    """It is no use in the source tree; it has to be in the release folder."""
    source = Path(readme_html.__file__).with_name("build.py").read_text(encoding="utf-8")
    assert "write_readme" in source
    assert 'readme.html' in source
