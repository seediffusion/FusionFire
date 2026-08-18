#!/usr/bin/env python3
"""Turn README.md into a readable HTML copy for the release folder.

    uv run readme_html.py [output.html]

A player who has unzipped the game has a folder and no documentation in it,
and a Markdown file is a poor answer: Windows has nothing registered to open
one, so double-clicking it does nothing useful. An HTML file opens in the
browser they already have.

Because that browser is often driven by a screen reader, the page is built
to be read rather than merely displayed:

* real heading structure straight from the Markdown, with a table of
  contents so a reader can jump rather than arrow through 300 lines;
* a skip link and a ``main`` landmark, so the contents can be passed over;
* ``scope`` on every table header, which is what tells a screen reader which
  column it is reading when it announces a cell -- the Markdown converter
  does not add it;
* one self-contained file. No fonts, scripts or stylesheets are fetched, so
  it works offline, which is where it will be.

It follows the reader's light or dark preference too, for the same reason
the game does.
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "README.md"

#: Written out in full rather than linked. A stylesheet the page cannot
#: reach is a page with no styling at all, and this one lives beside a game
#: on somebody's disk, not on a web server.
STYLE = """
:root {
  color-scheme: light dark;
  --bg: #ffffff;
  --fg: #1b1b1b;
  --muted: #5a5a5a;
  --rule: #d8d8d8;
  --accent: #8a2b06;
  --code-bg: #f2f2f2;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #202020;
    --fg: #f0f0f0;
    --muted: #b4b4b4;
    --rule: #3d3d3d;
    --accent: #ff9e6d;
    --code-bg: #2b2b2b;
  }
}
* { box-sizing: border-box; }
body {
  background: var(--bg);
  color: var(--fg);
  font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
  font-size: 1.05rem;
  line-height: 1.65;
  margin: 0 auto;
  max-width: 46rem;
  padding: 2rem 1.25rem 5rem;
}
h1, h2, h3, h4 { line-height: 1.25; margin: 2.2rem 0 0.6rem; }
h1 { font-size: 2.1rem; margin-top: 0.5rem; }
h2 { font-size: 1.55rem; border-bottom: 2px solid var(--rule); padding-bottom: 0.3rem; }
h3 { font-size: 1.2rem; }
p, ul, ol, table, pre { margin: 0.9rem 0; }
a { color: var(--accent); }
a:focus-visible, .skip:focus {
  outline: 3px solid var(--accent);
  outline-offset: 2px;
}
code {
  background: var(--code-bg);
  border-radius: 4px;
  font-family: Consolas, "Cascadia Mono", monospace;
  font-size: 0.94em;
  padding: 0.12em 0.35em;
}
pre {
  background: var(--code-bg);
  border-radius: 6px;
  overflow-x: auto;
  padding: 0.9rem 1rem;
}
pre code { background: none; padding: 0; }
table { border-collapse: collapse; width: 100%; }
th, td {
  border: 1px solid var(--rule);
  padding: 0.45rem 0.7rem;
  text-align: left;
  vertical-align: top;
}
th { background: var(--code-bg); }
blockquote {
  border-left: 4px solid var(--rule);
  color: var(--muted);
  margin: 1rem 0;
  padding: 0.2rem 0 0.2rem 1rem;
}
hr { border: 0; border-top: 2px solid var(--rule); margin: 2.5rem 0; }
.skip {
  background: var(--bg);
  color: var(--fg);
  left: 0.5rem;
  padding: 0.6rem 1rem;
  position: absolute;
  top: -4rem;
  transition: top 0.15s;
  z-index: 10;
}
.skip:focus { top: 0.5rem; }
nav.toc {
  border: 1px solid var(--rule);
  border-radius: 6px;
  padding: 0.5rem 1rem 1rem;
}
nav.toc ul { margin: 0.3rem 0; padding-left: 1.3rem; }
footer {
  border-top: 1px solid var(--rule);
  color: var(--muted);
  font-size: 0.92rem;
  margin-top: 3rem;
  padding-top: 1rem;
}
"""

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<style>{style}</style>
</head>
<body>
<a class="skip" href="#contents">Skip to the documentation</a>
<main id="contents">
{body}
</main>
<footer>
<p>{footer}</p>
</footer>
</body>
</html>
"""


def render(markdown_text: str) -> tuple[str, str]:
    """Return ``(body html, toc html)`` for the given Markdown."""
    import markdown

    converter = markdown.Markdown(
        extensions=["tables", "fenced_code", "sane_lists", "toc"],
        extension_configs={"toc": {"title": "", "toc_depth": "2-3"}},
    )
    body = converter.convert(markdown_text)
    return body, getattr(converter, "toc", "")


def add_table_scopes(body: str) -> str:
    """Give every table header a ``scope``.

    Python-Markdown emits a bare ``<th>``. Without ``scope="col"`` a screen
    reader has to guess which header belongs to the cell it is announcing,
    and in a table of keys and actions that is the whole content.
    """
    return re.sub(r"<th(?![^>]*\bscope=)", '<th scope="col"', body)


def first_heading(markdown_text: str, default: str = "Read me") -> str:
    for line in markdown_text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return default


def summary(markdown_text: str) -> str:
    """The first real paragraph, for the description meta tag."""
    for block in markdown_text.split("\n\n"):
        block = block.strip()
        if block and not block.startswith(("#", "|", "*", "-", "`", "[")):
            return " ".join(block.split())[:200]
    return ""


NAV = """<nav class="toc" aria-labelledby="toc-heading">
<h2 id="toc-heading">Contents</h2>
{toc}
</nav>"""


def insert_contents(body: str, toc: str) -> str:
    """Put the contents after the title, not before it.

    Heading order is how a screen reader user navigates a long page. A
    "Contents" heading sitting above the document's own h1 means the first
    thing found when arrowing through headings is the furniture rather than
    the title, so the nav goes immediately after the h1 instead.
    """
    if not toc:
        return body
    nav = NAV.format(toc=toc)
    end_of_title = body.find("</h1>")
    if end_of_title == -1:
        return nav + body
    cut = end_of_title + len("</h1>")
    return body[:cut] + "\n" + nav + body[cut:]


def build(source: Path = SOURCE, version: str = "") -> str:
    text = source.read_text(encoding="utf-8")
    body, toc = render(text)
    body = add_table_scopes(body)
    body = insert_contents(body, toc)

    title = first_heading(text)
    footer = f"{title} documentation"
    if version:
        footer += f", version {version}"
    footer += ". This page is a copy of README.md and needs no internet connection."

    return PAGE.format(
        title=html.escape(title),
        description=html.escape(summary(text)),
        style=STYLE,
        body=body,
        footer=html.escape(footer),
    )


def main(argv: list[str]) -> int:
    if not SOURCE.is_file():
        print(f"No README at {SOURCE}", file=sys.stderr)
        return 1
    target = Path(argv[0]) if argv else ROOT / "readme.html"

    try:
        import fusionfire

        version = fusionfire.__version__
    except Exception:
        version = ""

    target.write_text(build(version=version), encoding="utf-8")
    print(f"Wrote {target} ({target.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
