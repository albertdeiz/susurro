"""Local HTTP service (127.0.0.1) that receives text from the shortcut and hands it to the player.

POST /speak   body = UTF-8 text (optional ?mode=read|narrate|podcast&lang=auto|es|en|pt)
POST /stop
GET  /status
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import PORT
from .config import LANGUAGES, MODES, Settings
from .player import Player

MAX_CHARS = 60_000


def serve(player: Player, get_settings: Callable[[], Settings]) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # quiet
            pass

        def _reply(self, code: int, body: dict) -> None:
            data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if urlparse(self.path).path == "/status":
                self._reply(200, {"status": player.status, "language": player.language})
            else:
                self._reply(404, {"error": "not found"})

        def do_POST(self) -> None:
            url = urlparse(self.path)
            if url.path == "/stop":
                player.stop()
                self._reply(200, {"ok": True})
                return
            if url.path != "/speak":
                self._reply(404, {"error": "not found"})
                return
            n = int(self.headers.get("Content-Length") or 0)
            text = self.rfile.read(min(n, MAX_CHARS * 4)).decode("utf-8", "replace")[:MAX_CHARS]
            s = get_settings()
            query = parse_qs(url.query)
            mode = query.get("mode", [None])[0]
            lang = query.get("lang", [None])[0]
            if mode in MODES:
                s = replace(s, mode=mode)
            if lang in ("auto", *LANGUAGES):
                s = replace(s, language=lang)
            player.speak(text, s)
            self._reply(200, {"ok": True, "mode": s.mode, "chars": len(text)})

    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd
