"""Cohort CLI entry point.

Usage::

    python -m cohort serve                  # start HTTP server
    python -m cohort serve --port 8080      # custom port
    python -m cohort serve --data-dir /tmp  # custom data directory
"""

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="cohort -- multi-agent orchestration")
    sub = parser.add_subparsers(dest="command")

    serve_parser = sub.add_parser("serve", help="Start the HTTP server")
    serve_parser.add_argument("--host", default="0.0.0.0", help="Bind address")
    serve_parser.add_argument("--port", type=int, default=5100, help="Port")
    serve_parser.add_argument("--data-dir", default="data", help="Data directory")

    args = parser.parse_args()

    if args.command == "serve":
        from cohort.server import serve

        print(f"[*] cohort server starting on {args.host}:{args.port}")
        print(f"[*] data dir: {args.data_dir}")
        serve(host=args.host, port=args.port, data_dir=args.data_dir)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
