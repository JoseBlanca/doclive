# doclive

doclive serves a Sphinx site locally in one browser tab with three parts:
the rendered site, an editor of the sources behind the page on screen, and
a conversation with Claude. A toggle switches the page between Rendered
and Source.

In Rendered, a paragraph, a list, a code block or a title can be edited
where it stands: double-click it, or click the pencil that appears beside
it, and the block is replaced by its source, the lines of the Markdown
file or of the docstring that it was rendered from. Cmd+Enter saves, Esc
cancels, and when the site is built again, in about 2 s, the page shows
the block rendered. A rebuild that someone else's change causes while a
block is open waits until the editor is closed. A signature and the
sidebar have no text of their own and do not respond.

doclive knows the lines of each block from a Sphinx extension of its own,
`doclive_blocks`, which it loads into the build of the project without the
project's `conf.py` naming it. On popnei's ten pages, 583 of the 585 blocks
are editable; the other two are text that Sphinx takes from Python's own
`typing.Literal`.

In Source, the page shows:
- its own Markdown or reST file;
- the docstring of every module, class, function, method and attribute
  that the page documents with `automodule`, each in its own editor.

Saving an editor writes the file. For a docstring, only that string
literal in the `.py` file is replaced, and the file is checked to still
parse as Python first. Any change to a source, from the page, from Claude
or from another editor, rebuilds the site. The tab then reloads the
rendered page at the same place, and updates the editors that have no
unsaved text. When a file changes under an editor that has unsaved text,
the editor offers to take the file's version or to keep and save its own.

The message panel writes to a file, one JSON object per line, which Claude
watches from its session. Claude's answers are written to the same file and
appear in the panel. Each message carries the page, and the object on
screen or being edited, so that "shorter" is enough to say what should be
shorter.

## Running it

doclive has no dependencies of its own. The site is built with the
`sphinx-build` of the documented project's environment, which has to be
able to import the documented package.

```console
$ cd ~/devel/doclive
$ uv run doclive ~/devel/popnei/.claude/worktrees/docs-sphinx
building .../docs/api ...
built in 1.95 s
doclive at http://127.0.0.1:8765/
messages in /Users/jose/.doclive/docs-sphinx-f2db24e7/messages.jsonl
```

The options give the Sphinx source directory (`--docs`, `docs/api` by
default), the directory that holds the package (`--python`, `python`), the
`sphinx-build` to run (`--sphinx`, `.venv/bin/sphinx-build`), the port
(`--port`, 8765) and the file of the messages (`--messages`). All the paths
are relative to the project root.

A change to a docstring needs a full rebuild, `sphinx-build -E`, because
Sphinx does not know which pages a docstring appears on. On popnei's site,
eight reference pages and two written pages, a full rebuild took 1.7 to
1.9 s on an Apple M-series Mac on 30 September 2026.

## Asking Claude about a block

The editor of a block has three buttons that send the block, as it is in
the editor at that moment, to Claude:

- **Correct English** comes back as the corrected text, with the words
  taken out struck through and the words put in highlighted.
- **Suggest** comes back as a note to read and dismiss.
- **Ask** takes one line of instruction, "shorter", and comes back as a
  proposed text marked like a correction.

The answer appears in a cell under the editor. Accept puts a proposed text
into the editor and saves nothing: Save is the only action that writes the
file. The header of the conversation says whether a session of Claude is
listening, and a request made when none is gets no answer.

## For Claude

Listen from the session, as a Monitor, with the Python of doclive's own
environment and the state directory that the server prints when it starts:

    ~/devel/doclive/.venv/bin/python -m doclive.agent listen <state directory>

Each line it prints is a message of the owner from the panel,
`{"from": "owner", "text", "context": {"page", "mode", "object", "path",
"lines"}}`, or a request from a block, `{"id", "kind": "correct" |
"suggest" | "ask", "instruction", "page", "path", "qualname", "lines",
"text"}`. Answer a request with the text on standard input, `rewrite` for
a text that replaces the block's and `note` for one that is read:

    ~/devel/doclive/.venv/bin/python -m doclive.agent answer <id> rewrite <<'EOF'
    the new text of the block
    EOF

and write in the panel with `python -m doclive.agent say`, the same way.
A `rewrite` is the source of the block, Markdown or reStructuredText as the
block is, wrapped as its file is. Edit the source files directly for
anything else; the page picks the change up.
