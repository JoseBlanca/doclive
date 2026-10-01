"""The local server: the rendered site, the sources behind each page, the
saves, the rebuilds, and the requests the owner makes to Claude from a
block.

Everything is in the standard library. The server builds the site with
the Sphinx of the documented project, in a process of its own for each
build, so that the package it documents is imported afresh and a changed
docstring is read.
"""

import hashlib
import json
import mimetypes
import os
import re
import subprocess
import threading
import time
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from doclive.blocks import block_end, replace_lines
from doclive.docstrings import find_docstrings, replace_docstring

STATIC = Path(__file__).parent / "static"
SPHINXEXT = Path(__file__).parent / "sphinxext"
# Sphinx is started through this, in the Python of the project, so that the
# build loads doclive's extension as one of Sphinx's own and the project's
# conf.py need not name it.
BOOT = """
import sys
sys.path.insert(0, sys.argv.pop(1))
import sphinx.application
sphinx.application.builtin_extensions = (
    *sphinx.application.builtin_extensions, "doclive_blocks")
from sphinx.cmd.build import main
sys.exit(main(sys.argv[1:]))
"""
DOCSTRING_OF = ":docstring of "
AUTOMODULE = re.compile(r"^(?:\.\. automodule::|```\{automodule\})\s+(\S+)", re.M)


class Project:
    """The documented project: where its pages, its package and its build
    are, and the build itself."""

    def __init__(
        self, root: Path, docs: Path, build: Path, python: Path, sphinx: Path
    ):
        self.root = root
        self.docs = docs
        self.build = build
        self.python = python
        self.sphinx = sphinx
        self.build_lock = threading.Lock()
        self.last_build = {"ok": None, "log": "", "seconds": 0.0, "id": 0}

    # --- The sources of a page ---

    def page_file(self, page: str) -> Path | None:
        for suffix in (".md", ".rst"):
            path = self.docs / (page + suffix)
            if path.is_file() and path.resolve().is_relative_to(self.docs):
                return path
        return None

    def module_file(self, module: str) -> Path | None:
        base = self.python.joinpath(*module.split("."))
        for path in (base.with_suffix(".py"), base / "__init__.py"):
            if path.is_file():
                return path
        return None

    def sources(self, page: str) -> list[dict]:
        """The page's own file and the docstrings of every module it
        documents with automodule, as the editor shows them."""
        path = self.page_file(page)
        if path is None:
            return []
        text = path.read_text()
        items = [
            {
                "kind": "page",
                "path": self.rel(path),
                "qualname": None,
                "title": self.rel(path),
                "anchor": None,
                "text": text,
            }
        ]
        for module in AUTOMODULE.findall(text):
            module_path = self.module_file(module)
            if module_path is None:
                continue
            for doc in find_docstrings(module_path.read_text()):
                items.append(
                    {
                        "kind": "docstring",
                        "path": self.rel(module_path),
                        "qualname": doc.qualname,
                        "title": doc.qualname or module,
                        "module": module,
                        "anchor": (
                            f"{module}.{doc.qualname}"
                            if doc.qualname
                            else f"module-{module}"
                        ),
                        "text": doc.text,
                    }
                )
        return items

    def rel(self, path: Path) -> str:
        return str(path.resolve().relative_to(self.root))

    def file_of(self, rel: str) -> Path:
        path = (self.root / rel).resolve()
        if not path.is_relative_to(self.root) or not (
            path.is_relative_to(self.docs) or path.is_relative_to(self.python)
        ):
            raise PermissionError(f"{rel} is not a source of the site")
        return path

    def current_text(self, rel: str, qualname: str | None) -> str:
        source = self.file_of(rel).read_text()
        if qualname is None:
            return source
        docs = {d.qualname: d for d in find_docstrings(source)}
        if qualname not in docs:
            raise KeyError(f"{rel} has no docstring {qualname!r} now")
        return docs[qualname].text

    def save(self, rel: str, qualname: str | None, base: str, text: str) -> str:
        """Write `text` as the page or the docstring, when what is in the
        file is still `base`, the text the editor started from. Otherwise
        raise Conflict with what the file holds now."""
        path = self.file_of(rel)
        current = self.current_text(rel, qualname)
        if current != base and current != text:
            raise Conflict(current)
        if qualname is None:
            new = text
        else:
            new = replace_docstring(path.read_text(), qualname, text)
        tmp = path.with_name(f".{path.name}.doclive")
        tmp.write_text(new)
        os.replace(tmp, path)
        return self.current_text(rel, qualname)

    # --- The blocks of a rendered page ---

    def block(self, page: str, n: int) -> dict:
        """The source lines of block `n` of `page`: the file, the
        docstring when it is one, the lines and their text.

        It raises LookupError when the block has no source that doclive
        edits, and StaleBuild when the source changed after the page was
        built, so that its line numbers may be those of another text.
        """
        sidecar = self.build / "_doclive" / f"{page}.json"
        if not sidecar.is_file():
            raise LookupError("the build of this page marked no blocks")
        blocks = json.loads(sidecar.read_text())
        if not 0 <= n < len(blocks):
            raise LookupError("the page has no such block")
        source = blocks[n]["source"]
        file, _, name = source.partition(DOCSTRING_OF)
        path = Path(file).resolve()
        if not (path.is_relative_to(self.docs) or path.is_relative_to(self.python)):
            raise LookupError("the text of this block is not in the project")
        if path.stat().st_mtime > sidecar.stat().st_mtime:
            raise StaleBuild()
        qualname = None
        if name:
            module = ".".join(path.relative_to(self.python).with_suffix("").parts)
            module = module.removesuffix(".__init__")
            if name != module and not name.startswith(module + "."):
                raise LookupError("the text of this block is not in the project")
            qualname = name[len(module) + 1 :]
        try:
            whole = self.current_text(self.rel(path), qualname)
        except KeyError:
            raise LookupError("the text of this block is not in the project") from None
        lines = whole.split("\n")
        start = blocks[n]["line"]
        later = [
            b["line"] for b in blocks if b["source"] == source and b["line"] > start
        ]
        end = block_end(lines, start, blocks[n]["kind"], min(later, default=None))
        return {
            "path": self.rel(path),
            "qualname": qualname,
            "start": start,
            "end": end,
            "text": "\n".join(lines[start - 1 : end]),
        }

    def save_block(
        self, rel: str, qualname: str | None, start: int, end: int, base: str, text: str
    ) -> None:
        whole = self.current_text(rel, qualname)
        self.save(rel, qualname, whole, replace_lines(whole, start, end, base, text))

    # --- The build ---

    def run_build(self, full: bool) -> dict:
        with self.build_lock:
            started = time.monotonic()
            args = ["-q", str(self.docs), str(self.build)]
            if full:
                args.insert(0, "-E")
            python = self.sphinx.parent / "python"
            if python.exists():
                args = [str(python), "-c", BOOT, str(SPHINXEXT), *args]
            else:
                # Without the Python of the project the site is built as it
                # is, and no block of it can be edited in place.
                args = [str(self.sphinx), *args]
            done = subprocess.run(
                args, cwd=self.root, capture_output=True, text=True, check=False
            )
            log = (done.stdout + done.stderr).strip()
            self.last_build = {
                "ok": done.returncode == 0,
                "log": log[-4000:],
                "seconds": round(time.monotonic() - started, 2),
                "id": self.last_build["id"] + 1,
            }
            return self.last_build


class StaleBuild(Exception):
    pass


class Conflict(Exception):
    def __init__(self, current: str):
        super().__init__("the file changed since the editor read it")
        self.current = current


class Hub:
    """The browsers that listen to the events of the server."""

    def __init__(self):
        self.lock = threading.Lock()
        self.listeners: list[list] = []
        self.cond = threading.Condition(self.lock)

    def publish(self, event: dict) -> None:
        with self.cond:
            for queue in self.listeners:
                queue.append(event)
            self.cond.notify_all()


class Requests:
    """What the owner asks Claude to do with a block that is being edited:
    correct its English, suggest, or follow an instruction. A request is a
    line of a file that Claude listens to, and its answer comes back
    through the server and is sent to the page."""

    LISTENING_FOR = 10  # seconds a mark of the listener is good for

    def __init__(self, state: Path, hub: Hub):
        self.path = state / "requests.jsonl"
        self.heartbeat = state / "listening"
        self.hub = hub
        self.lock = threading.Lock()
        self.path.touch()
        self.count = 0
        self.answers: dict[str, dict] = {}
        self.client_log = state / "client.log"

    def add(self, body: dict) -> str:
        with self.lock:
            self.count += 1
            request = {
                "id": f"r{int(time.time())}-{self.count}",
                "time": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
                "kind": body["kind"],
                "instruction": body.get("instruction"),
                "page": body.get("page"),
                "path": body.get("path"),
                "qualname": body.get("qualname"),
                "lines": body.get("lines"),
                "text": body["text"],
            }
            with self.path.open("a") as out:
                out.write(json.dumps(request, ensure_ascii=False) + "\n")
        return request["id"]

    def answer(self, body: dict) -> None:
        """Keep the answer, for the page to ask for, and send it to the
        pages that are listening: a page gets it by whichever comes first."""
        answer = {
            "type": "answer",
            "id": body["id"],
            "kind": body["kind"],
            "text": body["text"],
        }
        self.answers[body["id"]] = answer
        self.hub.publish(answer)

    def log(self, text: str) -> None:
        """An error of the page, which only the owner's browser sees."""
        stamp = datetime.now(UTC).astimezone().isoformat(timespec="seconds")
        with self.lock, self.client_log.open("a") as out:
            out.write(f"{stamp} {text}\n")

    def listening(self) -> bool:
        try:
            age = time.time() - self.heartbeat.stat().st_mtime
        except FileNotFoundError:
            return False
        return age < self.LISTENING_FOR


def _fingerprints(project: Project) -> dict[Path, float]:
    found = {}
    for pattern in ("**/*.md", "**/*.rst", "**/*.py", "**/*.html", "**/*.css"):
        for path in project.docs.glob(pattern):
            if project.build not in path.parents:
                found[path] = path.stat().st_mtime
    for path in project.python.glob("**/*.py"):
        found[path] = path.stat().st_mtime
    return found


def watch(project: Project, hub: Hub) -> None:
    """Rebuild when a source changes, whoever changed it, and tell the
    browsers which files changed and when the site is built again."""
    seen = _fingerprints(project)
    while True:
        time.sleep(0.4)
        now = _fingerprints(project)
        changed = [p for p in now.keys() | seen.keys() if now.get(p) != seen.get(p)]
        if not changed:
            continue
        # Let a burst of saves settle before building.
        time.sleep(0.3)
        now = _fingerprints(project)
        changed = [p for p in now.keys() | seen.keys() if now.get(p) != seen.get(p)]
        seen = now
        rels = sorted(project.rel(p) for p in changed if p.exists())
        hub.publish({"type": "changed", "paths": rels})
        # A docstring, the configuration or a template can change every page,
        # and Sphinx only notices a changed page file on its own.
        full = any(p.suffix not in (".md", ".rst") for p in changed)
        hub.publish({"type": "building"})
        hub.publish({"type": "built", **project.run_build(full)})


def make_handler(project: Project, hub: Hub, requests: Requests):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):
            pass

        def handle(self):
            try:
                super().handle()
            except (BrokenPipeError, ConnectionResetError):
                pass

        def send(self, status, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, value, status=HTTPStatus.OK) -> None:
            self.send(status, json.dumps(value).encode(), "application/json")

        def send_file(self, path: Path) -> None:
            if not path.is_file():
                self.send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
                return
            kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.send(HTTPStatus.OK, path.read_bytes(), kind)

        def do_GET(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path == "/":
                self.send_file(STATIC / "index.html")
            elif url.path == "/api/source":
                self.send_json(project.sources(query.get("page", ["index"])[0]))
            elif url.path == "/api/block":
                try:
                    block = project.block(
                        query.get("page", ["index"])[0], int(query.get("n", ["-1"])[0])
                    )
                except StaleBuild:
                    self.send_json({"error": "stale"}, HTTPStatus.CONFLICT)
                except LookupError as error:
                    self.send_json({"error": str(error)}, HTTPStatus.NOT_FOUND)
                else:
                    self.send_json(block)
            elif url.path == "/api/build":
                self.send_json(project.last_build)
            elif url.path == "/api/answer":
                found = requests.answers.get(query.get("id", [""])[0])
                self.send_json(found or {"type": "waiting"})
            elif url.path == "/api/status":
                self.send_json({"listening": requests.listening()})
            elif url.path == "/api/events":
                self.events()
            elif url.path.startswith("/site/"):
                rel = url.path.removeprefix("/site/") or "index.html"
                path = (project.build / rel).resolve()
                if path.is_dir():
                    path = path / "index.html"
                if not path.is_relative_to(project.build):
                    self.send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
                else:
                    self.send_file(path)
            else:
                self.send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            url = urlparse(self.path)
            if url.path == "/api/save":
                try:
                    text = project.save(
                        body["path"], body.get("qualname"), body["base"], body["text"]
                    )
                except Conflict as conflict:
                    self.send_json(
                        {"error": str(conflict), "current": conflict.current},
                        HTTPStatus.CONFLICT,
                    )
                except (KeyError, ValueError, PermissionError) as error:
                    self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                else:
                    self.send_json({"text": text})
            elif url.path == "/api/save_block":
                try:
                    project.save_block(
                        body["path"], body.get("qualname"), body["start"],
                        body["end"], body["base"], body["text"],
                    )
                except LookupError as error:
                    self.send_json({"error": str(error)}, HTTPStatus.CONFLICT)
                except (KeyError, ValueError, PermissionError) as error:
                    self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                else:
                    self.send_json({"ok": True})
            elif url.path == "/api/requests":
                if body.get("kind") not in ("correct", "suggest", "ask"):
                    self.send_json({"error": "no such request"}, HTTPStatus.BAD_REQUEST)
                else:
                    self.send_json(
                        {"id": requests.add(body), "listening": requests.listening()}
                    )
            elif url.path == "/api/log":
                requests.log(str(body.get("text", ""))[:2000])
                self.send_json({"ok": True})
            elif url.path == "/api/answers":
                requests.answer(body)
                self.send_json({"ok": True})
            else:
                self.send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

        def events(self):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            queue: list = []
            with hub.cond:
                hub.listeners.append(queue)
            try:
                while True:
                    with hub.cond:
                        hub.cond.wait_for(lambda: queue, timeout=15)
                        pending, queue[:] = list(queue), []
                    if not pending:
                        self.wfile.write(b": keepalive\n\n")
                    for event in pending:
                        self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                with hub.cond:
                    hub.listeners.remove(queue)

    return Handler


def serve(project: Project, state: Path, host: str, port: int) -> None:
    hub = Hub()
    state.mkdir(parents=True, exist_ok=True)
    requests = Requests(state, hub)
    print(f"building {project.docs} ...", flush=True)
    result = project.run_build(full=True)
    print(
        f"built in {result['seconds']} s" if result["ok"] else result["log"],
        flush=True,
    )
    threading.Thread(target=watch, args=(project, hub), daemon=True).start()
    server = ThreadingHTTPServer((host, port), make_handler(project, hub, requests))
    server.daemon_threads = True
    print(f"doclive at http://{host}:{port}/", flush=True)
    print(f"state in {state}", flush=True)
    server.serve_forever()


def project_slug(root: Path) -> str:
    digest = hashlib.sha1(str(root).encode()).hexdigest()[:8]
    return f"{root.name}-{digest}"
