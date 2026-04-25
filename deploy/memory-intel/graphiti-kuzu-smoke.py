"""
Graphiti + Kuzu + Gemini smoke test for Intel Mac (no Neo4j, no Docker).

⚠ As of 2026-04-25, this script BUILDS and ADDS successfully but SEARCH fails
   on graphiti-core 0.30 + kuzu 0.11.3 with:
     RuntimeError: Binder exception: Table Entity doesn't have an index with
                   name node_name_and_summary.

   Tracking: github.com/getzep/graphiti issue #1132 ("Kuzu is archived").

   Keep this file as a reference; re-run periodically to detect upstream fix.

Setup (assumes uv + Python 3.12 from `uv python install 3.12`):
    D=$HOME/.openclaw/workspace/memory/structured/graphiti
    cd $D
    uv venv --python 3.12 venv
    uv pip install --python venv/bin/python "graphiti-core[kuzu]" "graphiti-core[google-genai]"
    venv/bin/python graphiti-kuzu-smoke.py
"""
import asyncio
import json
import os
from datetime import datetime, timezone

KEY = json.load(open(os.path.expanduser(
    "~/.openclaw/agents/main/agent/auth-profiles.json"
)))["profiles"]["gemini:default"]["token"]
os.environ["GOOGLE_API_KEY"] = KEY

from graphiti_core import Graphiti
from graphiti_core.driver.kuzu_driver import KuzuDriver
from graphiti_core.llm_client.gemini_client import GeminiClient, LLMConfig
from graphiti_core.embedder.gemini import GeminiEmbedder, GeminiEmbedderConfig
from graphiti_core.cross_encoder.gemini_reranker_client import GeminiRerankerClient
from graphiti_core.nodes import EpisodeType


async def main():
    db = os.path.expanduser(
        "~/.openclaw/workspace/memory/structured/graphiti/kuzu.db"
    )
    drv = KuzuDriver(db=db)
    cfg = LLMConfig(api_key=KEY, model="gemini-2.5-flash")
    g = Graphiti(
        graph_driver=drv,
        llm_client=GeminiClient(config=cfg),
        embedder=GeminiEmbedder(config=GeminiEmbedderConfig(
            api_key=KEY,
            embedding_model="models/gemini-embedding-001",
        )),
        cross_encoder=GeminiRerankerClient(config=cfg),
    )

    await g.build_indices_and_constraints()
    print("build OK")

    await g.add_episode(
        name="intro",
        episode_body=(
            "Nicolas Aubert lives in France and uses an Intel Mac mini Late 2014."
        ),
        source=EpisodeType.text,
        source_description="smoke",
        reference_time=datetime.now(timezone.utc),
        # NOTE: omit `group_id` on Kuzu — graphiti's multi-tenant check is
        # hard-coded against drivers that expose `_database`, which Kuzu
        # doesn't. AttributeError will be raised if you pass it.
    )
    print("add OK")

    res = await g.search("What computer does Nicolas use?")
    print("search results:", len(res))
    for r in res[:3]:
        print(" -", r.fact)


asyncio.run(main())
