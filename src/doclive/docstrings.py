"""Find the docstrings of a Python file and write one of them back.

A docstring is identified by its qualified name inside its module: "" for
the module's own, "Variants", "Variants.filter_by_maf", or
"PassStats.num_vars" for the docstring of an attribute, the string literal
that follows its assignment, which Sphinx's autodoc documents. Its text is
the one `inspect.cleandoc` gives, the text Sphinx renders, and writing it
back replaces only the literal of that docstring in the file.
"""

import ast
import inspect
from dataclasses import dataclass


@dataclass(frozen=True)
class Docstring:
    qualname: str
    text: str
    # The span of the string literal in the file: lines from 1, columns in
    # characters, the end exclusive.
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    raw: bool
    # Whether the closing quotes of a docstring of several lines stand on a
    # line of their own. Both are written in the same code base, and a
    # docstring keeps the form it had.
    closing_alone: bool


def _string_of(node: ast.AST) -> ast.Constant | None:
    if (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ):
        return node.value
    return None


def _is_shown(name: str, attribute: bool = False) -> bool:
    """Whether autodoc shows a member of this name by default: a public
    name, or a dunder attribute such as `__version__`."""
    if attribute and name.startswith("__") and name.endswith("__"):
        return True
    return not name.startswith("_")


def find_docstrings(source: str) -> list[Docstring]:
    """Every docstring that autodoc shows by default, in the order of the
    file."""
    lines = source.splitlines(keepends=True)
    tree = ast.parse(source)
    found = []

    def add(qualname: str, literal: ast.Constant) -> None:
        # ast gives the columns in bytes of UTF-8.
        start = len(lines[literal.lineno - 1].encode()[: literal.col_offset].decode())
        end = len(
            lines[literal.end_lineno - 1].encode()[: literal.end_col_offset].decode()
        )
        prefix = lines[literal.lineno - 1][start : start + 2].lower()
        last = lines[literal.end_lineno - 1][:end]
        found.append(
            Docstring(
                qualname=qualname,
                text=inspect.cleandoc(literal.value),
                start_line=literal.lineno,
                start_col=start,
                end_line=literal.end_lineno,
                end_col=end,
                raw="r" in prefix.rstrip("\"'"),
                closing_alone=last[:-3].strip() == "",
            )
        )

    def walk(body: list[ast.stmt], prefix: str) -> None:
        for i, node in enumerate(body):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                if not _is_shown(node.name):
                    continue
                qualname = prefix + node.name
                if node.body and (literal := _string_of(node.body[0])):
                    add(qualname, literal)
                if isinstance(node, ast.ClassDef):
                    walk(node.body, qualname + ".")
            elif isinstance(node, ast.Assign | ast.AnnAssign) and i + 1 < len(body):
                literal = _string_of(body[i + 1])
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if literal and isinstance(targets[0], ast.Name):
                    if _is_shown(targets[0].id, attribute=True):
                        add(prefix + targets[0].id, literal)

    if tree.body and (literal := _string_of(tree.body[0])):
        add("", literal)
    walk(tree.body, "")
    return found


def _literal(text: str, indent: int, raw: bool, closing_alone: bool) -> str:
    """The source of a docstring whose cleaned text is `text`, with the
    closing quotes of one of several lines on a line of their own when
    `closing_alone`."""
    if raw:
        if '"""' in text or text.endswith("\\"):
            raise ValueError(
                'a raw docstring cannot hold """ or end in a backslash; '
                "take the r prefix off the docstring in the file first"
            )
        body, prefix = text, "r"
    else:
        body, prefix = text.replace("\\", "\\\\").replace('"""', '\\"\\"\\"'), ""
    lines = body.split("\n")
    if len(lines) == 1:
        if body.endswith('"'):
            body = body[:-1] + '\\"'
        return f'{prefix}"""{body}"""'
    pad = " " * indent
    rest = [pad + line if line.strip() else "" for line in lines[1:]]
    closing = f'\n{pad}"""' if closing_alone else '"""'
    if not closing_alone and rest[-1].endswith('"'):
        rest[-1] = rest[-1][:-1] + '\\"'
    return f'{prefix}"""{lines[0]}\n' + "\n".join(rest) + closing


def replace_docstring(source: str, qualname: str, text: str) -> str:
    """`source` with the docstring of `qualname` holding `text`.

    It raises KeyError when the file has no docstring of that name, and
    ValueError when the result would not parse or would not give `text`
    back.
    """
    text = inspect.cleandoc(text)
    target = {d.qualname: d for d in find_docstrings(source)}[qualname]
    lines = source.splitlines(keepends=True)
    before = "".join(lines[: target.start_line - 1]) + lines[target.start_line - 1][
        : target.start_col
    ]
    after = lines[target.end_line - 1][target.end_col :] + "".join(
        lines[target.end_line :]
    )
    literal = _literal(text, target.start_col, target.raw, target.closing_alone)
    new = before + literal + after
    try:
        written = {d.qualname: d for d in find_docstrings(new)}.get(qualname)
    except SyntaxError as error:
        raise ValueError(f"the file would not parse: {error}") from None
    if written is None or written.text != text:
        raise ValueError("the docstring would not read back as it was written")
    return new
