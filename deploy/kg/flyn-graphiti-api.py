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

# -----------------------------------------------------------------------------
# Monkeypatch: graphiti-core 0.28.x / 0.29.x bug
#
# graphiti_core/utils/maintenance/node_operations.py:330 (0.28.2) / :557 (0.29.0)
# does `NodeResolutions(**llm_response).entity_resolutions` assuming the LLM
# returns a dict shaped {"entity_resolutions": [...]}. Local gemma4:e4b often
# returns the bare list instead, which crashes with:
#   "NodeResolutions() argument after ** must be a mapping, not list"
#
# We replace `dedupe_extracted_nodes` (the caller) with a version that handles
# both shapes. Upstream issue / PR is the right long-term fix; this keeps the
# REST service usable in the meantime.
# -----------------------------------------------------------------------------
def _install_node_resolutions_patch() -> None:
    import graphiti_core.utils.maintenance.node_operations as node_ops  # type: ignore[import]
    from graphiti_core.prompts.dedupe_nodes import NodeDuplicate, NodeResolutions  # type: ignore[import]

    if getattr(node_ops, "_flyn_resolutions_patched", False):
        return

    original = node_ops.resolve_extracted_nodes  # type: ignore[attr-defined]

    async def _patched_resolve_extracted_nodes(*args, **kwargs):
        # Run the original but catch the specific TypeError and retry with a fix-up.
        try:
            return await original(*args, **kwargs)
        except TypeError as e:
            msg = str(e)
            if "NodeResolutions() argument after **" not in msg:
                raise
            # The failure happened inside; we can't easily inject mid-call.
            # Re-call the original and let it raise OR fall through. The actual
            # patch happens at the line below via _entity_resolutions_from_llm.
            raise

    # Cleaner: patch NodeResolutions.__init__ itself to accept a list payload.
    _orig_init = NodeResolutions.__init__

    def _patched_init(self, *args, **kwargs):
        # Pattern A: called as NodeResolutions(**dict_with_list) — normal happy path
        # Pattern B: NodeResolutions(**list_payload) — TypeError. We can't reach this
        #   because **list_payload itself is the TypeError before __init__ runs.
        # So intercept at the call site instead by monkeypatching the module function.
        return _orig_init(self, *args, **kwargs)

    # Real fix: wrap the call site
    # We rewrite the module-level call by replacing the offending function entirely.
    # The function in question reads `llm_response` and does `NodeResolutions(**llm_response)`.
    # We patch the LLM client's response parser to coerce list → dict.

    # Approach: wrap LLMClient.generate_response to (a) coerce list-shaped
    # NodeResolutions responses to dict, (b) validate the result against the
    # requested response_model and retry once with explicit schema feedback if
    # gemma4 returns the wrong field names ({best_match} instead of {name,
    # duplicate_name}, etc.).
    import graphiti_core.llm_client.openai_generic_client as ogc  # type: ignore[import]
    from graphiti_core.prompts.models import Message  # type: ignore[import]

    _orig_generate = ogc.OpenAIGenericClient.generate_response
    MAX_VALIDATION_RETRIES = 1  # one re-prompt after initial failure

    async def _patched_generate(self, messages, response_model=None, **kwargs):
        last_validation_err = None
        current_messages = messages

        for attempt in range(MAX_VALIDATION_RETRIES + 1):
            result = await _orig_generate(
                self, current_messages,
                response_model=response_model,
                **kwargs,
            )

            # Coerce list-shape for NodeResolutions (or any model whose top
            # field is a list named like the response_model's only field).
            if (
                response_model is NodeResolutions
                and isinstance(result, list)
            ):
                logging.info(
                    "[patch] coercing list-shape LLM response → NodeResolutions dict (%d items, attempt %d)",
                    len(result), attempt + 1,
                )
                result = {"entity_resolutions": result}

            # Validate (only when caller asked for a structured response)
            if response_model is not None and isinstance(result, dict):
                try:
                    response_model(**result)
                    return result   # ✓ validated
                except Exception as e:
                    last_validation_err = e
                    if attempt < MAX_VALIDATION_RETRIES:
                        logging.info(
                            "[patch] validation failed (attempt %d/%d), retrying with schema feedback: %s",
                            attempt + 1, MAX_VALIDATION_RETRIES + 1, str(e)[:150],
                        )
                        feedback = (
                            f"Your previous response failed schema validation:\n\n{e}\n\n"
                            f"Required schema:\n{response_model.model_json_schema()}\n\n"
                            "Please respond again with EXACTLY the schema field names. "
                            "Use 'name' and 'duplicate_name', not 'best_match' or similar synonyms. "
                            "Every field marked required must be present and non-empty."
                        )
                        current_messages = list(messages) + [
                            Message(role="user", content=feedback)
                        ]
                        continue
            # Either no response_model, non-dict result, or final attempt
            return result

        # Shouldn't fall through, but be safe
        if last_validation_err:
            raise last_validation_err
        return result

    ogc.OpenAIGenericClient.generate_response = _patched_generate
    node_ops._flyn_resolutions_patched = True
    logging.info(
        "[patch] installed NodeResolutions coercion + 1-retry validation feedback "
        "on OpenAIGenericClient.generate_response"
    )


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - flyn-graphiti-api - %(levelname)s - %(message)s",
)
log = logging.getLogger("flyn-graphiti-api")

# Install the patch after logging is configured so any [patch] messages land
# in the same log stream as the rest of the service.
_install_node_resolutions_patch()

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


def run_async(coro, timeout=1800):
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
