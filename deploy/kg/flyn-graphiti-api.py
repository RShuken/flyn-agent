"""Flyn's Graphiti REST API — Edge-style local service.

Flask REST on localhost:8100 wrapping graphiti-core + Neo4j.
Agent reaches it via `curl` from the exec/shell tool (no MCP needed).

Endpoints:
  GET  /api/health             - liveness + backend connectivity
  POST /api/episode            - add an episode (body: {body, name?, source?, valid_at?})
  GET  /api/search?q=...       - semantic search over facts (edges)
  GET  /api/nodes?q=...        - entity node search
  GET  /api/episodes?limit=N   - recent episodes
  GET  /api/temporal?q=...&from=ISO&to=ISO  - temporal-filtered fact search

Secrets read from ~/.openclaw/agents/main/agent/auth-profiles.json
(same pattern as flyn-graphiti-launch.sh).
"""
import asyncio
import json
import logging
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request
from graphiti_core import Graphiti
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
from graphiti_core.embedder.gemini import GeminiEmbedder, GeminiEmbedderConfig
from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - flyn-graphiti-api - %(levelname)s - %(message)s",
)
log = logging.getLogger("flyn-graphiti-api")

AUTH_FILE = Path.home() / ".openclaw/agents/main/agent/auth-profiles.json"
GROUP_ID = "flyn"


def load_auth():
    with open(AUTH_FILE) as f:
        data = json.load(f)
    profs = data["profiles"]
    return {
        "neo4j_uri": profs["neo4j:default"]["uri"],
        "neo4j_user": profs["neo4j:default"]["user"],
        "neo4j_pass": profs["neo4j:default"]["token"],
        "gemini_key": profs["google:default"]["token"],
    }


def build_graphiti():
    auth = load_auth()
    llm_cfg = LLMConfig(
        api_key="ollama",
        model="gemma4:e4b",
        small_model="gemma4:e4b",
        base_url="http://localhost:11434/v1",
    )
    return Graphiti(
        auth["neo4j_uri"],
        auth["neo4j_user"],
        auth["neo4j_pass"],
        llm_client=OpenAIGenericClient(config=llm_cfg),
        embedder=GeminiEmbedder(
            config=GeminiEmbedderConfig(
                api_key=auth["gemini_key"],
                embedding_model="gemini-embedding-001",
            )
        ),
        cross_encoder=OpenAIRerankerClient(config=llm_cfg),
    )


_loop = asyncio.new_event_loop()
_graphiti = None
_init_lock = threading.Lock()


def _loop_runner():
    asyncio.set_event_loop(_loop)
    _loop.run_forever()


threading.Thread(target=_loop_runner, daemon=True).start()


def run_async(coro, timeout=600):
    return asyncio.run_coroutine_threadsafe(coro, _loop).result(timeout=timeout)


def ensure_graphiti():
    global _graphiti
    with _init_lock:
        if _graphiti is None:
            log.info("initializing Graphiti (one-time)")
            _graphiti = build_graphiti()
            run_async(_graphiti.build_indices_and_constraints())
            log.info("Graphiti ready")
    return _graphiti


app = Flask(__name__)


@app.get("/api/health")
def health():
    try:
        g = ensure_graphiti()
        # Quick Neo4j round-trip
        async def ping():
            async with g.driver.session() as s:
                r = await s.run("RETURN 1 AS ok")
                rec = await r.single()
                return rec["ok"] == 1
        ok = run_async(ping())
        return jsonify({"status": "ok" if ok else "degraded", "neo4j": "connected" if ok else "error", "group": GROUP_ID})
    except Exception as e:
        log.exception("health failed")
        return jsonify({"status": "error", "error": str(e)}), 500


@app.post("/api/episode")
def add_episode():
    body = request.get_json(force=True) or {}
    episode_body = body.get("body") or body.get("episode_body")
    if not episode_body:
        return jsonify({"error": "body (episode text) required"}), 400
    name = body.get("name") or f"flyn-{datetime.now(timezone.utc).isoformat()}"
    source_description = body.get("source") or "flyn-graphiti-api"
    valid_at = body.get("valid_at")
    ref_time = (
        datetime.fromisoformat(valid_at.replace("Z", "+00:00"))
        if valid_at
        else datetime.now(timezone.utc)
    )
    try:
        g = ensure_graphiti()
        run_async(
            g.add_episode(
                name=name,
                episode_body=episode_body,
                source_description=source_description,
                reference_time=ref_time,
                group_id=GROUP_ID,
            )
        )
        return jsonify({"ok": True, "name": name, "reference_time": ref_time.isoformat()})
    except Exception as e:
        log.exception("add_episode failed")
        return jsonify({"error": str(e) or type(e).__name__, "error_type": type(e).__name__}), 500


def _serialize_edge(r):
    return {
        "uuid": getattr(r, "uuid", None),
        "name": getattr(r, "name", None),
        "fact": getattr(r, "fact", None),
        "source_node_uuid": getattr(r, "source_node_uuid", None),
        "target_node_uuid": getattr(r, "target_node_uuid", None),
        "valid_at": getattr(r, "valid_at", None).isoformat() if getattr(r, "valid_at", None) else None,
        "invalid_at": getattr(r, "invalid_at", None).isoformat() if getattr(r, "invalid_at", None) else None,
        "created_at": getattr(r, "created_at", None).isoformat() if getattr(r, "created_at", None) else None,
        "episodes": getattr(r, "episodes", None),
    }


@app.get("/api/search")
def search_facts():
    q = request.args.get("q")
    if not q:
        return jsonify({"error": "q (query) required"}), 400
    try:
        g = ensure_graphiti()
        results = run_async(g.search(q, group_ids=[GROUP_ID]))
        return jsonify({
            "query": q,
            "count": len(results),
            "results": [_serialize_edge(r) for r in results],
        })
    except Exception as e:
        log.exception("search failed")
        return jsonify({"error": str(e)}), 500


@app.get("/api/temporal")
def temporal_search():
    q = request.args.get("q")
    frm = request.args.get("from")
    to = request.args.get("to")
    if not q:
        return jsonify({"error": "q required"}), 400
    try:
        g = ensure_graphiti()
        all_results = run_async(g.search(q, group_ids=[GROUP_ID]))
        filtered = []
        frm_dt = datetime.fromisoformat(frm.replace("Z", "+00:00")) if frm else None
        to_dt = datetime.fromisoformat(to.replace("Z", "+00:00")) if to else None
        for r in all_results:
            va = getattr(r, "valid_at", None)
            if va is None:
                continue
            if frm_dt and va < frm_dt:
                continue
            if to_dt and va > to_dt:
                continue
            filtered.append(r)
        return jsonify({
            "query": q, "from": frm, "to": to,
            "count": len(filtered),
            "results": [_serialize_edge(r) for r in filtered],
        })
    except Exception as e:
        log.exception("temporal search failed")
        return jsonify({"error": str(e)}), 500


def _coerce(v):
    # Flask's default serializer can't handle Neo4j DateTime etc.
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _coerce(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_coerce(x) for x in v]
    return v


@app.get("/api/episodes")
def list_episodes():
    limit = int(request.args.get("limit", "10"))
    try:
        g = ensure_graphiti()
        async def q():
            async with g.driver.session() as s:
                r = await s.run(
                    "MATCH (e:Episodic) WHERE e.group_id = $gid RETURN e ORDER BY e.created_at DESC LIMIT $limit",
                    gid=GROUP_ID, limit=limit,
                )
                return [dict(rec["e"]) for rec in await r.data()]
        eps = run_async(q())
        return jsonify({"count": len(eps), "episodes": [_coerce(e) for e in eps]})
    except Exception as e:
        log.exception("list episodes failed")
        return jsonify({"error": str(e) or type(e).__name__}), 500


if __name__ == "__main__":
    log.info("flyn-graphiti-api starting on 127.0.0.1:8100 (group=%s)", GROUP_ID)
    ensure_graphiti()  # Warm init
    app.run(host="127.0.0.1", port=8100, debug=False, use_reloader=False)
