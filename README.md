# doclive

doclive serves a Sphinx site locally in one browser tab with three parts:
the rendered site, an editor of the sources behind the page on screen, and
a conversation with Claude. A toggle switches the page between Rendered
and Source.

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

## For Claude

Every line of the messages file is a message:
`{"time", "from": "owner" | "claudia", "text", "context": {"page", "mode",
"object", "path"}}`. Watch it for the owner's messages with

    tail -n0 -f <messages file> | grep --line-buffered '"from": "owner"'

and answer through the server, so that the panel shows the answer at once:

    curl -s localhost:8765/api/messages -H 'Content-Type: application/json' \
      -d '{"from": "claudia", "text": "..."}'

Edit the source files directly; the page picks the change up.
