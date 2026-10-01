"""What Claude runs from its session: listen to the owner, and answer.

    python -m doclive.agent listen <state directory>
    python -m doclive.agent answer <request id> rewrite|note   < text

`listen` prints one line for each request made from a block that is being
edited, as the JSON object the server wrote, and it marks the state
directory every second, which is how the page knows that somebody is
listening. It ends when it is killed, and the mark goes stale with it.

`answer` sends the text on standard input to the server, so that no text
has to be quoted for a shell.
"""

import json
import sys
import time
import urllib.request
from pathlib import Path

HEARTBEAT = "listening"
WATCHED = ("requests.jsonl",)


def listen(state: Path) -> None:
    offsets = {}
    for name in WATCHED:
        path = state / name
        offsets[name] = path.stat().st_size if path.exists() else 0
    while True:
        (state / HEARTBEAT).touch()
        for name in WATCHED:
            path = state / name
            if not path.exists():
                continue
            size = path.stat().st_size
            if size < offsets[name]:
                offsets[name] = 0
            if size == offsets[name]:
                continue
            with path.open("rb") as fh:
                fh.seek(offsets[name])
                chunk = fh.read()
            # A line that is still being written waits for the next turn.
            complete = chunk[: chunk.rfind(b"\n") + 1]
            offsets[name] += len(complete)
            for line in complete.decode().splitlines():
                if line.strip():
                    print(line, flush=True)
        time.sleep(1)


def post(port: int, path: str, body: dict) -> None:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        json.dumps(body).encode(),
        {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        response.read()


def main(argv: list[str]) -> None:
    port = 8765
    if "--port" in argv:
        at = argv.index("--port")
        port = int(argv[at + 1])
        del argv[at : at + 2]
    match argv:
        case ["listen", state]:
            listen(Path(state).expanduser())
        case ["answer", request_id, ("rewrite" | "note") as kind]:
            text = sys.stdin.read().strip("\n")
            post(port, "/api/answers", {"id": request_id, "kind": kind, "text": text})
        case _:
            sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
