"""
Graphiti + FalkorDB smoke test for Intel Mac.

Chosen backend for Intel/16 GB hosts: FalkorDB is Redis-based, ships
sub-10 ms queries, and the official graphiti-core falkordb extra works
out of the box (unlike kuzu, which has a broken search path on
graphiti-core 0.30 — see graphiti-kuzu-smoke.py).

Verified working on Nicolas Aubert (Mac mini Late 2014, x86_64, 16 GB,
Docker 27.1.1) on 2026-04-25:

    build OK
    add OK
    search results: 3
     - Nicolas Aubert uses an Intel Mac mini Late 2014.
     - Rungis is near Paris.
     - Nicolas Aubert is a freelance commercial agent at Rungis.

Setup:

    docker run -d \
      --name flyn-falkordb --restart unless-stopped \
      --memory 1g --memory-reservation 512m \
      -p 127.0.0.1:6379:6379 -p 127.0.0.1:3000:3000 \
      -v $HOME/.openclaw/data/falkordb:/data \
      falkordb/falkordb:latest

    D=$HOME/.openclaw/data/structured/graphiti
    uv pip install --python $D/venv/bin/python \
        "graphiti-core[falkordb]" "graphiti-core[google-genai]"
    $D/venv/bin/python graphiti-falkordb-smoke.py
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
from graphiti_core.driver.falkordb_driver import FalkorDriver
from graphiti_core.llm_client.gemini_client import GeminiClient, LLMConfig
from graphiti_core.embedder.gemini import GeminiEmbedder, GeminiEmbedderConfig
from graphiti_core.cross_encoder.gemini_reranker_client import GeminiRerankerClient
from graphiti_core.nodes import EpisodeType


async def main():
    drv = FalkorDriver(host="127.0.0.1", port=6379, database="nicolas")
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
            "Nicolas Aubert is a freelance commercial agent at Rungis, "
            "the largest European food market near Paris. "
            "He uses an Intel Mac mini Late 2014."
        ),
        source=EpisodeType.text,
        source_description="smoke",
        reference_time=datetime.now(timezone.utc),
        group_id="nicolas",
    )
    print("add OK")

    res = await g.search(
        "Where does Nicolas work?", group_ids=["nicolas"]
    )
    print("search results:", len(res))
    for r in res[:3]:
        print(" -", r.fact)


asyncio.run(main())
