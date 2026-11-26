"""Serve the AstroIdentify website locally (landing page + image analysis).

    python3 web/serve.py                       # http://127.0.0.1:8765/
    python3 web/serve.py --port 9000 --bind 0.0.0.0

This is a shortcut for ``astroidentify serve`` (run it with the project's virtual
environment, where the ``astroidentify`` package is installed). Every response is sent with
``Cache-Control: no-store``, so a normal reload always gets the current files.

If the package isn't importable (e.g. plain system Python), the static pages are still served,
without the analysis API, so the landing page can be previewed on its own.
"""

import argparse
import contextlib
import functools
import http.server
import sys
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
    args, rest = parser.parse_known_args()
    try:
        from astroidentify.cli import main as cli_main
    except ImportError:
        print(
            "astroidentify isn't importable here, so only the static pages are served (no image "
            "analysis). Use the project's virtual environment: .venv/bin/python web/serve.py",
            file=sys.stderr,
        )
    else:
        sys.exit(cli_main(["serve", "--host", args.bind, "--port", str(args.port), *rest]))

    handler = functools.partial(NoCacheHandler, directory=str(ROOT))
    with http.server.ThreadingHTTPServer((args.bind, args.port), handler) as server:
        print(f"Serving {ROOT} at http://{args.bind}:{args.port}/ (static only). Ctrl+C to stop.")
        with contextlib.suppress(KeyboardInterrupt):
            server.serve_forever()


if __name__ == "__main__":
    main()
