"""A small HTTP server for the altar. Local only, no framework, no build step."""
from __future__ import annotations

import json
import mimetypes
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import keepsake
from .ground import Ghost
from .store import MAX_CARD_CHARS, Altar

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
SAMPLES = ROOT / "samples"


def _samples() -> list[dict]:
    found = []
    for path in sorted(SAMPLES.glob("*.json")):
        try:
            data = json.loads(path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        found.append(
            {
                "id": data.get("id", path.stem),
                "label": data.get("label", path.stem),
                "blurb": data.get("blurb", ""),
                "cards": len(data.get("cards", [])),
                "person": data.get("person", {}),
            }
        )
    return found


class AltarApp:
    def __init__(self, data_path: Path, backend=None, verbose: bool = False,
                 sample: str | None = None):
        self.data_path = Path(data_path)
        self.backend = backend
        self.verbose = verbose
        self.lock = threading.Lock()
        if sample:
            self.altar = Altar.from_sample(SAMPLES / f"{sample}.json", self.data_path)
        else:
            self.altar = Altar.load(self.data_path)
        self.ghost = Ghost(self.altar, backend)

    def reset_ghost(self) -> None:
        self.ghost = Ghost(self.altar, self.backend)

    def state(self) -> dict:
        payload = self.altar.public()
        by_id = {card["id"]: card for card in self.altar.cards}
        pairs = []
        for pair in self.ghost.disagreements():
            a, b = by_id.get(pair["a"]), by_id.get(pair["b"])
            if a and b:
                pairs.append({
                    "word": pair["word"],
                    "a": {"id": a["id"], "text": a["text"], "by": a.get("by", "")},
                    "b": {"id": b["id"], "text": b["text"], "by": b.get("by", "")},
                })
        payload.update(
            {
                "backend": self.backend.describe() if self.backend else {
                    "name": "none", "model": "no model", "online": False
                },
                "stats": self.ghost.stats_payload(),
                "suggestions": self.ghost.suggestions(),
                "disagreements": pairs,
                "altars": _samples(),
                "limits": {"max_cards": 24, "max_card_chars": MAX_CARD_CHARS},
            }
        )
        return payload


def make_handler(app: AltarApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ofrenda/1.0"

        # -- plumbing --------------------------------------------------------

        def log_message(self, fmt: str, *args) -> None:  # quieter than the default
            if app.verbose:
                super().log_message(fmt, *args)

        def _send(self, status: int, body: bytes, content_type: str,
                  extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command == "HEAD":
                return
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                # the browser navigated away or gave up. nobody to tell.
                pass

        def _json(self, payload: dict, status: int = 200) -> None:
            self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _fail(self, message: str, status: int = 400) -> None:
            self._json({"error": message}, status)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                raise ValueError("that request was not JSON")
            if not isinstance(data, dict):
                raise ValueError("that request was not a JSON object")
            return data

        def _log(self, label: str, started: float, extra: str = "") -> None:
            print(f"[ofrenda] {label:<16} {time.time() - started:5.1f}s  {extra}".rstrip(),
                  flush=True)

        # -- routes ----------------------------------------------------------

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            if path == "/api/state":
                return self._json(app.state())
            if path == "/api/health":
                online = bool(app.backend and app.backend.health())
                return self._json({"ok": True, "model": online,
                                   "backend": app.backend.describe() if app.backend else None})
            if path == "/api/altars":
                return self._json({"altars": _samples()})
            if path in ("/", "/index.html"):
                return self._file(WEB / "index.html")
            return self._file(WEB / path.lstrip("/"))

        def do_DELETE(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/api/cards":
                return self._fail("no such endpoint", 404)
            card_id = urllib.parse.parse_qs(parsed.query).get("id", [""])[0]
            with app.lock:
                removed = app.altar.remove_card(card_id)
                app.reset_ghost()
            if not removed:
                return self._fail("that card is not on the altar", 404)
            return self._json(app.state())

        def do_POST(self) -> None:
            path = urllib.parse.urlparse(self.path).path
            try:
                body = self._body()
            except ValueError as exc:
                return self._fail(str(exc))

            if path == "/api/ask":
                question = (body.get("question") or "").strip()
                if len(question) < 2:
                    return self._fail("ask something")
                started = time.time()
                with app.lock:
                    turn = app.ghost.ask(question)
                self._log("ask", started, f"{turn.source} {turn.tokens}tok")
                return self._json({"turn": turn.payload(), "stats": app.ghost.stats_payload()})

            if path == "/api/night/open":
                started = time.time()
                with app.lock:
                    app.reset_ghost()
                    app.altar.open_night()
                    turn = app.ghost.open_night()
                self._log("open night", started, turn.source)
                return self._json({"turn": turn.payload(), "stats": app.ghost.stats_payload()})

            if path == "/api/night/close":
                started = time.time()
                with app.lock:
                    turn = app.ghost.close_night()
                    doc = keepsake.as_html(
                        app.altar, keepsake.DEDICATION, _model_label(app), closing=turn.say
                    )
                self._log("close night", started, turn.source)
                payload = {
                    "turn": turn.payload(),
                    "keepsake": {
                        "html": doc,
                        "filename": f"recuerdo-{_slug(app)}.html",
                        "text": keepsake.as_text(
                            app.altar, keepsake.DEDICATION, _model_label(app), closing=turn.say
                        ),
                    },
                    "stats": app.ghost.stats_payload(),
                }
                return self._json(payload)

            if path == "/api/cards":
                try:
                    with app.lock:
                        card = app.altar.add_card(
                            body.get("text", ""), body.get("by", ""), body.get("year", "")
                        )
                        app.reset_ghost()
                except ValueError as exc:
                    return self._fail(str(exc))
                return self._json({"card": card, "state": app.state()})

            if path == "/api/person":
                with app.lock:
                    app.altar.set_person(body)
                    app.reset_ghost()
                return self._json(app.state())

            if path == "/api/altars":
                altars = {a["id"]: a for a in _samples()}
                wanted = body.get("id", "")
                if wanted not in altars:
                    return self._fail("unknown altar")
                with app.lock:
                    app.altar = Altar.from_sample(SAMPLES / f"{wanted}.json", app.data_path)
                    app.reset_ghost()
                return self._json(app.state())

            return self._fail("no such endpoint", 404)

        def do_HEAD(self) -> None:
            self.do_GET()

        # -- files -----------------------------------------------------------

        def _file(self, path: Path) -> None:
            try:
                resolved = path.resolve()
                resolved.relative_to(WEB.resolve())
            except (ValueError, OSError):
                return self._fail("not found", 404)
            if not resolved.is_file():
                return self._fail("not found", 404)
            guessed = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
            if guessed.startswith("text/") or guessed.endswith("javascript"):
                guessed += "; charset=utf-8"
            self._send(200, resolved.read_bytes(), guessed)

    return Handler


def _model_label(app: AltarApp) -> str:
    if not app.backend:
        return "no model"
    return app.backend.model_label


def _slug(app: AltarApp) -> str:
    name = app.altar.person.get("name") or "altar"
    from .store import slugify
    return slugify(name)


def serve(app: AltarApp, host: str = "0.0.0.0", port: int = 7777) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    httpd.daemon_threads = True
    return httpd


def free_port(start: int = 7777) -> int:
    for port in range(start, start + 60):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    raise RuntimeError("no free port nearby")
