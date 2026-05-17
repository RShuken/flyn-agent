"""flyn-mem CLI — wraps the local MemoryRouter REST endpoint."""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Callable

import httpx


def _default_client_factory() -> Callable[[], httpx.Client]:
    port = os.environ.get("FLYN_MEMORY_ROUTER_PORT", "8400")
    base = f"http://localhost:{port}"
    return lambda: httpx.Client(base_url=base, timeout=10.0)


def _connect_error(e: httpx.ConnectError) -> int:
    print(f"flyn-mem: cannot reach memory router ({e})", file=sys.stderr)
    print("  Service running? Try:", file=sys.stderr)
    print("    launchctl print gui/$(id -u)/ai.flyn.memory-router", file=sys.stderr)
    print("  Or restart:", file=sys.stderr)
    print("    launchctl kickstart -k gui/$(id -u)/ai.flyn.memory-router", file=sys.stderr)
    return 2


def _cmd_query(args, client_factory) -> int:
    payload = {"q": args.q, "top_k": args.top}
    if args.include:
        payload["include"] = args.include
    if args.exclude:
        payload["exclude"] = args.exclude
    try:
        with client_factory() as c:
            r = c.post("/api/memory/query", json=payload)
            r.raise_for_status()
            data = r.json()
    except httpx.ConnectError as e:
        return _connect_error(e)
    except httpx.HTTPStatusError as e:
        print(f"flyn-mem: server error: {e.response.status_code} {e.response.text}", file=sys.stderr)
        return 1

    if args.json_out:
        print(json.dumps(data, indent=2))
        return 0
    print(f"query_id: {data['query_id']}   elapsed: {data['elapsed_ms']}ms")
    print()
    for i, hit in enumerate(data.get("hits", []), start=1):
        print(f"{i}. [{hit['source']}] score={hit['score']:.4f}")
        print(f"   {hit['text'][:300].strip()}")
        print()
    for err in data.get("source_errors", []):
        print(f"  (source {err['source']} {err['error_class']}: {err.get('message', '')})",
              file=sys.stderr)
    return 0


def _cmd_health(args, client_factory) -> int:
    try:
        with client_factory() as c:
            h = c.get("/api/health").json()
            srcs = c.get("/api/memory/sources").json()
    except httpx.ConnectError as e:
        return _connect_error(e)
    print(f"flyn-memory-router: {'OK' if h.get('ok') else 'DEGRADED'} (port {h.get('port')})")
    print()
    print(f"{'source':<14} {'default':<8} {'last_ms':<10} {'error_rate'}")
    for s in srcs:
        print(f"{s['name']:<14} "
              f"{'yes' if s.get('default_included') else 'no':<8} "
              f"{str(s.get('last_elapsed_ms') or '-'):<10} "
              f"{s.get('error_rate_100q', 0.0)}")
    return 0


def _cmd_sources(args, client_factory) -> int:
    try:
        with client_factory() as c:
            srcs = c.get("/api/memory/sources").json()
    except httpx.ConnectError as e:
        return _connect_error(e)
    print(json.dumps(srcs, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="flyn-mem")
    sub = p.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("query", help="run a cross-source memory query")
    q.add_argument("q")
    q.add_argument("--top", type=int, default=10)
    q.add_argument("--include", nargs="*", default=None)
    q.add_argument("--exclude", nargs="*", default=None)
    q.add_argument("--json", dest="json_out", action="store_true")
    sub.add_parser("health", help="overall + per-source health")
    sub.add_parser("sources", help="full sources registry (JSON)")
    return p


def main(argv: list[str] | None = None,
         client_factory: Callable[[], httpx.Client] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cf = client_factory or _default_client_factory()
    dispatch = {
        "query": _cmd_query,
        "health": _cmd_health,
        "sources": _cmd_sources,
    }
    fn = dispatch.get(args.cmd)
    if fn is None:
        parser.print_help()
        return 2
    return fn(args, cf)


if __name__ == "__main__":
    sys.exit(main())
