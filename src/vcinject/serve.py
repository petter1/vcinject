"""Serve CIAs over the local network for FBI's "Remote Install -> Scan QR Code".

FBI reads a QR code containing one URL per line and downloads each CIA over
plain HTTP, so we only need a tiny static file server on the LAN.
"""
from __future__ import annotations

import errno
import io
import re
import shutil
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import quote

import segno

from .errors import InjectError

DEFAULT_PORT = 8080


def lan_ip() -> str:
    """The address other devices on the LAN can reach us at."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("10.255.255.255", 1))  # no packet is sent for UDP connect
            return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def _url_name(index: int, path: Path) -> str:
    # Keep URLs plain ASCII so the QR code stays small and FBI parses them.
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem).strip("_") or "title"
    return f"{index}/{quote(stem)}.cia"


class CiaServer:
    """Serves a fixed set of files; anything else gets a 404."""

    def __init__(self, files: list[Path], host: str | None = None, port: int = DEFAULT_PORT,
                 log: Callable[[str], None] = lambda msg: print(msg, flush=True)):
        if not files:
            raise InjectError("Nothing to serve.")
        for path in files:
            if not Path(path).is_file():
                raise InjectError(f"File not found: {path}")
        self.host = host or lan_ip()
        self._routes = {"/" + _url_name(i, Path(p)): Path(p) for i, p in enumerate(files)}
        self._log = log
        self._httpd = self._bind(port)
        self._thread: threading.Thread | None = None

    def _bind(self, port: int) -> ThreadingHTTPServer:
        handler = self._handler()
        try:
            return ThreadingHTTPServer(("0.0.0.0", port), handler)
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE or port == 0:
                raise InjectError(f"Could not start the server: {exc}") from exc
            return ThreadingHTTPServer(("0.0.0.0", 0), handler)  # any free port

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    @property
    def urls(self) -> list[str]:
        return [f"http://{self.host}:{self.port}{route}" for route in self._routes]

    def qr(self) -> segno.QRCode:
        return segno.make("\n".join(self.urls), error="m", micro=False)

    def qr_png(self, scale: int = 6) -> bytes:
        buffer = io.BytesIO()
        self.qr().save(buffer, kind="png", scale=scale, border=2)
        return buffer.getvalue()

    def start(self) -> "CiaServer":
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def serve_forever(self) -> None:
        self._httpd.serve_forever()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def _handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_HEAD(self):
                self._send(body=False)

            def do_GET(self):
                self._send(body=True)

            def _send(self, body: bool):
                path = server._routes.get(self.path.split("?", 1)[0])
                if path is None:
                    self.send_error(404)
                    return
                size = path.stat().st_size
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(size))
                self.end_headers()
                if not body:
                    return
                server._log(f"{self.client_address[0]} is downloading {path.name} "
                            f"({size / 1024 / 1024:.1f} MiB)")
                try:
                    with path.open("rb") as f:
                        shutil.copyfileobj(f, self.wfile, 1024 * 1024)
                except (BrokenPipeError, ConnectionResetError):
                    server._log(f"{self.client_address[0]} aborted the download of {path.name}")
                    return
                server._log(f"{self.client_address[0]} finished downloading {path.name}")

            def log_message(self, *_args):  # silence default stderr access log
                pass

        return Handler
