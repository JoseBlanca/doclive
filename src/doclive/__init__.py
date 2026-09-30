"""doclive: edit a Sphinx site and the docstrings it is built from, in one
browser tab, with a conversation with Claude beside it."""

import argparse
from pathlib import Path

from doclive.server import Project, project_slug, serve


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="doclive",
        description=(
            "Serve a Sphinx site with an editor of its Markdown pages and of the "
            "docstrings its automodule directives document, and a message panel."
        ),
    )
    parser.add_argument("project", type=Path, help="the root of the documented project")
    parser.add_argument(
        "--docs", default="docs/api", help="the Sphinx source directory, from the root"
    )
    parser.add_argument(
        "--python",
        default="python",
        help="the directory that holds the documented package, from the root",
    )
    parser.add_argument(
        "--sphinx",
        default=".venv/bin/sphinx-build",
        help="the sphinx-build of the project's environment, from the root",
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--messages",
        type=Path,
        help="the file of the conversation, by default under ~/.doclive/",
    )
    args = parser.parse_args()

    root = args.project.resolve()
    docs = (root / args.docs).resolve()
    project = Project(
        root=root,
        docs=docs,
        build=docs / "_build",
        python=(root / args.python).resolve(),
        sphinx=root / args.sphinx,
    )
    messages = args.messages or (
        Path.home() / ".doclive" / project_slug(root) / "messages.jsonl"
    )
    serve(project, messages, args.host, args.port)
