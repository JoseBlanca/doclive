"""A Sphinx extension that says where each block of a page came from.

doclive loads it into the build of the documented project, which does not
know of it. A block is a paragraph, a list, a code block, a title or
another element that stands directly in a section or in the body of a
documented object. Each one gets a class, `dlb-<n>`, in the HTML, and the
file `_doclive/<page>.json` of the build says, for each n, the source the
block was parsed from and the line it starts at: for a page, the file; for
a docstring, the file and "docstring of <the object>", with the line
counted inside the docstring.

It imports nothing of doclive: it runs in the environment of the project.
"""

import json
from pathlib import Path

from docutils import nodes
from sphinx import addnodes

BLOCKS = (
    nodes.paragraph,
    nodes.literal_block,
    nodes.bullet_list,
    nodes.enumerated_list,
    nodes.definition_list,
    nodes.block_quote,
    nodes.table,
    nodes.title,
    nodes.Admonition,
    nodes.math_block,
)
CONTAINERS = (nodes.section, nodes.document, addnodes.desc_content)


def _where(node):
    """The source and the line of a node, or of the first thing inside it
    that has them: the parser of reStructuredText gives a list none."""
    for candidate in node.findall(nodes.Element, include_self=True):
        if candidate.source and candidate.line:
            return candidate.source, candidate.line
    return None


def mark_blocks(app, doctree, docname):
    blocks = []
    for node in doctree.findall(lambda n: isinstance(n, BLOCKS)):
        # A documented object is an admonition to docutils, and it is no
        # block: its body holds the blocks.
        if not isinstance(node.parent, CONTAINERS) or isinstance(node, addnodes.desc):
            continue
        where = _where(node)
        if where is None:
            continue
        node["classes"].append(f"dlb-{len(blocks)}")
        blocks.append(
            {
                "source": where[0],
                "line": where[1],
                "kind": type(node).__name__,
            }
        )
    out = Path(app.outdir) / "_doclive" / f"{docname}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(blocks))


def setup(app):
    app.connect("doctree-resolved", mark_blocks)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
