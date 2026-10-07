#!/usr/bin/env python3
"""Minimal HTTP exchange for guests without 9p (FreeBSD 14).

GET  /<file>        serves files from SERVE_DIR (e.g. the Mocinha tarball, scripts)
PUT  /logs/<name>   stores an upload in LOG_DIR (flat names only)

    http_exchange.py PORT SERVE_DIR LOG_DIR
"""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import sys


class ExchangeHandler(SimpleHTTPRequestHandler):
    log_dir: Path = Path(".")

    def do_PUT(self) -> None:
        m = re.fullmatch(r"/logs/([A-Za-z0-9._-]+)", self.path)
        if not m:
            self.send_error(403, "uploads go to /logs/<flat-name>")
            return
        length = int(self.headers.get("Content-Length", "0"))
        (self.log_dir / m.group(1)).write_bytes(self.rfile.read(length))
        self.send_response(201)
        self.end_headers()


def main() -> None:
    port, serve_dir, log_dir = int(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    log_dir.mkdir(parents=True, exist_ok=True)
    ExchangeHandler.log_dir = log_dir
    ThreadingHTTPServer(("127.0.0.1", port), partial(ExchangeHandler, directory=serve_dir)).serve_forever()


if __name__ == "__main__":
    main()
