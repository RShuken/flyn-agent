"""flyn-mem CLI — wraps the local MemoryRouter REST endpoint."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time as _time
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

    def _attempt(client):
        r = client.post("/api/memory/query", json=payload)
        r.raise_for_status()
        return r.json()

    try:
        with client_factory() as c:
            try:
                data = _attempt(c)
            except httpx.HTTPStatusError as e:
                if e.response.status_code >= 500:
                    _time.sleep(0.5)
                    data = _attempt(c)
                else:
                    raise
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


def _cmd_logs(args, client_factory) -> int:
    import datetime
    from .config import Config
    log_dir = Config.from_env().log_dir
    if args.query_id:
        _dump_correlated(log_dir, args.query_id)
        return 0
    today_q = log_dir / f"query-{datetime.date.today().isoformat()}.jsonl"
    if not today_q.exists():
        print("(no log for today)")
        return 0
    lines = today_q.read_text().splitlines()
    for line in lines[-args.tail:]:
        rec = json.loads(line)
        if args.grep and args.grep.lower() not in line.lower():
            continue
        if args.errors and not any(v.get("error") for v in rec.get("per_source", {}).values()):
            continue
        print(f"{rec.get('ts', '')}  {rec['query_id']}  {rec['total_elapsed_ms']}ms  {rec['q']}")
    return 0


def _dump_correlated(log_dir, query_id: str) -> None:
    print(f"=== query {query_id} ===")
    for f in sorted(log_dir.glob("query-*.jsonl")):
        for line in f.read_text().splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("query_id") == query_id:
                print(json.dumps(rec, indent=2))
    print(f"=== errors for {query_id} ===")
    for f in sorted(log_dir.glob("source-errors-*.jsonl")):
        for line in f.read_text().splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("query_id") == query_id:
                print(json.dumps(rec, indent=2))


def _cmd_sources(args, client_factory) -> int:
    try:
        with client_factory() as c:
            srcs = c.get("/api/memory/sources").json()
    except httpx.ConnectError as e:
        return _connect_error(e)
    print(json.dumps(srcs, indent=2))
    return 0


def _cmd_ingest(args, client_factory) -> int:
    try:
        payload = json.loads(args.event_json)
    except json.JSONDecodeError as e:
        print(f"flyn-mem: invalid JSON event: {e}", file=sys.stderr)
        return 1
    try:
        with client_factory() as c:
            r = c.post("/api/memory/ingest", json=payload)
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
    print(f"accepted={data.get('accepted')} deduped={data.get('deduped')} importance={data.get('importance')}")
    print(f"tiers_written={', '.join(data.get('tiers_written', []))}")
    for n in data.get("notes", []):
        print(f"  note: {n}")
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
    ig = sub.add_parser("ingest", help="POST a memory event to /api/memory/ingest")
    ig.add_argument("event_json", help="JSON-encoded event payload (InboundEvent shape)")
    ig.add_argument("--json", dest="json_out", action="store_true")
    lg = sub.add_parser("logs", help="tail query log")
    lg.add_argument("--query-id", dest="query_id", default=None)
    lg.add_argument("--grep", default=None)
    lg.add_argument("--errors", action="store_true")
    lg.add_argument("--tail", type=int, default=20)
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
        "ingest": _cmd_ingest,
        "logs": _cmd_logs,
    }
    fn = dispatch.get(args.cmd)
    if fn is None:
        parser.print_help()
        return 2
    return fn(args, cf)


if __name__ == "__main__":
    sys.exit(main())
