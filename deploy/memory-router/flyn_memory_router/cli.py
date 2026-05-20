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


def _cmd_conv(args, client_factory=None) -> int:
    """Dispatch `flyn-mem conv <subcmd>`."""
    from .config import Config
    from .conv.owner import OwnerRegistry
    from .conv import encrypted_raw

    cfg = Config.from_env()
    registry = OwnerRegistry(cfg.conv_owners_db_path, cfg.principals_json_path)
    viewer = os.environ.get("USER", "ryan")

    if args.conv_cmd == "health":
        return _conv_health(cfg, registry, viewer)
    if args.conv_cmd == "search":
        return _conv_search(cfg, registry, viewer, args)
    if args.conv_cmd == "thread":
        return _conv_thread(cfg, registry, viewer, args)
    if args.conv_cmd == "replay":
        return _conv_replay(cfg, registry, viewer, args, encrypted_raw)
    print(f"unknown conv subcommand: {args.conv_cmd}", file=sys.stderr)
    return 2


def _conv_health(cfg, registry, viewer) -> int:
    from .conv.schema import ConvDb
    print(f"{'owner':<12} {'messages':<10} {'oldest_ts':<22} {'newest_ts':<22} {'backlog':<8}")
    for owner_id in sorted(registry.list_accessible_owners(viewer)):
        db_path = registry.db_path_for(owner_id, cfg.conv_root)
        if not db_path.exists():
            print(f"{owner_id:<12} {'0':<10} {'-':<22} {'-':<22} {'-':<8}")
            continue
        stats = ConvDb(owner_id, db_path).stats()
        print(f"{owner_id:<12} {stats['messages']:<10} {stats['oldest_ts'] or '-':<22} "
              f"{stats['newest_ts'] or '-':<22} {stats['summary_backlog']:<8}")
    return 0


def _conv_search(cfg, registry, viewer, args) -> int:
    from .conv.schema import ConvDb
    owners = [args.owner] if args.owner else sorted(registry.list_accessible_owners(viewer))
    n = 0
    for owner_id in owners:
        db_path = registry.db_path_for(owner_id, cfg.conv_root)
        if not db_path.exists():
            continue
        for hit in ConvDb(owner_id, db_path).search(args.q, top_k=args.top):
            n += 1
            print(f"\n┌ {hit.ts} · {hit.sender_id} · {owner_id} · row {hit.row_id}")
            print(f"│   {hit.body[:300]}")
            if hit.summary:
                print(f"└ summary: {hit.summary}")
            else:
                print(f"└ summary: (pending)")
        if owner_id != viewer:
            registry.append_audit(viewer, owner_id, op="read", q=args.q)
    print(f"\n{n} hits")
    return 0


def _conv_thread(cfg, registry, viewer, args) -> int:
    from .conv.schema import ConvDb
    owners = [args.owner] if args.owner else sorted(registry.list_accessible_owners(viewer))
    for owner_id in owners:
        db_path = registry.db_path_for(owner_id, cfg.conv_root)
        if not db_path.exists():
            continue
        for msg in ConvDb(owner_id, db_path).get_by_thread(args.thread_id, limit=args.limit):
            print(f"{msg.ts}  {msg.sender_id}: {msg.body[:200]}")
    return 0


def _conv_replay(cfg, registry, viewer, args, encrypted_raw) -> int:
    from .conv.schema import ConvDb
    owner = args.owner or viewer
    if not registry.viewer_can_read(viewer, owner):
        print(f"flyn-mem: viewer {viewer!r} lacks grant to read owner {owner!r}", file=sys.stderr)
        return 3
    db_path = registry.db_path_for(owner, cfg.conv_root)
    if not db_path.exists():
        print(f"flyn-mem: no DB for owner {owner!r}", file=sys.stderr)
        return 1
    msg = ConvDb(owner, db_path).get_by_id(args.row_id)
    if msg is None:
        print(f"flyn-mem: no row {args.row_id} in owner {owner!r}", file=sys.stderr)
        return 1
    try:
        plaintext = encrypted_raw.unseal(msg.encrypted_raw, owner)
    except Exception as exc:
        print(f"flyn-mem: unseal failed: {exc}", file=sys.stderr)
        return 1
    registry.append_audit(viewer, owner, op="replay", q=str(args.row_id))
    print(plaintext.decode("utf-8", errors="replace"))
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

    # conv subcommand cluster (Telegram slice 1)
    conv_p = sub.add_parser("conv", help="Conversation tier (Telegram messages)")
    conv_sub = conv_p.add_subparsers(dest="conv_cmd", required=True)

    conv_sub.add_parser("health", help="Per-owner DB stats")

    s = conv_sub.add_parser("search", help="FTS5 search in conv DBs")
    s.add_argument("q", help="search text")
    s.add_argument("--top", type=int, default=10)
    s.add_argument("--owner", default=None)

    t = conv_sub.add_parser("thread", help="Dump a thread's recent messages")
    t.add_argument("thread_id")
    t.add_argument("--limit", type=int, default=20)
    t.add_argument("--owner", default=None)

    r = conv_sub.add_parser("replay", help="Decrypt + print raw payload (audit-logged)")
    r.add_argument("row_id", type=int)
    r.add_argument("--owner", default=None)

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
        "conv": _cmd_conv,
    }
    fn = dispatch.get(args.cmd)
    if fn is None:
        parser.print_help()
        return 2
    return fn(args, cf)


if __name__ == "__main__":
    sys.exit(main())
