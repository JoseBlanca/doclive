"""The local server: the rendered site, the sources behind each page, the
saves, the rebuilds, and the messages between the owner and Claude.

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

from doclive.docstrings import find_docstrings, replace_docstring

STATIC = Path(__file__).parent / "static"
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

    # --- The build ---

    def run_build(self, full: bool) -> dict:
        with self.build_lock:
            started = time.monotonic()
            args = [str(self.sphinx), "-q", str(self.docs), str(self.build)]
            if full:
                args.insert(1, "-E")
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


class Messages:
    """The conversation, one JSON object per line in a file that Claude
    reads and writes as well."""

    def __init__(self, path: Path, hub: Hub):
        self.path = path
        self.hub = hub
        self.lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        self.offset = path.stat().st_size

    def all(self) -> list[dict]:
        return [json.loads(line) for line in self.path.read_text().splitlines() if line]

    def add(self, author: str, text: str, context: dict | None) -> dict:
        message = {
            "time": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
            "from": author,
            "text": text,
            "context": context or {},
        }
        with self.lock, self.path.open("a") as out:
            out.write(json.dumps(message, ensure_ascii=False) + "\n")
        return message

    def poll(self) -> None:
        """Publish the lines added to the file since the last poll, whoever
        wrote them."""
        size = self.path.stat().st_size
        if size <= self.offset:
            self.offset = min(self.offset, size)
            return
        with self.path.open() as fh:
            fh.seek(self.offset)
            chunk = fh.read()
        self.offset = size
        for line in chunk.splitlines():
            if line.strip():
                self.hub.publish({"type": "message", "message": json.loads(line)})


def _fingerprints(project: Project) -> dict[Path, float]:
    found = {}
    for pattern in ("**/*.md", "**/*.rst", "**/*.py", "**/*.html", "**/*.css"):
        for path in project.docs.glob(pattern):
            if project.build not in path.parents:
                found[path] = path.stat().st_mtime
    for path in project.python.glob("**/*.py"):
        found[path] = path.stat().st_mtime
    return found


def watch(project: Project, hub: Hub, messages: Messages) -> None:
    """Rebuild when a source changes, whoever changed it, and tell the
    browsers which files changed and when the site is built again."""
    seen = _fingerprints(project)
    while True:
        time.sleep(0.4)
        messages.poll()
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


def make_handler(project: Project, hub: Hub, messages: Messages):
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
            elif url.path == "/api/messages":
                self.send_json(messages.all())
            elif url.path == "/api/build":
                self.send_json(project.last_build)
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
            elif url.path == "/api/messages":
                author = body.get("from", "owner")
                message = messages.add(author, body["text"], body.get("context"))
                self.send_json(message)
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


def serve(
    project: Project, messages_path: Path, host: str, port: int
) -> None:
    hub = Hub()
    messages = Messages(messages_path, hub)
    print(f"building {project.docs} ...", flush=True)
    result = project.run_build(full=True)
    print(
        f"built in {result['seconds']} s" if result["ok"] else result["log"],
        flush=True,
    )
    threading.Thread(target=watch, args=(project, hub, messages), daemon=True).start()
    server = ThreadingHTTPServer((host, port), make_handler(project, hub, messages))
    server.daemon_threads = True
    print(f"doclive at http://{host}:{port}/", flush=True)
    print(f"messages in {messages_path}", flush=True)
    server.serve_forever()


def project_slug(root: Path) -> str:
    digest = hashlib.sha1(str(root).encode()).hexdigest()[:8]
    return f"{root.name}-{digest}"
