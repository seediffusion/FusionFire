"""Speech text sanitising.

These guard the workaround for the Prism 0.17.3 encoding bug documented in
:mod:`fusionfire.speech`. If Prism is ever fixed, ``make_speakable`` becomes
harmless rather than wrong, and these tests should be deleted along with it.
"""

from __future__ import annotations

import pytest

from fusionfire.speech import _unsupported, make_speakable


# ----------------------------------------------------------------------
# The failure signature
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "char",
    ["—", "–", "‘", "’", "“", "”", "…", "•"],
)
def test_typographic_punctuation_is_recognised_as_unsupported(char):
    assert _unsupported(char)


@pytest.mark.parametrize("char", ["a", "1", " ", "é", "£", "中", "\U0001f600"])
def test_safe_characters_are_left_alone(char):
    assert not _unsupported(char)


def test_the_rule_matches_the_measured_boundary():
    """Three UTF-8 bytes with a 0x80-0x9F continuation byte, and nothing else.

    Equivalently ``cp % 4096 < 2048`` above U+07FF, which is what was
    measured against the live backend.
    """
    for cp in range(0x20, 0x11000, 7):
        char = chr(cp)
        if 0xD800 <= cp <= 0xDFFF:  # lone surrogates have no encoding
            continue
        expected = cp > 0x7FF and cp < 0x10000 and (cp % 4096) < 2048
        assert _unsupported(char) is expected, f"U+{cp:04X}"


# ----------------------------------------------------------------------
# Rewriting
# ----------------------------------------------------------------------
def test_ascii_text_passes_through_untouched():
    text = "You shoot and hit for 12. Blue Screen is on 74."
    assert make_speakable(text) is text


def test_the_result_is_always_speakable():
    messy = "You lash — hit for 5… “nice” ‘shot’ • café"
    assert not any(_unsupported(c) for c in make_speakable(messy))


def test_punctuation_becomes_its_ascii_equivalent():
    assert make_speakable("a—b") == "a - b"
    assert make_speakable("“hello”") == '"hello"'
    assert make_speakable("wait…") == "wait..."
    assert make_speakable("it’s") == "it's"


def test_supported_non_ascii_survives():
    assert make_speakable("café 中") == "café 中"


def test_accented_characters_in_the_bad_range_decompose_to_ascii():
    # U+1E9E (capital sharp S) is unsupported; it must still say something.
    result = make_speakable("Straẞe")
    assert not any(_unsupported(c) for c in result)
    assert result.strip()


def test_an_unmappable_character_becomes_a_space_not_a_hole():
    # U+1000 (Myanmar) has no ASCII decomposition, but the sentence around it
    # must still reach the player.
    result = make_speakable("name က here")
    assert not any(_unsupported(c) for c in result)
    assert "name" in result and "here" in result


def test_empty_and_whitespace_are_safe():
    assert make_speakable("") == ""
    assert make_speakable("   ") == "   "


# ----------------------------------------------------------------------
# The game's own strings
# ----------------------------------------------------------------------
def test_no_user_facing_literal_in_the_package_is_unspeakable():
    """Belt and braces: the sanitiser handles it, but the source should not
    need it. Docstrings and the substitution table itself are exempt."""
    import ast
    import pathlib

    import fusionfire

    root = pathlib.Path(fusionfire.__file__).parent
    offenders = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "speech.py":
            continue  # holds the substitution table by definition
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                doc = ast.get_docstring(node, clean=False)
                if doc:
                    docstrings.add(doc)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in docstrings:
                    continue
                if any(_unsupported(c) for c in node.value):
                    offenders.append(f"{path.name}:{node.lineno}: {node.value[:60]!r}")
    assert offenders == [], "unspeakable literals: " + "; ".join(offenders)
