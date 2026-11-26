"""Serve the landing page locally with browser caching turned off.

    python3 web/serve.py            # http://127.0.0.1:8765/
    python3 web/serve.py --port 9000 --bind 0.0.0.0

Plain `python3 -m http.server` sends no cache headers, so after you edit the page a browser
can keep using older copies of styles.css and the JavaScript modules while loading the new
index.html, which shows a broken mix of old and new. This server marks every response
`Cache-Control: no-store`, so a normal reload always gets the current files.
"""

import argparse
import contextlib
import functools
import http.server
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--bind", default="127.0.0.1")
    args = parser.parse_args()
    handler = functools.partial(NoCacheHandler, directory=str(ROOT))
    with http.server.ThreadingHTTPServer((args.bind, args.port), handler) as server:
        print(f"Serving {ROOT} at http://{args.bind}:{args.port}/ (no caching). Ctrl+C to stop.")
        with contextlib.suppress(KeyboardInterrupt):
            server.serve_forever()


if __name__ == "__main__":
    main()
