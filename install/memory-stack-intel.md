# Memory Stack — Intel Mac (x86_64, 16 GB target)

Companion doc to `install/openclaw-install-intel.md`. Captures the memory architecture for Intel-based Macs (no Metal/MPS, no admin assumed) where the Apple-Silicon Flyn stack is too heavy.

> **Branch:** `intel-mac-support` in `flyn-agent`. Hardware envelope tuned for Mac mini Late 2014 (Macmini7,1, 4 cores, 16 GB) — Nicolas Aubert's box.

## Reference target — Apple Silicon Flyn (do not use as-is)

The default `deploy/install-flyn.sh` builds:

| Layer | Apple Silicon Flyn | RAM | Why it doesn't fit Intel/16 GB |
|---|---|---|---|
| Heartbeat model | `ollama/gemma4:e4b` (local, Metal) | ~11 GB | No Metal/MPS on Intel — runs CPU-only at 1–3 tok/s, heartbeats time out |
| Embeddings (primary) | `gemini-embedding-2-preview` (cloud) | 0 | ✅ keep |
| Embeddings (local fallback) | EmbeddingGemma 300M (Ollama) | ~1.5 GB | Slow on Intel CPU, marginal value |
| Context engine | Lossless Claw plugin | ~30 MB | ✅ keep |
| Structured KG | **Graphiti + Neo4j 5.26 Docker** (1 GB heap) | ~3–5 GB (incl. Docker VM) | Too heavy on 16 GB total — see swap below |
| KG REST wrapper | `flyn-graphiti-api` Flask service | ~300 MB | ✅ keep |
| OpenClaw memory | sqlite-vec + cloud embeddings | ~50–100 MB | ✅ keep |

## Intel Mac stack (recommended)

| Layer | Pick | RAM | Notes |
|---|---|---|---|
| Heartbeat model | `gemini/gemini-2.5-flash` (cloud, via Gemini key) | 0 | Already configured on Nicolas. Codex Plus left for user-facing chat turns. |
| Embeddings (primary) | `gemini-embedding-001` (cloud, stable) | 0 | Use `gemini-embedding-001` not `-2-preview` for production stability — 3072 dims, MTEB-leading. Aliased through `gemini:default` + `google:default` profiles. |
| Embeddings (local fallback) | **none** | 0 | Skip on Intel. If Gemini quota dies, accept the gap. |
| Context engine | **Lossless Claw plugin** | ~30 MB | Loaded by default in OpenClaw 2026.4.23 (verified in `doctor`'s 59-plugin list on Nicolas). |
| OpenClaw built-in memory | **sqlite-vec + Gemini cloud embeddings** | ~50–100 MB | First memory primitive to deploy. `openclaw memory index --force` after embedding provider is set. |
| Structured memory | **mem0** (Python, SQLite default) | ~150 MB | Pure Python, cloud LLM (uses Gemini key for both LLM + embeddings). CRM-style facts, decay-aware. Lightweight enough for 16 GB. |
| Structured KG (typed/temporal) | **Graphiti + FalkorDB Docker** *(or skip entirely)* | ~600–800 MB total | See "Graphiti backend" section below. |
| Notes / second brain | **Obsidian + `obsidian-mcp-server`** | ~200 MB (Obsidian app) + ~50 MB (MCP) | Human-facing markdown vault. Mobile sync. Agent reads/writes via MCP. Optional but high-leverage. |

**Total steady-state memory footprint**: ~1.5–2 GB for the full stack incl. FalkorDB. Leaves ~14 GB for macOS, browser, OpenClaw runtime, Codex/Gemini chat sessions.

## Graphiti backend on Intel Mac (research summary, 2026)

Graphiti 2026 supports four native drivers: **Neo4j**, **FalkorDB**, **Kuzu**, **Amazon Neptune**.

| Driver | Type | Footprint | Verdict for Nicolas |
|---|---|---|---|
| Neo4j 5.26 (default) | Java + Docker | ~3–5 GB incl. Docker VM | ❌ Too heavy on 16 GB |
| Neo4j 5.26 *shrunken* (heap_max=512m, pagecache=128m, Docker cap=4GB) | Java + Docker | ~2.5 GB | ⚠️ Workable but uncomfortable |
| **FalkorDB 1.1.2** | Redis-based, single Docker container | ~600–800 MB total | ✅ **Recommended.** Native Graphiti driver, sub-10 ms queries, multi-agent isolation. |
| Kuzu 0.11.3 | Embedded, file-based, no Docker | ~100–200 MB | ✅ Lightest, BUT graphiti-core issue #1132 reports "Kuzu is archived" — stable but no new development. Use only if Docker is forbidden on the host. |
| Amazon Neptune | Managed cloud | 0 (cloud) | ❌ Cloud-only, billed per hour |

**Pick: FalkorDB** for active development + tiny footprint. Kuzu is the contingency if Docker becomes a hard constraint.

### FalkorDB Docker config for Intel/16 GB

```sh
docker run -d \
  --name flyn-falkordb \
  --restart unless-stopped \
  --memory 1g --memory-reservation 512m \
  -p 127.0.0.1:6379:6379 \
  -p 127.0.0.1:3000:3000 \
  -v "$HOME/.openclaw/workspace/memory/structured/falkordb":/data \
  falkordb/falkordb:1.1.2
```

Graphiti-core install: `pip install graphiti-core[falkordb]` then point the driver at `redis://localhost:6379`.

### Kuzu (attempted, **NOT production-ready as of 2026-04-25**)

```python
from graphiti_core import Graphiti
from graphiti_core.driver.kuzu_driver import KuzuDriver
driver = KuzuDriver(db=os.path.expanduser("~/.openclaw/workspace/memory/structured/graphiti/kuzu.db"))
```

Install: `uv pip install "graphiti-core[kuzu]" "graphiti-core[google-genai]"`. macOS x86_64 wheels published for Python 3.11/3.12/3.13.

> **⚠ Hit two regressions on graphiti-core 0.30 + kuzu 0.11.3 during Nicolas's deploy (2026-04-25)** — exactly what graphiti issue [#1132 "Kuzu is archived"](https://github.com/getzep/graphiti/issues/1132) warned about:
> 1. `add_episode(group_id=...)` raises `AttributeError: 'KuzuDriver' object has no attribute '_database'` — the multi-tenant `group_id` check is hard-coded against Neo4j-style drivers. Workaround: omit `group_id`.
> 2. After `await g.build_indices_and_constraints()` succeeds, `await g.search(...)` fails with `RuntimeError: Binder exception: Table Entity doesn't have an index with name node_name_and_summary.` — the search path expects a fulltext index that the Kuzu schema-builder doesn't create.
>
> **Build + add work; search is broken.** That makes Kuzu unusable for an agent that needs to query its memory.
>
> **Decision for Nicolas:** **drop Graphiti** entirely. Coverage is good enough from OpenClaw built-in memory (sqlite-vec + Gemini) + Lossless Claw + mem0. Revisit Graphiti when *either* Kuzu's regressions are fixed in graphiti-core, *or* Docker Desktop is installed and we can switch to FalkorDB.

## Obsidian as a knowledge layer (research summary, 2026)

Obsidian **does not replace Graphiti** — Graphiti is the agent's typed/temporal entity graph; Obsidian is the human-facing markdown vault. They complement each other.

| Project | Type | Local-only | Best for |
|---|---|---|---|
| **`cyanheads/obsidian-mcp-server`** | MCP server | ✅ requires Obsidian Local REST API plugin | Agent CRUD on a vault Nicolas already uses. Notes, tags, frontmatter, full-text search. |
| **`engraph`** (devwhodevs) | Single Go binary + MCP | ✅ | Heavier KG features: semantic embeddings + wikilink graph traversal + temporal awareness + LLM rerank, all local. |
| **Obsidian-Intelligence MCP** | MCP + Ollama | ✅ air-gappable | Good if local LLM was viable — but Ollama on Intel is too slow, so prefer the cloud-Gemini path. |
| **Smart Connections plugin** | Obsidian plugin | ✅ | UX-side — chat with the vault from inside Obsidian. Doesn't expose MCP, so the agent can't reach it. |

**Pick for Nicolas:** start with **`obsidian-mcp-server`** if/when he installs Obsidian. Lowest deps, no Ollama, agent gets full vault access via MCP. Add `engraph` later if the wikilink graph is wanted on top.

If Nicolas is **not** an Obsidian user already, skip this layer for now — Lossless Claw + OpenClaw memory + mem0 + Graphiti+FalkorDB cover the agent's memory needs without it.

## Build order on Intel (lightest → heaviest)

1. **Lossless Claw context engine** — verify already loaded (it is, per Nicolas's `doctor` output).
2. **OpenClaw built-in memory** — `agents.defaults.memorySearch.{provider=gemini, model=gemini-embedding-001, fallback=local}`, then `openclaw memory index --force`. This is the highest-payoff step.
3. **mem0** — `pip install mem0ai`, configure with Gemini for LLM + embeddings, SQLite store at `~/.openclaw/workspace/memory/mem0/`.
4. **Graphiti + FalkorDB** — Docker container + `graphiti-core[falkordb]` venv + REST wrapper from `deploy/kg/flyn-graphiti-api.py` (patch the driver from Neo4j→FalkorDB).
5. **Obsidian + `obsidian-mcp-server`** — only if Nicolas already uses Obsidian; otherwise skip.

## What we permanently skip on Intel

| Component | Reason |
|---|---|
| Ollama + `gemma4:e4b` heartbeat | No Metal/MPS — 1–3 tok/s, heartbeat times out |
| EmbeddingGemma local fallback | Same — slow embeddings, marginal value when Gemini cloud is reachable |
| Neo4j default config (1 GB heap) | Docker VM + heap = ~3–5 GB on a 16 GB host |
| Kuzu (unless Docker is forbidden) | Project archived — stable but no new development |

## Findings log (Nicolas Aubert, 2026-04-25)

### Lossless Claw context engine — ✅ deployed
- `openclaw plugins install @martian-engineering/lossless-claw@0.9.2` — clean install, peer-linked to `~/.openclaw/lib/node_modules/openclaw`. Bound to `contextEngine` slot (was `legacy`). Restart gateway after.

### OpenClaw built-in memory — ✅ deployed
- Set `agents.defaults.memorySearch.{provider:"gemini", model:"gemini-embedding-001", fallback:"local"}`.
- `openclaw memory index --force` → `Memory index updated (main)`.
- `openclaw memory status --json` reports `provider=gemini`, `model=gemini-embedding-001`, `vector` block populated.

### mem0 — ✅ deployed
- Path: `~/.openclaw/workspace/memory/mem0/`
- Created venv with system Python 3.9 (mem0 supports it). Installed `mem0ai 2.0.0` + `google-genai` + `chromadb`.
- Config: Gemini for both LLM (`gemini-2.5-flash`) and embeddings (`gemini-embedding-001`); chroma vector store at `~/.openclaw/workspace/memory/mem0/store/`.
- Smoke test: `m.add("Nicolas Aubert lives in France and uses an Intel Mac mini.", user_id="nicolas")` → mem0 split into 2 atomic facts; `m.search("What computer does Nicolas use?", filters={"user_id":"nicolas"})` returned both, ranked by similarity.
- ⚠ Note: `search()` API in mem0 v2 dropped top-level `user_id`; must use `filters={"user_id":...}`.

### Graphiti + Kuzu — ❌ blocked by upstream regressions (see "Kuzu" section above)
- Tried via `uv` (Python 3.12 + venv, since system Python is 3.9 < graphiti's 3.10 minimum).
- `pip install "graphiti-core[kuzu]" "graphiti-core[google-genai]"` → installs cleanly. `import KuzuDriver` works. `kuzu 0.11.3` working.
- Smoke script with `GeminiClient`, `GeminiEmbedder`, **`GeminiRerankerClient`** (default reranker is `OpenAIRerankerClient` and demands `OPENAI_API_KEY`):
  - `g.build_indices_and_constraints()` → ✅
  - `g.add_episode(...)` with `group_id="nicolas"` → ❌ `AttributeError: 'KuzuDriver' object has no attribute '_database'`. Removing `group_id` works.
  - `g.search(...)` → ❌ `RuntimeError: Binder exception: Table Entity doesn't have an index with name node_name_and_summary.`
- **Conclusion:** Don't deploy Graphiti+Kuzu on Nicolas. Revisit when Kuzu regressions are fixed upstream OR Docker Desktop is installed (then switch to FalkorDB).

### Brew + python@3.12 install attempt
- `brew install python@3.12` stalled on `openssl@3` dependency for 5+ minutes with no progress. Three brew processes alive but no log advance.
- Pivoted to `uv` (Astral) — `curl -LsSf https://astral.sh/uv/install.sh | sh` → `uv 0.11.7` in `~/.local/bin/` in seconds. `uv python install 3.12` downloaded prebuilt cpython-3.12.13-macos-x86_64 in <30s.
- **Lesson:** for Intel Macs (especially non-admin or where brew misbehaves), prefer `uv` over `brew install python@*`. Single static binary, no compile, no admin.

## Final Intel stack on Nicolas (deployed)

| Layer | Status | Command/Path |
|---|---|---|
| Heartbeat model | ✅ `gemini/gemini-2.5-flash` | `agents.defaults.heartbeat.model` |
| Embeddings | ✅ `gemini-embedding-001` cloud | `agents.defaults.memorySearch.{provider,model}` |
| Context engine | ✅ Lossless Claw 0.9.2 | `~/.openclaw/extensions/lossless-claw/` |
| Built-in memory | ✅ sqlite-vec + Gemini | `openclaw memory ...` |
| Structured memory | ✅ mem0 v2.0.0 | `~/.openclaw/workspace/memory/mem0/` |
| Typed/temporal KG | ❌ blocked (Graphiti+Kuzu regressions) | revisit with FalkorDB+Docker or upstream fix |
| Notes / second brain | 🟡 not deployed | available via `obsidian-mcp-server` if Nicolas adopts Obsidian |
