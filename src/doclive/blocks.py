"""The lines of a source that one rendered block came from.

The Sphinx extension `doclive_blocks` gives each block the line it starts
at. Where it ends is worked out here from the text: a paragraph ends at
the first blank line, a fenced code block at its closing fence, a list at
the first line that is neither an item nor a continuation of one, and
nothing reaches the line the next block starts at.
"""

import re

FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
ITEM = re.compile(r"^\s*([-*+]|\d+[.)]|\(\d+\))\s")
RST_UNDERLINE = re.compile(r"^([=\-~^\"'`#*+.:_])\1{2,}\s*$")


def block_end(lines: list[str], start: int, kind: str, next_start: int | None) -> int:
    """The last line of the block that starts at line `start`, both from 1.

    `next_start` is the line the next block of the same source starts at.
    """
    limit = (next_start - 1) if next_start else len(lines)
    end = start

    if kind == "title":
        if start < limit and RST_UNDERLINE.match(lines[start]):
            end = start + 1
        return end

    fence = FENCE.match(lines[start - 1])
    if kind == "literal_block" and fence:
        closing = re.compile(rf"^\s*{re.escape(fence.group(1)[0])}{{{len(fence.group(1))},}}\s*$")
        for i in range(start + 1, limit + 1):
            if closing.match(lines[i - 1]):
                return i
        return limit

    if kind == "paragraph":
        for i in range(start + 1, limit + 1):
            if not lines[i - 1].strip():
                break
            end = i
        return end

    if kind in ("bullet_list", "enumerated_list"):
        i = start + 1
        while i <= limit:
            line = lines[i - 1]
            if line.strip():
                end = i
            else:
                following = next(
                    (lines[j - 1] for j in range(i + 1, limit + 1) if lines[j - 1].strip()),
                    None,
                )
                if following is None or not (
                    ITEM.match(following) or following[:1].isspace()
                ):
                    break
            i += 1
        return end

    for i in range(start + 1, limit + 1):
        if lines[i - 1].strip():
            end = i
    return end


def replace_lines(whole: str, start: int, end: int, base: str, text: str) -> str:
    """`whole` with its lines `start` to `end` holding `text`, when they
    still hold `base`. When they do not, but `base` is in `whole` exactly
    once as whole lines, that place is used: lines were added or taken
    above the block since the page was built. Otherwise LookupError.
    """
    lines = whole.split("\n")
    base_lines = base.split("\n")
    if lines[start - 1 : end] != base_lines:
        places = [
            i
            for i in range(len(lines) - len(base_lines) + 1)
            if lines[i : i + len(base_lines)] == base_lines
        ]
        if len(places) != 1:
            raise LookupError("the block is no longer where the page says it is")
        start, end = places[0] + 1, places[0] + len(base_lines)
    new = text.rstrip("\n").split("\n") if text.strip() else []
    return "\n".join(lines[: start - 1] + new + lines[end:])
