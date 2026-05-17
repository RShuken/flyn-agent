# Flyn MemoryRouter — Read-Side Implementation Plan (Tasks 25–42)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the shipped MemoryRouter (`localhost:8400`) with a read surface — `POST /api/memory/query`, `POST /api/memory/lint`, `GET /api/memory/sources`, the `flyn-mem` CLI, structured logging, and cross-agent discovery via auto-memory + workspace pointers — fanning out across 10 read adapters with RRF rank fusion.

**Architecture:** Async sibling to the existing sync write path. New `ReadAdapter` Protocol alongside existing `MemoryAdapter`. A new `query.py` orchestrator fans out via `asyncio.gather`, dedupes by `canonical_id` and normalized text hash, and merges via reciprocal rank fusion. No LLM in the router; callers synthesize. CLI binary `flyn-mem` at `/usr/local/bin/flyn-mem` wraps the REST endpoint for every shell.

**Tech Stack:** Python 3.14, FastAPI 0.110+, Pydantic 2.5+, httpx (async client), pytest + pytest-asyncio (already in `dev` extras), `asyncio` stdlib, `subprocess.run` + `asyncio.to_thread` for the ocw_mem adapter. No new dependencies beyond Phase 0.

**Spec:** `docs/superpowers/specs/2026-05-16-flyn-memory-router-unified-design.md`
**Depends on:** `docs/superpowers/plans/2026-05-15-flyn-memory-router-phase-0.md` (Tasks 1–24, already shipped as of commit `bb8cf88`)
**Out of scope:** multi-owner read scoping, streaming results, cross-machine federation (see spec §10).

---

## File structure (lock the decomposition)

```
deploy/memory-router/flyn_memory_router/
├── types.py                          (modify: add Hit, QueryResult, LintReport)
├── adapters/
│   ├── base.py                       (modify: add ReadAdapter Protocol)
│   ├── hot_read.py                   (new)
│   ├── warm_read.py                  (new)
│   ├── cool_read.py                  (new)
│   ├── cold_read.py                  (new)
│   ├── lesson_read.py                (new)
│   ├── reference_read.py             (new)
│   ├── user_read.py                  (new)
│   ├── ol_wiki_read.py               (new)
│   ├── ocw_mem_read.py               (new)
│   └── lossless_read.py              (new)
├── query.py                          (new: orchestrator + RRF + dedup, ≤250 lines)
├── lint.py                           (new: drift detection, ≤200 lines)
├── health_tracker.py                 (new: rolling per-source stats, ≤150 lines)
├── logging_contract.py               (new: JSONL writers + rotation, ≤200 lines)
├── cli.py                            (new: flyn-mem entry point, ≤300 lines)
├── discovery.py                      (new: install-time artifact writers, ≤150 lines)
├── config.py                         (modify: add READ_SOURCES + log paths)
├── server.py                         (modify: add 3 routes; cap ≤300 lines)
└── pyproject.toml                    (modify: add console_scripts entry)

deploy/memory-router/tests/
├── unit/
│   ├── test_query.py                 (new: RRF math, dedup logic)
│   ├── test_read_adapters.py         (new: per-adapter unit tests w/ fixtures)
│   ├── test_lint.py                  (new)
│   ├── test_health_tracker.py        (new)
│   ├── test_logging_contract.py      (new)
│   ├── test_install_artifacts.py     (new)
│   └── test_cli.py                   (new)
├── integration/
│   ├── test_query_roundtrip.py       (new)
│   ├── test_timeout_handling.py      (new)
│   ├── test_partial_failure.py       (new)
│   └── test_cli_to_server.py         (new)
├── smoke/
│   └── test_live_query.py            (new: manual ship-gate)
└── fixtures/
    ├── reference_vault/              (new: mini Karpathy vault)
    ├── auto_memory/                  (new: mini auto-memory dir)
    └── mock_graphiti_search.json     (new)

deploy/memory-router/install.sh       (modify: symlink + write auto-memory + workspace pointers)
deploy/outcomes/MEMORY-ROUTER-READ-RUBRIC.md   (new: per-phase rubric for outcomes_runner)
```

---

## Phase 0c — Read-side foundation (Tasks 25–27)

### Task 25: Hit + QueryResult + LintReport models

**Files:**
- Modify: `deploy/memory-router/flyn_memory_router/types.py`
- Modify: `deploy/memory-router/tests/unit/test_types.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_types.py`)

```python
def test_hit_minimal():
    from flyn_memory_router.types import Hit
    h = Hit(text="Beth Kukla, COO Cora", source="hot/MEMORY.md", score=0.9, metadata={})
    assert h.source == "hot/MEMORY.md"
    assert h.score == 0.9


def test_hit_requires_source_namespace():
    from flyn_memory_router.types import Hit
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Hit(text="x", source="", score=0.5, metadata={})


def test_query_result_shape():
    from flyn_memory_router.types import Hit, QueryResult, SourceError
    qr = QueryResult(
        query_id="qid-1",
        hits=[Hit(text="a", source="hot/MEMORY.md", score=0.9, metadata={})],
        source_errors=[SourceError(source="lossless", error_class="timeout", message="2s")],
        elapsed_ms=42,
    )
    assert qr.query_id == "qid-1"
    assert len(qr.hits) == 1
    assert qr.source_errors[0].error_class == "timeout"


def test_lint_report_shape():
    from flyn_memory_router.types import LintReport, LintFinding
    lr = LintReport(findings=[LintFinding(
        entity="Beth",
        sources={"hot/MEMORY.md": "COO Cora", "warm/graphiti": "Co-Founder"},
        divergence="graphiti missing 'COO Cora' attribute",
        suggested_fix="update Graphiti episode 'beth-intro-2026-04'",
    )])
    assert len(lr.findings) == 1
```

- [ ] **Step 2: Run tests, expect FAIL**

```bash
cd deploy/memory-router && python -m pytest tests/unit/test_types.py -v -k "hit or query_result or lint_report"
```

Expected: `ImportError: cannot import name 'Hit' from 'flyn_memory_router.types'`

- [ ] **Step 3: Add models to `types.py`** (append after `EventResult`)

```python
class Hit(BaseModel):
    """One ranked retrieval result returned by a ReadAdapter."""

    text: str = Field(..., min_length=1, max_length=8000)
    source: str = Field(
        ..., min_length=1, max_length=64,
        description="namespaced: 'hot/MEMORY.md', 'warm/graphiti', 'reference/wiki', ...",
    )
    score: float = Field(..., description="adapter-native score; RRF re-ranks across sources")
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceError(BaseModel):
    source: str
    error_class: str       # "timeout" | "exception" | "malformed"
    message: str = ""


class QueryResult(BaseModel):
    query_id: str
    hits: list[Hit] = Field(default_factory=list)
    source_errors: list[SourceError] = Field(default_factory=list)
    elapsed_ms: int


class LintFinding(BaseModel):
    entity: str
    sources: dict[str, str]
    divergence: str
    suggested_fix: str = ""


class LintReport(BaseModel):
    findings: list[LintFinding] = Field(default_factory=list)
```

- [ ] **Step 4: Run tests, expect PASS**

```bash
cd deploy/memory-router && python -m pytest tests/unit/test_types.py -v
```

- [ ] **Step 5: Commit**

```bash
git add deploy/memory-router/flyn_memory_router/types.py \
        deploy/memory-router/tests/unit/test_types.py
git commit -m "feat(memory-router): Hit/QueryResult/LintReport models for read surface"
```

---

### Task 26: ReadAdapter Protocol + ReadSourceConfig registry

**Files:**
- Modify: `deploy/memory-router/flyn_memory_router/adapters/base.py`
- Modify: `deploy/memory-router/flyn_memory_router/config.py`
- Modify: `deploy/memory-router/tests/unit/test_adapters.py`
- Modify: `deploy/memory-router/tests/unit/test_config.py`

- [ ] **Step 1: Write the failing tests** (append to existing test files)

`tests/unit/test_adapters.py`:

```python
def test_read_adapter_protocol_recognized():
    from flyn_memory_router.adapters.base import ReadAdapter
    from flyn_memory_router.types import Hit
    import asyncio

    class FakeRead:
        name = "fake"
        read_timeout = 2.0
        default_included = True
        async def query(self, q: str, top_k: int = 10) -> list[Hit]:
            return [Hit(text=q.upper(), source="fake/test", score=1.0, metadata={})]

    fake = FakeRead()
    assert isinstance(fake, ReadAdapter)
    hits = asyncio.run(fake.query("hello", top_k=1))
    assert hits[0].text == "HELLO"
```

`tests/unit/test_config.py`:

```python
def test_read_sources_registry_has_all_ten():
    from flyn_memory_router.config import READ_SOURCES
    expected = {"hot", "warm", "cool", "cold", "lesson",
                "reference", "user", "ol_wiki", "ocw_mem", "lossless"}
    assert set(READ_SOURCES.keys()) == expected


def test_read_sources_defaults_excluded_heavies():
    from flyn_memory_router.config import READ_SOURCES
    assert READ_SOURCES["ocw_mem"].default_included is False
    assert READ_SOURCES["lossless"].default_included is False
    assert READ_SOURCES["hot"].default_included is True


def test_read_source_config_has_log_paths():
    from flyn_memory_router.config import Config
    cfg = Config.from_env()
    assert cfg.log_dir.name == "logs"
```

- [ ] **Step 2: Run tests, expect FAIL**

```bash
cd deploy/memory-router && python -m pytest tests/unit/test_adapters.py::test_read_adapter_protocol_recognized tests/unit/test_config.py -v
```

- [ ] **Step 3: Extend `adapters/base.py`** (append after `MemoryAdapter`)

```python
from ..types import Hit


@runtime_checkable
class ReadAdapter(Protocol):
    """Implement `query(q, top_k)`. Returned hits use adapter-native scoring;
    cross-source ranking happens in query.py via RRF."""

    name: str
    read_timeout: float
    default_included: bool

    async def query(self, q: str, top_k: int = 10) -> list[Hit]:
        ...
```

- [ ] **Step 4: Replace `config.py` with the extended version**

```python
"""Runtime configuration. All paths and ports come from env. No hardcoded paths in modules."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    home: Path
    workspace: Path
    port: int
    passthrough_mode: bool
    graphiti_url: str
    knowledge_dir: Path
    reference_vault: Path
    auto_memory_dir: Path
    ol_wiki_url: str
    ol_wiki_pin: str

    @property
    def db_path(self) -> Path:
        return self.home / "data" / "router.db"

    @property
    def queue_dir(self) -> Path:
        return self.home / "queue"

    @property
    def log_dir(self) -> Path:
        return self.home / "logs"

    @property
    def memory_md(self) -> Path:
        return self.workspace / "MEMORY.md"

    @property
    def workspace_memory_dir(self) -> Path:
        return self.workspace / "memory"

    @property
    def pin_file(self) -> Path:
        return self.workspace / "pins.json"

    @property
    def captures_index(self) -> Path:
        return self.home / "captures_index.jsonl"

    @classmethod
    def from_env(cls) -> "Config":
        home_env = Path.home() / ".flyn" / "memory-router"
        home = Path(os.environ.get("FLYN_MEMORY_ROUTER_HOME", str(home_env)))
        workspace = Path(os.environ.get("FLYN_WORKSPACE",
                                         str(Path.home() / ".openclaw" / "workspace")))
        port = int(os.environ.get("FLYN_MEMORY_ROUTER_PORT", "8400"))
        passthrough = os.environ.get("FLYN_MEMORY_ROUTER_PASSTHROUGH", "false").lower() == "true"
        graphiti_url = os.environ.get("FLYN_GRAPHITI_URL", "http://localhost:8100")
        knowledge_dir = Path(os.environ.get("FLYN_KNOWLEDGE_DIR",
                                             str(Path.home() / "AI" / "openclaw" / "flyn-agent" / "KNOWLEDGE")))
        reference_vault = Path(os.environ.get("FLYN_REFERENCE_VAULT",
                                               str(Path.home() / "AI" / "openclaw" / "reference")))
        auto_memory_dir = Path(os.environ.get("FLYN_AUTO_MEMORY_DIR",
                                               str(Path.home() / ".claude" / "projects" / "-Users-4c-AI" / "memory")))
        ol_wiki_url = os.environ.get("FLYN_OL_WIKI_URL", "http://localhost:8200")
        ol_wiki_pin = os.environ.get("FLYN_OL_WIKI_PIN", "1080")
        return cls(home=home, workspace=workspace, port=port,
                   passthrough_mode=passthrough, graphiti_url=graphiti_url,
                   knowledge_dir=knowledge_dir, reference_vault=reference_vault,
                   auto_memory_dir=auto_memory_dir, ol_wiki_url=ol_wiki_url,
                   ol_wiki_pin=ol_wiki_pin)


@dataclass(frozen=True)
class ReadSourceConfig:
    name: str
    cls_path: str
    timeout: float
    default_included: bool


READ_SOURCES: dict[str, ReadSourceConfig] = {
    "hot":       ReadSourceConfig("hot",       "flyn_memory_router.adapters.hot_read:HotRead",             1.0, True),
    "warm":      ReadSourceConfig("warm",      "flyn_memory_router.adapters.warm_read:WarmRead",           2.0, True),
    "cool":      ReadSourceConfig("cool",      "flyn_memory_router.adapters.cool_read:CoolRead",           1.0, True),
    "cold":      ReadSourceConfig("cold",      "flyn_memory_router.adapters.cold_read:ColdRead",           1.0, True),
    "lesson":    ReadSourceConfig("lesson",    "flyn_memory_router.adapters.lesson_read:LessonRead",       1.0, True),
    "reference": ReadSourceConfig("reference", "flyn_memory_router.adapters.reference_read:ReferenceRead", 1.5, True),
    "user":      ReadSourceConfig("user",      "flyn_memory_router.adapters.user_read:UserRead",           1.0, True),
    "ol_wiki":   ReadSourceConfig("ol_wiki",   "flyn_memory_router.adapters.ol_wiki_read:OLWikiRead",      2.0, True),
    "ocw_mem":   ReadSourceConfig("ocw_mem",   "flyn_memory_router.adapters.ocw_mem_read:OCWMemRead",      3.0, False),
    "lossless":  ReadSourceConfig("lossless",  "flyn_memory_router.adapters.lossless_read:LosslessRead",   3.0, False),
}
```

- [ ] **Step 5: Run tests, expect PASS, then commit**

```bash
cd deploy/memory-router && python -m pytest tests/unit/test_adapters.py tests/unit/test_config.py -v
git add deploy/memory-router/flyn_memory_router/adapters/base.py \
        deploy/memory-router/flyn_memory_router/config.py \
        deploy/memory-router/tests/unit/test_adapters.py \
        deploy/memory-router/tests/unit/test_config.py
git commit -m "feat(memory-router): ReadAdapter Protocol + READ_SOURCES registry"
```

---

### Task 27: RRF merge + dedup pure functions (query.py without I/O)

**Files:**
- Create: `deploy/memory-router/flyn_memory_router/query.py` (pure functions only)
- Create: `deploy/memory-router/tests/unit/test_query.py`

- [ ] **Step 1: Write the failing tests**

```python
# deploy/memory-router/tests/unit/test_query.py
"""Pure-function tests for RRF merge + dedup. No I/O, no adapters."""
from __future__ import annotations

from flyn_memory_router.types import Hit


def _h(text: str, source: str, score: float = 0.9, **meta) -> Hit:
    return Hit(text=text, source=source, score=score, metadata=meta)


def test_rrf_combines_ranks_across_sources():
    from flyn_memory_router.query import rrf_merge
    per_source = {
        "hot/MEMORY.md":   [_h("Beth = COO Cora", "hot/MEMORY.md")],
        "warm/graphiti":   [_h("Beth Kukla, co-founder", "warm/graphiti")],
        "reference/wiki":  [_h("Beth — see [[OpenLit]]", "reference/wiki")],
    }
    result = rrf_merge(per_source, top_k=3)
    assert len(result) == 3
    assert all("Beth" in h.text for h in result)
    assert all(h.score > 0 for h in result)


def test_rrf_dedups_by_canonical_id():
    from flyn_memory_router.query import rrf_merge
    per_source = {
        "warm/graphiti":  [_h("Beth Kukla", "warm/graphiti", canonical_id="episode-42")],
        "ocw_mem":        [_h("Beth Kukla, COO", "ocw_mem", canonical_id="episode-42")],
        "hot/MEMORY.md":  [_h("Beth = COO Cora", "hot/MEMORY.md")],
    }
    result = rrf_merge(per_source, top_k=10)
    assert len(result) == 2
    merged = next(h for h in result if h.metadata.get("canonical_id") == "episode-42")
    assert "warm/graphiti" in merged.metadata.get("merged_sources", [])
    assert "ocw_mem" in merged.metadata.get("merged_sources", [])


def test_rrf_dedups_by_text_hash():
    from flyn_memory_router.query import rrf_merge
    per_source = {
        "hot/MEMORY.md":  [_h("Beth = COO Cora", "hot/MEMORY.md")],
        "warm/graphiti":  [_h("  Beth  =  COO  Cora  ", "warm/graphiti")],
    }
    result = rrf_merge(per_source, top_k=10)
    assert len(result) == 1


def test_rrf_respects_top_k():
    from flyn_memory_router.query import rrf_merge
    per_source = {
        "hot/MEMORY.md": [_h(f"hit-{i}", "hot/MEMORY.md", score=1.0 - i * 0.1) for i in range(10)],
    }
    result = rrf_merge(per_source, top_k=3)
    assert len(result) == 3


def test_rrf_handles_empty_sources():
    from flyn_memory_router.query import rrf_merge
    result = rrf_merge({"hot/MEMORY.md": [], "warm/graphiti": []}, top_k=10)
    assert result == []


def test_rrf_k_constant_is_60():
    from flyn_memory_router.query import RRF_K
    assert RRF_K == 60
```

- [ ] **Step 2: Run tests, expect FAIL**

```bash
cd deploy/memory-router && python -m pytest tests/unit/test_query.py -v
```

- [ ] **Step 3: Write `query.py` (pure functions only)**

```python
"""Cross-source query orchestration: dedup + RRF merge.

This module splits into pure functions (this file) and the async orchestrator
entry point `query()` added in Task 35. Phase 0c ships only pure functions
to keep early tests free of I/O.
"""
from __future__ import annotations

import hashlib
import re

from .types import Hit

RRF_K = 60  # reciprocal-rank-fusion constant (Cormack, Clarke, Buettcher 2009)


def _normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


def _text_hash(s: str) -> str:
    return hashlib.sha256(_normalize_text(s).encode("utf-8")).hexdigest()


def _hit_canonical_key(h: Hit) -> str:
    cid = h.metadata.get("canonical_id")
    return f"cid:{cid}" if cid else f"th:{_text_hash(h.text)}"


def _merge_two_hits(a: Hit, b: Hit) -> Hit:
    merged_sources: list[str] = list(a.metadata.get("merged_sources") or [a.source])
    if b.source not in merged_sources:
        merged_sources.append(b.source)
    new_meta = {**a.metadata, **{k: v for k, v in b.metadata.items() if k not in a.metadata}}
    new_meta["merged_sources"] = merged_sources
    return Hit(text=a.text, source=a.source, score=a.score, metadata=new_meta)


def rrf_merge(per_source: dict[str, list[Hit]], top_k: int) -> list[Hit]:
    """Merge per-source hits into a ranked list via reciprocal rank fusion.

    Hits with the same canonical_id (or, lacking that, normalized-text hash)
    are collapsed BEFORE RRF scoring. The collapsed hit's RRF score
    accumulates from all sources where it appeared.
    """
    bucket: dict[str, tuple[Hit, list[tuple[str, int]]]] = {}
    for source, hits in per_source.items():
        for rank, hit in enumerate(hits[:max(top_k * 3, 50)]):
            key = _hit_canonical_key(hit)
            if key in bucket:
                rep, observations = bucket[key]
                rep = _merge_two_hits(rep, hit)
                observations.append((source, rank))
                bucket[key] = (rep, observations)
            else:
                bucket[key] = (hit, [(source, rank)])

    scored: list[tuple[float, Hit]] = []
    for _key, (rep, observations) in bucket.items():
        rrf_score = sum(1.0 / (RRF_K + rank) for _src, rank in observations)
        scored_hit = Hit(text=rep.text, source=rep.source, score=rrf_score, metadata=rep.metadata)
        scored.append((rrf_score, scored_hit))

    scored.sort(key=lambda t: t[0], reverse=True)
    return [h for _s, h in scored[:top_k]]


def normalize_text(s: str) -> str:
    return _normalize_text(s)


def text_hash(s: str) -> str:
    return _text_hash(s)
```

- [ ] **Step 4: Run tests, expect PASS, then commit**

```bash
cd deploy/memory-router && python -m pytest tests/unit/test_query.py -v
git add deploy/memory-router/flyn_memory_router/query.py \
        deploy/memory-router/tests/unit/test_query.py
git commit -m "feat(memory-router): RRF merge + dedup pure functions"
```

---

## Phase 0d — Read adapters (Tasks 28–34)

Each adapter is ≤200 lines, returns `list[Hit]` sorted by native score descending. Cross-source ranking is RRF's job.

### Task 28: hot_read adapter

**Files:**
- Create: `deploy/memory-router/flyn_memory_router/adapters/hot_read.py`
- Create: `deploy/memory-router/tests/unit/test_read_adapters.py`

- [ ] **Step 1: Write the failing test**

```python
# deploy/memory-router/tests/unit/test_read_adapters.py
"""Per-adapter unit tests. Each adapter test class is added by its own task."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest


class TestHotRead:
    def test_finds_hit_in_memory_md(self, tmp_path: Path):
        from flyn_memory_router.adapters.hot_read import HotRead
        md = tmp_path / "MEMORY.md"
        md.write_text(
            "# MEMORY\n\n## Beth\nBeth Kukla, COO Cora, PM for OL.\n\n"
            "## Eric\nEric Schneider, tech lead at FPS.\n"
        )
        pin_file = tmp_path / "pins.json"
        pin_file.write_text("[]")
        hr = HotRead(memory_md=md, pin_file=pin_file)
        hits = asyncio.run(hr.query("Beth"))
        assert any("Beth Kukla" in h.text for h in hits)

    def test_returns_empty_when_no_match(self, tmp_path: Path):
        from flyn_memory_router.adapters.hot_read import HotRead
        md = tmp_path / "MEMORY.md"
        md.write_text("# MEMORY\n\n## A\nsomething\n")
        pin_file = tmp_path / "pins.json"
        pin_file.write_text("[]")
        hits = asyncio.run(HotRead(memory_md=md, pin_file=pin_file).query("nonexistent"))
        assert hits == []

    def test_pins_have_higher_score_than_sections(self, tmp_path: Path):
        from flyn_memory_router.adapters.hot_read import HotRead
        md = tmp_path / "MEMORY.md"
        md.write_text("# MEMORY\n\n## Beth\nBeth in a section.\n")
        pin_file = tmp_path / "pins.json"
        pin_file.write_text(json.dumps([{"subject": "Beth", "body": "Beth is pinned", "ts": 0}]))
        hits = asyncio.run(HotRead(memory_md=md, pin_file=pin_file).query("Beth"))
        assert hits[0].source == "hot/pins"

    def test_missing_files_gracefully_return_empty(self, tmp_path: Path):
        from flyn_memory_router.adapters.hot_read import HotRead
        hits = asyncio.run(HotRead(
            memory_md=tmp_path / "missing.md",
            pin_file=tmp_path / "missing.json",
        ).query("anything"))
        assert hits == []
```

- [ ] **Step 2: Run test, expect FAIL**

```bash
cd deploy/memory-router && python -m pytest tests/unit/test_read_adapters.py::TestHotRead -v
```

- [ ] **Step 3: Implement `hot_read.py`**

```python
"""Hot-tier read: grep MEMORY.md sections and pins.json for q."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..types import Hit


class HotRead:
    name = "hot"
    read_timeout = 1.0
    default_included = True

    def __init__(self, memory_md: Path, pin_file: Path) -> None:
        self._md = memory_md
        self._pins = pin_file

    async def query(self, q: str, top_k: int = 10) -> list[Hit]:
        results: list[Hit] = []
        results.extend(self._scan_pins(q, top_k))
        results.extend(self._scan_sections(q, top_k))
        results.sort(key=lambda h: h.score, reverse=True)
        return results[:top_k]

    def _scan_pins(self, q: str, top_k: int) -> list[Hit]:
        if not self._pins.exists():
            return []
        try:
            pins = json.loads(self._pins.read_text())
        except (json.JSONDecodeError, OSError):
            return []
        ql = q.lower()
        hits: list[Hit] = []
        for p in pins:
            text = f"{p.get('subject', '')}: {p.get('body', '')}".strip(": ")
            if ql in text.lower():
                hits.append(Hit(
                    text=text,
                    source="hot/pins",
                    score=1.0,
                    metadata={"pin_subject": p.get("subject", ""), "ts": p.get("ts", 0)},
                ))
        return hits[:top_k]

    def _scan_sections(self, q: str, top_k: int) -> list[Hit]:
        if not self._md.exists():
            return []
        try:
            content = self._md.read_text()
        except OSError:
            return []
        ql = q.lower()
        sections = re.split(r"(?m)^##\s+", content)
        hits: list[Hit] = []
        for section in sections[1:]:
            head, _, body = section.partition("\n")
            body_text = (head + "\n" + body).strip()
            if ql in body_text.lower():
                count = body_text.lower().count(ql)
                hits.append(Hit(
                    text=body_text[:1000],
                    source="hot/MEMORY.md",
                    score=0.5 + min(0.4, count * 0.1),
                    metadata={"section": head.strip()},
                ))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]
```

- [ ] **Step 4: Run tests, expect PASS, then commit**

```bash
cd deploy/memory-router && python -m pytest tests/unit/test_read_adapters.py::TestHotRead -v
git add deploy/memory-router/flyn_memory_router/adapters/hot_read.py \
        deploy/memory-router/tests/unit/test_read_adapters.py
git commit -m "feat(memory-router): hot_read adapter"
```

---

### Task 29: warm_read adapter

**Files:**
- Create: `deploy/memory-router/flyn_memory_router/adapters/warm_read.py`
- Modify: `deploy/memory-router/tests/unit/test_read_adapters.py`
- Create: `deploy/memory-router/tests/fixtures/mock_graphiti_search.json`

- [ ] **Step 1: Create fixture**

`tests/fixtures/mock_graphiti_search.json`:
```json
{
  "results": [
    {
      "uuid": "ep-1",
      "name": "Beth intro",
      "summary": "Beth Kukla, co-founder + COO Cora; PM for OpenLit phase 2.",
      "score": 0.94
    },
    {
      "uuid": "ep-2",
      "name": "Linear sync update",
      "summary": "73/124 OL questions synced; remaining 51 blocked.",
      "score": 0.81
    }
  ]
}
```

- [ ] **Step 2: Write failing tests** (append to `tests/unit/test_read_adapters.py`)

```python
class TestWarmRead:
    @pytest.mark.asyncio
    async def test_calls_graphiti_and_returns_hits(self, tmp_path: Path):
        from flyn_memory_router.adapters.warm_read import WarmRead
        import httpx

        fixture_path = Path(__file__).parent.parent / "fixtures" / "mock_graphiti_search.json"
        fixture = json.loads(fixture_path.read_text())

        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/search"
            assert request.url.params["q"] == "Beth"
            return httpx.Response(200, json=fixture)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            wr = WarmRead(
                graphiti_url="http://test-graphiti",
                workspace_memory_dir=tmp_path,
                http=client,
            )
            hits = await wr.query("Beth")

        graphiti_hits = [h for h in hits if h.source == "warm/graphiti"]
        assert len(graphiti_hits) >= 2
        assert graphiti_hits[0].metadata.get("canonical_id") == "ep-1"

    @pytest.mark.asyncio
    async def test_workspace_memory_grep_returns_hits(self, tmp_path: Path):
        from flyn_memory_router.adapters.warm_read import WarmRead
        import httpx

        (tmp_path / "2026-05-13.md").write_text("Beth status: PM. Linear: 73/124.")

        async def handler(request):
            return httpx.Response(200, json={"results": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            wr = WarmRead(graphiti_url="http://t", workspace_memory_dir=tmp_path, http=client)
            hits = await wr.query("Beth")
        ws_hits = [h for h in hits if h.source == "warm/workspace"]
        assert len(ws_hits) == 1
        assert "Linear" in ws_hits[0].text

    @pytest.mark.asyncio
    async def test_graphiti_5xx_does_not_block_workspace_grep(self, tmp_path: Path):
        from flyn_memory_router.adapters.warm_read import WarmRead
        import httpx

        (tmp_path / "x.md").write_text("Beth note")

        async def handler(request):
            return httpx.Response(503, json={"detail": "unavailable"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            wr = WarmRead(graphiti_url="http://t", workspace_memory_dir=tmp_path, http=client)
            hits = await wr.query("Beth")
        assert any(h.source == "warm/workspace" for h in hits)
```

- [ ] **Step 3: Run test, expect FAIL**

```bash
cd deploy/memory-router && python -m pytest tests/unit/test_read_adapters.py::TestWarmRead -v
```

- [ ] **Step 4: Implement `warm_read.py`**

```python
"""Warm-tier read: Graphiti REST + workspace/memory/*.md grep."""
from __future__ import annotations

from pathlib import Path

import httpx

from ..types import Hit


class WarmRead:
    name = "warm"
    read_timeout = 2.0
    default_included = True

    def __init__(
        self,
        graphiti_url: str,
        workspace_memory_dir: Path,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = graphiti_url.rstrip("/")
        self._dir = workspace_memory_dir
        self._http = http or httpx.AsyncClient(timeout=2.0)
        self._owns_http = http is None

    async def query(self, q: str, top_k: int = 10) -> list[Hit]:
        graphiti = await self._graphiti(q, top_k)
        workspace = self._workspace(q, top_k)
        combined = graphiti + workspace
        combined.sort(key=lambda h: h.score, reverse=True)
        return combined[:top_k]

    async def _graphiti(self, q: str, top_k: int) -> list[Hit]:
        try:
            resp = await self._http.get(
                f"{self._url}/api/search",
                params={"q": q, "limit": top_k},
                timeout=self.read_timeout,
            )
            if resp.status_code >= 400:
                return []
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            return []
        hits: list[Hit] = []
        for ep in data.get("results", []):
            text = ep.get("summary") or ep.get("name") or ""
            if not text:
                continue
            hits.append(Hit(
                text=text,
                source="warm/graphiti",
                score=float(ep.get("score", 0.5)),
                metadata={
                    "canonical_id": ep.get("uuid"),
                    "name": ep.get("name"),
                },
            ))
        return hits

    def _workspace(self, q: str, top_k: int) -> list[Hit]:
        if not self._dir.exists():
            return []
        ql = q.lower()
        hits: list[Hit] = []
        for md in self._dir.glob("*.md"):
            try:
                content = md.read_text()
            except OSError:
                continue
            if ql not in content.lower():
                continue
            idx = content.lower().find(ql)
            start = max(0, idx - 200)
            end = min(len(content), idx + 200 + len(q))
            snippet = content[start:end].strip()
            count = content.lower().count(ql)
            hits.append(Hit(
                text=snippet,
                source="warm/workspace",
                score=0.5 + min(0.4, count * 0.1),
                metadata={"file": str(md)},
            ))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()
```

- [ ] **Step 5: Run tests, expect PASS, then commit**

```bash
cd deploy/memory-router && python -m pytest tests/unit/test_read_adapters.py::TestWarmRead -v
git add deploy/memory-router/flyn_memory_router/adapters/warm_read.py \
        deploy/memory-router/tests/unit/test_read_adapters.py \
        deploy/memory-router/tests/fixtures/mock_graphiti_search.json
git commit -m "feat(memory-router): warm_read adapter"
```

---

### Task 30: cool_read + cold_read + lesson_read adapters

**Files:**
- Create: `deploy/memory-router/flyn_memory_router/adapters/cool_read.py`
- Create: `deploy/memory-router/flyn_memory_router/adapters/cold_read.py`
- Create: `deploy/memory-router/flyn_memory_router/adapters/lesson_read.py`
- Modify: `deploy/memory-router/tests/unit/test_read_adapters.py`

- [ ] **Step 1: Write failing tests** (append to `test_read_adapters.py`)

```python
class TestCoolRead:
    def test_grep_daily_rollups(self, tmp_path: Path):
        from flyn_memory_router.adapters.cool_read import CoolRead
        (tmp_path / "2026-05-10.md").write_text("Beth pinged about Linear.")
        (tmp_path / "2026-05-11.md").write_text("Eric posted Pearl Platform update.")
        hits = asyncio.run(CoolRead(memory_dir=tmp_path).query("Beth"))
        assert len(hits) == 1
        assert hits[0].source == "cool/rollup"
        assert "2026-05-10" in hits[0].metadata.get("date", "")


class TestColdRead:
    def test_line_grep_captures_index(self, tmp_path: Path):
        from flyn_memory_router.adapters.cold_read import ColdRead
        idx = tmp_path / "captures_index.jsonl"
        idx.write_text(
            json.dumps({"ts": "2026-04-01", "subject": "Beth onboard", "summary": "..."}) + "\n"
            + json.dumps({"ts": "2026-04-02", "subject": "Eric onboard", "summary": "..."}) + "\n"
        )
        hits = asyncio.run(ColdRead(index_path=idx).query("Beth"))
        assert len(hits) == 1
        assert hits[0].source == "cold/captures"


class TestLessonRead:
    def test_grep_knowledge_dir(self, tmp_path: Path):
        from flyn_memory_router.adapters.lesson_read import LessonRead
        (tmp_path / "lesson-mcp-failure.md").write_text(
            "## Lesson 2026-04-21\nMCP tool_use fails with codex; use REST."
        )
        hits = asyncio.run(LessonRead(knowledge_dir=tmp_path).query("MCP"))
        assert len(hits) == 1
        assert hits[0].source == "lesson/KNOWLEDGE"
```

- [ ] **Step 2: Run tests, expect FAIL, then implement the three adapters**

`adapters/cool_read.py`:

```python
"""Cool-tier read: grep daily roll-up files in workspace/memory/."""
from __future__ import annotations

import re
from pathlib import Path

from ..types import Hit

_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")


class CoolRead:
    name = "cool"
    read_timeout = 1.0
    default_included = True

    def __init__(self, memory_dir: Path) -> None:
        self._dir = memory_dir

    async def query(self, q: str, top_k: int = 10) -> list[Hit]:
        if not self._dir.exists():
            return []
        ql = q.lower()
        hits: list[Hit] = []
        for md in sorted(self._dir.glob("????-??-??.md"), reverse=True):
            m = _DATE_RE.match(md.name)
            if not m:
                continue
            try:
                content = md.read_text()
            except OSError:
                continue
            if ql not in content.lower():
                continue
            idx = content.lower().find(ql)
            snippet = content[max(0, idx - 150):idx + 350].strip()
            hits.append(Hit(
                text=snippet,
                source="cool/rollup",
                score=0.6,
                metadata={"date": m.group(1), "file": str(md)},
            ))
            if len(hits) >= top_k:
                break
        return hits
```

`adapters/cold_read.py`:

```python
"""Cold-tier read: line-grep the captures index JSONL."""
from __future__ import annotations

import json
from pathlib import Path

from ..types import Hit


class ColdRead:
    name = "cold"
    read_timeout = 1.0
    default_included = True

    def __init__(self, index_path: Path) -> None:
        self._idx = index_path

    async def query(self, q: str, top_k: int = 10) -> list[Hit]:
        if not self._idx.exists():
            return []
        ql = q.lower()
        hits: list[Hit] = []
        try:
            for line in self._idx.read_text().splitlines():
                if ql not in line.lower():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                text = f"{rec.get('subject', '')}: {rec.get('summary', '')}".strip(": ")
                if not text:
                    continue
                hits.append(Hit(
                    text=text,
                    source="cold/captures",
                    score=0.4,
                    metadata={"ts": rec.get("ts"), "capture_id": rec.get("id")},
                ))
                if len(hits) >= top_k:
                    break
        except OSError:
            return []
        return hits
```

`adapters/lesson_read.py`:

```python
"""Lesson-tier read: grep KNOWLEDGE/*.md."""
from __future__ import annotations

from pathlib import Path

from ..types import Hit


class LessonRead:
    name = "lesson"
    read_timeout = 1.0
    default_included = True

    def __init__(self, knowledge_dir: Path) -> None:
        self._dir = knowledge_dir

    async def query(self, q: str, top_k: int = 10) -> list[Hit]:
        if not self._dir.exists():
            return []
        ql = q.lower()
        hits: list[Hit] = []
        for md in sorted(self._dir.glob("*.md")):
            try:
                content = md.read_text()
            except OSError:
                continue
            if ql not in content.lower():
                continue
            idx = content.lower().find(ql)
            snippet = content[max(0, idx - 200):idx + 400].strip()
            count = content.lower().count(ql)
            hits.append(Hit(
                text=snippet,
                source="lesson/KNOWLEDGE",
                score=0.5 + min(0.4, count * 0.1),
                metadata={"file": str(md)},
            ))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]
```

- [ ] **Step 3: Run tests, expect PASS, then commit**

```bash
cd deploy/memory-router && python -m pytest tests/unit/test_read_adapters.py -v
git add deploy/memory-router/flyn_memory_router/adapters/cool_read.py \
        deploy/memory-router/flyn_memory_router/adapters/cold_read.py \
        deploy/memory-router/flyn_memory_router/adapters/lesson_read.py \
        deploy/memory-router/tests/unit/test_read_adapters.py
git commit -m "feat(memory-router): cool/cold/lesson_read adapters"
```

---

### Task 31: reference_read adapter

**Files:**
- Create: `deploy/memory-router/flyn_memory_router/adapters/reference_read.py`
- Create: `deploy/memory-router/tests/fixtures/reference_vault/` (mini vault)
- Modify: `deploy/memory-router/tests/unit/test_read_adapters.py`

- [ ] **Step 1: Build fixture vault**

```bash
mkdir -p deploy/memory-router/tests/fixtures/reference_vault/wiki/people
mkdir -p deploy/memory-router/tests/fixtures/reference_vault/wiki/projects
mkdir -p deploy/memory-router/tests/fixtures/reference_vault/raw
```

`tests/fixtures/reference_vault/wiki/index.md`:

```markdown
# Index

## Entities — People
- [[beth]]

## Entities — Projects
- [[openlit]]
```

`tests/fixtures/reference_vault/wiki/people/beth.md`:

```markdown
---
type: person
aliases: [Beth, Kukla]
---
# Beth

Beth Kukla, co-founder + COO Cora. PM for [[openlit]].
```

`tests/fixtures/reference_vault/wiki/projects/openlit.md`:

```markdown
---
type: project
---
# OpenLit

Phase 2. Wiki at :8200, MCP registered. Linear sync 73/124.
```

- [ ] **Step 2: Write failing tests** (append to `test_read_adapters.py`)

```python
class TestReferenceRead:
    @pytest.fixture
    def vault(self) -> Path:
        return Path(__file__).parent.parent / "fixtures" / "reference_vault"

    def test_reads_index_first_then_walks(self, vault: Path):
        from flyn_memory_router.adapters.reference_read import ReferenceRead
        hits = asyncio.run(ReferenceRead(vault=vault).query("Beth"))
        assert any(h.metadata.get("file", "").endswith("beth.md") for h in hits)
        assert all(h.source == "reference/wiki" for h in hits)

    def test_follows_wikilinks(self, vault: Path):
        from flyn_memory_router.adapters.reference_read import ReferenceRead
        hits = asyncio.run(ReferenceRead(vault=vault).query("openlit"))
        assert any(h.metadata.get("file", "").endswith("openlit.md") for h in hits)

    def test_returns_empty_without_index(self, tmp_path: Path):
        from flyn_memory_router.adapters.reference_read import ReferenceRead
        assert asyncio.run(ReferenceRead(vault=tmp_path).query("anything")) == []
```

- [ ] **Step 3: Run tests, expect FAIL**

```bash
cd deploy/memory-router && python -m pytest tests/unit/test_read_adapters.py::TestReferenceRead -v
```

- [ ] **Step 4: Implement `reference_read.py`**

```python
"""Reference-tier read: walk the Karpathy LLM Wiki at vault/wiki/.

Strategy per the vault's CLAUDE.md schema: read wiki/index.md first to
get the catalog, then walk wiki/*.md for text matches. Follow [[wikilinks]]
to surface adjacent pages.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..types import Hit

_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


class ReferenceRead:
    name = "reference"
    read_timeout = 1.5
    default_included = True

    def __init__(self, vault: Path) -> None:
        self._vault = vault
        self._wiki = vault / "wiki"

    async def query(self, q: str, top_k: int = 10) -> list[Hit]:
        if not self._wiki.exists():
            return []
        index_path = self._wiki / "index.md"
        if not index_path.exists():
            return []                                  # index-first rule
        ql = q.lower()
        candidates: list[Path] = []

        for md in self._wiki.rglob("*.md"):
            if md.name in ("log.md", "index.md"):
                continue
            try:
                if ql in md.read_text().lower():
                    candidates.append(md)
            except OSError:
                continue

        adjacent: set[Path] = set()
        for cand in candidates:
            try:
                content = cand.read_text()
            except OSError:
                continue
            for match in _WIKILINK_RE.finditer(content):
                target = match.group(1).strip()
                for h in self._wiki.rglob(f"{target}.md"):
                    if h != cand:
                        adjacent.add(h)

        hits: list[Hit] = []
        for path in candidates:
            try:
                content = path.read_text()
            except OSError:
                continue
            idx = content.lower().find(ql)
            snippet = content[max(0, idx - 200):idx + 400].strip()
            hits.append(Hit(
                text=snippet,
                source="reference/wiki",
                score=0.8,
                metadata={"file": str(path), "via": "direct_match"},
            ))
        for path in adjacent:
            if path in candidates:
                continue
            try:
                content = path.read_text()
            except OSError:
                continue
            hits.append(Hit(
                text=content[:500].strip(),
                source="reference/wiki",
                score=0.5,
                metadata={"file": str(path), "via": "wikilink"},
            ))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]
```

- [ ] **Step 5: Run tests, expect PASS, then commit**

```bash
cd deploy/memory-router && python -m pytest tests/unit/test_read_adapters.py::TestReferenceRead -v
git add deploy/memory-router/flyn_memory_router/adapters/reference_read.py \
        deploy/memory-router/tests/unit/test_read_adapters.py \
        deploy/memory-router/tests/fixtures/reference_vault/
git commit -m "feat(memory-router): reference_read adapter (Karpathy LLM Wiki)"
```

---

### Task 32: user_read adapter

**Files:**
- Create: `deploy/memory-router/flyn_memory_router/adapters/user_read.py`
- Create: `deploy/memory-router/tests/fixtures/auto_memory/` (fixture)
- Modify: `deploy/memory-router/tests/unit/test_read_adapters.py`
- Modify: `deploy/memory-router/pyproject.toml` (add pyyaml dep)

- [ ] **Step 1: Build fixture**

`tests/fixtures/auto_memory/MEMORY.md`:

```markdown
- [Beth role](feedback_beth.md) — Beth = COO Cora, PM for OL
- [no autostart](feedback_no_unrequested_autostart.md) — don't add launchd login items
```

`tests/fixtures/auto_memory/feedback_beth.md`:

```markdown
---
name: beth-role
description: Beth Kukla, COO of Cora and PM for OpenLiteracy phase 2.
metadata:
  type: user
---
Beth Kukla, COO of Cora. PM for OL phase 2. Telegram chat_id 7434192034.
```

`tests/fixtures/auto_memory/feedback_no_unrequested_autostart.md`:

```markdown
---
name: no-unrequested-autostart
description: don't add launchd login items unless asked
metadata:
  type: feedback
---
Don't auto-add launchd login items.
```

- [ ] **Step 2: Write failing tests** (append to `test_read_adapters.py`)

```python
class TestUserRead:
    @pytest.fixture
    def memdir(self) -> Path:
        return Path(__file__).parent.parent / "fixtures" / "auto_memory"

    def test_grep_finds_match(self, memdir: Path):
        from flyn_memory_router.adapters.user_read import UserRead
        hits = asyncio.run(UserRead(auto_memory_dir=memdir).query("Beth"))
        assert any("Beth" in h.text for h in hits)
        assert all(h.source == "user/auto-memory" for h in hits)

    def test_frontmatter_aware_metadata(self, memdir: Path):
        from flyn_memory_router.adapters.user_read import UserRead
        hits = asyncio.run(UserRead(auto_memory_dir=memdir).query("Beth"))
        beth_hits = [h for h in hits if h.metadata.get("name") == "beth-role"]
        assert beth_hits
        assert beth_hits[0].metadata.get("memory_type") == "user"

    def test_skips_memory_md_index_file(self, memdir: Path):
        from flyn_memory_router.adapters.user_read import UserRead
        hits = asyncio.run(UserRead(auto_memory_dir=memdir).query("Beth"))
        assert all("MEMORY.md" not in h.metadata.get("file", "") for h in hits)
```

- [ ] **Step 3: Run tests, expect FAIL**

```bash
cd deploy/memory-router && python -m pytest tests/unit/test_read_adapters.py::TestUserRead -v
```

- [ ] **Step 4: Implement `user_read.py`**

```python
"""User-tier read: Claude Code auto-memory at ~/.claude/projects/.../memory/."""
from __future__ import annotations

from pathlib import Path

import yaml

from ..types import Hit


def _split_frontmatter(content: str) -> tuple[dict, str]:
    if not content.startswith("---\n"):
        return {}, content
    end = content.find("\n---\n", 4)
    if end < 0:
        return {}, content
    try:
        meta = yaml.safe_load(content[4:end]) or {}
    except yaml.YAMLError:
        meta = {}
    body = content[end + 5:]
    return meta, body


class UserRead:
    name = "user"
    read_timeout = 1.0
    default_included = True

    def __init__(self, auto_memory_dir: Path) -> None:
        self._dir = auto_memory_dir

    async def query(self, q: str, top_k: int = 10) -> list[Hit]:
        if not self._dir.exists():
            return []
        ql = q.lower()
        hits: list[Hit] = []
        for md in sorted(self._dir.glob("*.md")):
            if md.name == "MEMORY.md":
                continue
            try:
                content = md.read_text()
            except OSError:
                continue
            meta, body = _split_frontmatter(content)
            if ql not in body.lower() and ql not in str(meta).lower():
                continue
            idx_in_body = body.lower().find(ql)
            if idx_in_body >= 0:
                snippet = body[max(0, idx_in_body - 200):idx_in_body + 400].strip()
            else:
                snippet = body.strip()[:500]
            hits.append(Hit(
                text=snippet,
                source="user/auto-memory",
                score=0.7,
                metadata={
                    "file": str(md),
                    "name": meta.get("name", ""),
                    "memory_type": (meta.get("metadata") or {}).get("type", ""),
                    "description": meta.get("description", ""),
                },
            ))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]
```

- [ ] **Step 5: Add pyyaml to `pyproject.toml`**

In `[project]` dependencies, add: `"pyyaml>=6.0",`. Then:

```bash
cd deploy/memory-router && pip install -e .
```

- [ ] **Step 6: Run tests, expect PASS, then commit**

```bash
cd deploy/memory-router && python -m pytest tests/unit/test_read_adapters.py::TestUserRead -v
git add deploy/memory-router/flyn_memory_router/adapters/user_read.py \
        deploy/memory-router/tests/unit/test_read_adapters.py \
        deploy/memory-router/tests/fixtures/auto_memory/ \
        deploy/memory-router/pyproject.toml
git commit -m "feat(memory-router): user_read adapter + pyyaml dep"
```

---

### Task 33: ol_wiki_read adapter

**Files:**
- Create: `deploy/memory-router/flyn_memory_router/adapters/ol_wiki_read.py`
- Modify: `deploy/memory-router/tests/unit/test_read_adapters.py`

- [ ] **Step 1: Write failing tests** (append to `test_read_adapters.py`)

```python
class TestOLWikiRead:
    @pytest.mark.asyncio
    async def test_sends_pin_header_and_returns_hits(self):
        from flyn_memory_router.adapters.ol_wiki_read import OLWikiRead
        import httpx

        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers.get("X-OL-Wiki-Pin") == "1080"
            assert request.url.params["q"] == "Linear"
            return httpx.Response(200, json={"results": [
                {"id": "Q-42", "section": "I", "question": "Linear plan?",
                 "answer": "Free tier blocks at 250.", "score": 0.85},
            ]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            r = OLWikiRead(url="http://test-olwiki", pin="1080", http=client)
            hits = await r.query("Linear")
        assert len(hits) == 1
        assert hits[0].source == "ol_wiki"
        assert hits[0].metadata.get("question_id") == "Q-42"

    @pytest.mark.asyncio
    async def test_5xx_returns_empty(self):
        from flyn_memory_router.adapters.ol_wiki_read import OLWikiRead
        import httpx

        async def handler(request):
            return httpx.Response(503, json={"detail": "down"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            r = OLWikiRead(url="http://t", pin="1080", http=client)
            assert await r.query("anything") == []
```

- [ ] **Step 2: Run tests, expect FAIL**

```bash
cd deploy/memory-router && python -m pytest tests/unit/test_read_adapters.py::TestOLWikiRead -v
```

- [ ] **Step 3: Implement `ol_wiki_read.py`**

```python
"""ol-wiki read: REST search at /search with PIN header."""
from __future__ import annotations

import httpx

from ..types import Hit


class OLWikiRead:
    name = "ol_wiki"
    read_timeout = 2.0
    default_included = True

    def __init__(self, url: str, pin: str, http: httpx.AsyncClient | None = None) -> None:
        self._url = url.rstrip("/")
        self._pin = pin
        self._http = http or httpx.AsyncClient(timeout=self.read_timeout)
        self._owns_http = http is None

    async def query(self, q: str, top_k: int = 10) -> list[Hit]:
        try:
            resp = await self._http.get(
                f"{self._url}/search",
                params={"q": q, "limit": top_k},
                headers={"X-OL-Wiki-Pin": self._pin},
                timeout=self.read_timeout,
            )
            if resp.status_code >= 400:
                return []
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            return []

        hits: list[Hit] = []
        for rec in data.get("results", []):
            text = f"{rec.get('question', '')}\n{rec.get('answer', '')}".strip()
            if not text:
                continue
            hits.append(Hit(
                text=text,
                source="ol_wiki",
                score=float(rec.get("score", 0.5)),
                metadata={
                    "question_id": rec.get("id"),
                    "section": rec.get("section"),
                },
            ))
        return hits

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()
```

- [ ] **Step 4: Run tests, expect PASS, then commit**

```bash
cd deploy/memory-router && python -m pytest tests/unit/test_read_adapters.py::TestOLWikiRead -v
git add deploy/memory-router/flyn_memory_router/adapters/ol_wiki_read.py \
        deploy/memory-router/tests/unit/test_read_adapters.py
git commit -m "feat(memory-router): ol_wiki_read adapter"
```

---

### Task 34: ocw_mem_read + lossless_read adapters

**Files:**
- Create: `deploy/memory-router/flyn_memory_router/adapters/ocw_mem_read.py`
- Create: `deploy/memory-router/flyn_memory_router/adapters/lossless_read.py`
- Modify: `deploy/memory-router/tests/unit/test_read_adapters.py`

The ocw_mem adapter uses `subprocess.run` wrapped in `asyncio.to_thread` — keeps the adapter async-friendly without raw process-spawning APIs.

- [ ] **Step 1: Write failing tests** (append to `test_read_adapters.py`)

```python
class TestOCWMemRead:
    @pytest.mark.asyncio
    async def test_runs_search_command_and_parses_json(self, monkeypatch):
        from flyn_memory_router.adapters import ocw_mem_read
        import subprocess

        fake_stdout = json.dumps({
            "results": [
                {"text": "Beth = COO", "score": 0.7, "file": "/path/MEMORY.md", "line": 42},
            ]
        })

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args[0], returncode=0, stdout=fake_stdout, stderr="",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)
        r = ocw_mem_read.OCWMemRead()
        hits = await r.query("Beth")
        assert len(hits) == 1
        assert hits[0].source == "ocw_mem"
        assert hits[0].metadata.get("line") == 42

    @pytest.mark.asyncio
    async def test_nonzero_returncode_yields_empty(self, monkeypatch):
        from flyn_memory_router.adapters import ocw_mem_read
        import subprocess

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(args=args[0], returncode=1, stdout="", stderr="boom")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert await ocw_mem_read.OCWMemRead().query("Beth") == []

    @pytest.mark.asyncio
    async def test_missing_binary_returns_empty(self, monkeypatch):
        from flyn_memory_router.adapters import ocw_mem_read
        import subprocess

        def fake_run(*args, **kwargs):
            raise FileNotFoundError("no such binary")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert await ocw_mem_read.OCWMemRead().query("Beth") == []


class TestLosslessRead:
    def test_default_excluded(self):
        from flyn_memory_router.adapters.lossless_read import LosslessRead
        assert LosslessRead().default_included is False

    def test_grep_session_logs(self, tmp_path: Path):
        from flyn_memory_router.adapters.lossless_read import LosslessRead
        (tmp_path / "session-2026-05-13.jsonl").write_text(
            json.dumps({"role": "user", "content": "What's Beth's role?"}) + "\n"
            + json.dumps({"role": "assistant", "content": "Beth is COO Cora."}) + "\n"
        )
        hits = asyncio.run(LosslessRead(sessions_dir=tmp_path).query("Beth"))
        assert len(hits) == 2
        assert all(h.source == "lossless" for h in hits)
```

- [ ] **Step 2: Run tests, expect FAIL**

```bash
cd deploy/memory-router && python -m pytest tests/unit/test_read_adapters.py::TestOCWMemRead tests/unit/test_read_adapters.py::TestLosslessRead -v
```

- [ ] **Step 3: Implement `ocw_mem_read.py`**

```python
"""ocw_mem read: runs `openclaw memory search --json` via subprocess.run.

Uses asyncio.to_thread to keep the adapter async-friendly without
calling raw process-spawn APIs. subprocess.run with a list argv is
shell-safe (no shell=True).
"""
from __future__ import annotations

import asyncio
import json
import subprocess

from ..types import Hit


class OCWMemRead:
    name = "ocw_mem"
    read_timeout = 3.0
    default_included = False

    def __init__(self, binary: str = "openclaw") -> None:
        self._bin = binary

    async def query(self, q: str, top_k: int = 10) -> list[Hit]:
        argv = [self._bin, "memory", "search",
                "--query", q, "--limit", str(top_k), "--json"]
        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                argv,
                capture_output=True,
                text=True,
                timeout=self.read_timeout,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []
        if completed.returncode != 0:
            return []
        try:
            data = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            return []

        hits: list[Hit] = []
        for rec in data.get("results", []):
            text = rec.get("text") or ""
            if not text:
                continue
            hits.append(Hit(
                text=text,
                source="ocw_mem",
                score=float(rec.get("score", 0.5)),
                metadata={
                    "file": rec.get("file"),
                    "line": rec.get("line"),
                },
            ))
        return hits
```

- [ ] **Step 4: Implement `lossless_read.py`**

```python
"""lossless read: grep Lossless Claw plugin's on-disk session logs."""
from __future__ import annotations

import json
from pathlib import Path

from ..types import Hit


class LosslessRead:
    name = "lossless"
    read_timeout = 3.0
    default_included = False

    def __init__(self, sessions_dir: Path | None = None) -> None:
        self._dir = sessions_dir or (
            Path.home() / ".openclaw" / "plugins" / "lossless-claw" / "sessions"
        )

    async def query(self, q: str, top_k: int = 10) -> list[Hit]:
        if not self._dir.exists():
            return []
        ql = q.lower()
        hits: list[Hit] = []
        for jsonl in sorted(self._dir.glob("*.jsonl"), reverse=True):
            try:
                for line in jsonl.read_text().splitlines():
                    if ql not in line.lower():
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    content = rec.get("content") or ""
                    if not content or ql not in content.lower():
                        continue
                    hits.append(Hit(
                        text=content,
                        source="lossless",
                        score=0.3,
                        metadata={
                            "session_file": str(jsonl),
                            "role": rec.get("role", ""),
                        },
                    ))
                    if len(hits) >= top_k:
                        return hits
            except OSError:
                continue
        return hits
```

- [ ] **Step 5: Run tests, expect PASS, then commit**

```bash
cd deploy/memory-router && python -m pytest tests/unit/test_read_adapters.py -v
git add deploy/memory-router/flyn_memory_router/adapters/ocw_mem_read.py \
        deploy/memory-router/flyn_memory_router/adapters/lossless_read.py \
        deploy/memory-router/tests/unit/test_read_adapters.py
git commit -m "feat(memory-router): ocw_mem_read + lossless_read adapters"
```

---

## Phase 0e — Routes + orchestration (Tasks 35–37)

### Task 35: /api/memory/query route + async query() orchestrator

**Files:**
- Modify: `deploy/memory-router/flyn_memory_router/query.py` (add async orchestrator)
- Modify: `deploy/memory-router/flyn_memory_router/server.py` (add route)
- Create: `deploy/memory-router/tests/integration/test_query_roundtrip.py`

- [ ] **Step 1: Write failing integration test**

```python
# deploy/memory-router/tests/integration/test_query_roundtrip.py
"""Integration: real FastAPI app, fake adapters, full POST cycle."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from flyn_memory_router.server import build_app
from flyn_memory_router.types import Hit


class _FakeRead:
    def __init__(self, name: str, hits: list[Hit] | None = None,
                 default_included: bool = True, timeout: float = 1.0):
        self.name = name
        self.default_included = default_included
        self.read_timeout = timeout
        self._hits = hits or []

    async def query(self, q: str, top_k: int = 10) -> list[Hit]:
        return self._hits


@pytest.fixture
def app_with_fakes(monkeypatch, tmp_path):
    from flyn_memory_router import query as query_module
    fakes = [
        _FakeRead("hot", [Hit(text="Beth Kukla, COO", source="hot/MEMORY.md", score=0.9, metadata={})]),
        _FakeRead("warm", [Hit(text="Beth episode", source="warm/graphiti", score=0.8,
                                metadata={"canonical_id": "ep-1"})]),
        _FakeRead("reference", [Hit(text="Beth: see [[openlit]]", source="reference/wiki",
                                     score=0.7, metadata={})]),
    ]
    monkeypatch.setattr(query_module, "_load_adapters", lambda include, exclude: fakes)
    monkeypatch.setenv("FLYN_MEMORY_ROUTER_HOME", str(tmp_path / "router"))
    monkeypatch.setenv("FLYN_WORKSPACE", str(tmp_path / "ws"))
    app = build_app()
    return TestClient(app)


def test_query_returns_merged_hits(app_with_fakes):
    resp = app_with_fakes.post("/api/memory/query", json={"q": "Beth"})
    assert resp.status_code == 200
    body = resp.json()
    assert "query_id" in body
    assert body["query_id"].startswith("q-")
    assert len(body["hits"]) >= 1


def test_query_top_k_clamps_results(app_with_fakes):
    resp = app_with_fakes.post("/api/memory/query", json={"q": "Beth", "top_k": 1})
    assert resp.status_code == 200
    assert len(resp.json()["hits"]) == 1


def test_query_validation_rejects_empty_q(app_with_fakes):
    resp = app_with_fakes.post("/api/memory/query", json={"q": ""})
    assert resp.status_code == 422
```

- [ ] **Step 2: Run test, expect FAIL**

```bash
cd deploy/memory-router && python -m pytest tests/integration/test_query_roundtrip.py -v
```

- [ ] **Step 3: Extend `query.py` — add async orchestrator** (append to existing pure functions)

```python
# --- async orchestrator added in Task 35 ---
import asyncio
import importlib
import time
import uuid as _uuid

from .config import Config, READ_SOURCES, ReadSourceConfig
from .types import QueryResult, SourceError


def _resolve_class(cls_path: str):
    module_path, _, cls_name = cls_path.partition(":")
    module = importlib.import_module(module_path)
    return getattr(module, cls_name)


def _construct(rsc: ReadSourceConfig, cfg: Config):
    cls = _resolve_class(rsc.cls_path)
    name = rsc.name
    if name == "hot":
        return cls(memory_md=cfg.memory_md, pin_file=cfg.pin_file)
    if name == "warm":
        return cls(graphiti_url=cfg.graphiti_url, workspace_memory_dir=cfg.workspace_memory_dir)
    if name == "cool":
        return cls(memory_dir=cfg.workspace_memory_dir)
    if name == "cold":
        return cls(index_path=cfg.captures_index)
    if name == "lesson":
        return cls(knowledge_dir=cfg.knowledge_dir)
    if name == "reference":
        return cls(vault=cfg.reference_vault)
    if name == "user":
        return cls(auto_memory_dir=cfg.auto_memory_dir)
    if name == "ol_wiki":
        return cls(url=cfg.ol_wiki_url, pin=cfg.ol_wiki_pin)
    if name == "ocw_mem":
        return cls()
    if name == "lossless":
        return cls()
    raise KeyError(f"No constructor wiring for adapter {name!r}")


def _load_adapters(include: list[str] | None, exclude: list[str] | None):
    """Construct active read adapters per request. Override in tests via monkeypatch."""
    cfg = Config.from_env()
    inc = set(include) if include else None
    exc = set(exclude or [])

    selected: list[ReadSourceConfig] = []
    for name, rsc in READ_SOURCES.items():
        if inc is not None:
            if name not in inc:
                continue
        else:
            if not rsc.default_included:
                continue
        if name in exc:
            continue
        selected.append(rsc)

    return [_construct(rsc, cfg) for rsc in selected]


async def query(q: str,
                include: list[str] | None = None,
                exclude: list[str] | None = None,
                top_k: int = 10) -> QueryResult:
    """Fan out across configured ReadAdapters, gather, dedup + RRF, return."""
    qid = "q-" + _uuid.uuid4().hex[:12]
    start = time.monotonic()
    adapters = _load_adapters(include, exclude)
    if not adapters:
        return QueryResult(query_id=qid, hits=[], source_errors=[], elapsed_ms=0)

    async def _one(adapter):
        return await asyncio.wait_for(adapter.query(q, top_k=top_k),
                                       timeout=adapter.read_timeout)

    results = await asyncio.gather(
        *[_one(a) for a in adapters],
        return_exceptions=True,
    )

    per_source: dict[str, list[Hit]] = {}
    errors: list[SourceError] = []
    for adapter, result in zip(adapters, results):
        if isinstance(result, asyncio.TimeoutError):
            errors.append(SourceError(source=adapter.name, error_class="timeout",
                                       message=f"{adapter.read_timeout}s"))
            continue
        if isinstance(result, Exception):
            errors.append(SourceError(source=adapter.name, error_class="exception",
                                       message=f"{type(result).__name__}: {result}"))
            continue
        per_source[adapter.name] = result

    merged = rrf_merge(per_source, top_k=top_k)
    elapsed = int((time.monotonic() - start) * 1000)
    return QueryResult(query_id=qid, hits=merged, source_errors=errors, elapsed_ms=elapsed)
```

- [ ] **Step 4: Add query route to `server.py`**

Add this import at the top of server.py:
```python
from . import query as query_module
from pydantic import Field
```

Add this body model with the other `_*` models:
```python
class _QueryBody(BaseModel):
    q: str = Field(..., min_length=1, max_length=2000)
    include: list[str] | None = None
    exclude: list[str] | None = None
    top_k: int = Field(10, ge=1, le=100)
```

Inside `build_app`, after the `decay_route`:
```python
    @app.post("/api/memory/query")
    async def query_route(body: _QueryBody) -> dict[str, Any]:
        result = await query_module.query(
            body.q, include=body.include, exclude=body.exclude, top_k=body.top_k
        )
        return result.model_dump()
```

- [ ] **Step 5: Run tests, expect PASS, then commit**

```bash
cd deploy/memory-router && python -m pytest tests/integration/test_query_roundtrip.py -v
git add deploy/memory-router/flyn_memory_router/query.py \
        deploy/memory-router/flyn_memory_router/server.py \
        deploy/memory-router/tests/integration/test_query_roundtrip.py
git commit -m "feat(memory-router): POST /api/memory/query route + async orchestrator"
```

---

### Task 36: /api/memory/lint route + drift detection

**Files:**
- Create: `deploy/memory-router/flyn_memory_router/lint.py`
- Modify: `deploy/memory-router/flyn_memory_router/server.py`
- Create: `deploy/memory-router/tests/unit/test_lint.py`

- [ ] **Step 1: Write failing tests**

```python
# deploy/memory-router/tests/unit/test_lint.py
from __future__ import annotations

import pytest

from flyn_memory_router.types import Hit


@pytest.mark.asyncio
async def test_no_drift_when_sources_agree():
    from flyn_memory_router.lint import detect_drift
    per_source = {
        "hot/MEMORY.md": [Hit(text="Beth Kukla, COO Cora", source="hot/MEMORY.md", score=0.9, metadata={})],
        "warm/graphiti": [Hit(text="Beth Kukla, COO Cora", source="warm/graphiti", score=0.9, metadata={})],
    }
    assert await detect_drift("Beth", per_source) == []


@pytest.mark.asyncio
async def test_drift_when_sources_diverge_substantially():
    from flyn_memory_router.lint import detect_drift
    per_source = {
        "hot/MEMORY.md":   [Hit(text="Beth = COO Cora, PM for OL", source="hot/MEMORY.md", score=0.9, metadata={})],
        "warm/graphiti":   [Hit(text="Beth is a contractor", source="warm/graphiti", score=0.9, metadata={})],
    }
    findings = await detect_drift("Beth", per_source)
    assert len(findings) == 1
    assert findings[0].entity == "Beth"


@pytest.mark.asyncio
async def test_no_finding_when_only_one_source_has_data():
    from flyn_memory_router.lint import detect_drift
    per_source = {
        "hot/MEMORY.md": [Hit(text="Beth = COO Cora", source="hot/MEMORY.md", score=0.9, metadata={})],
        "warm/graphiti": [],
    }
    assert await detect_drift("Beth", per_source) == []
```

- [ ] **Step 2: Run tests, expect FAIL, then implement `lint.py`**

```python
"""Drift detection across read sources.

Strategy: for an entity, run the standard query; pairwise-compare top hits
per source by token Jaccard; if any pair < threshold, emit one finding.
Reported, never auto-resolved.
"""
from __future__ import annotations

import re

from .types import Hit, LintFinding


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"\w+", s.lower()))


def _jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


async def detect_drift(entity: str,
                        per_source: dict[str, list[Hit]],
                        threshold: float = 0.6) -> list[LintFinding]:
    top_per_source: dict[str, str] = {}
    for src, hits in per_source.items():
        if hits:
            top_per_source[src] = hits[0].text
    if len(top_per_source) < 2:
        return []
    diverged = False
    keys = list(top_per_source.keys())
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            if _jaccard(top_per_source[keys[i]], top_per_source[keys[j]]) < threshold:
                diverged = True
                break
        if diverged:
            break
    if not diverged:
        return []
    return [LintFinding(
        entity=entity,
        sources=top_per_source,
        divergence=f"Pairwise Jaccard < {threshold} between {len(keys)} sources",
        suggested_fix="Review and reconcile; canonical source is typically Graphiti for facts.",
    )]
```

- [ ] **Step 3: Add lint route to `server.py`**

Add import: `from . import lint as lint_module`
Add `from .types import Hit` if not already imported.

Add body model:
```python
class _LintBody(BaseModel):
    entities: list[str] = Field(..., min_length=1, max_length=100)
    sources: list[str] | None = None
```

Inside `build_app`, after the query_route:
```python
    @app.post("/api/memory/lint")
    async def lint_route(body: _LintBody) -> dict[str, Any]:
        findings = []
        for entity in body.entities:
            result = await query_module.query(entity, include=body.sources, top_k=3)
            per_source: dict[str, list[Hit]] = {}
            for h in result.hits:
                per_source.setdefault(h.source, []).append(h)
            ent_findings = await lint_module.detect_drift(entity, per_source)
            findings.extend(ent_findings)
        return {"findings": [f.model_dump() for f in findings]}
```

- [ ] **Step 4: Run tests, expect PASS, then commit**

```bash
cd deploy/memory-router && python -m pytest tests/unit/test_lint.py -v
git add deploy/memory-router/flyn_memory_router/lint.py \
        deploy/memory-router/flyn_memory_router/server.py \
        deploy/memory-router/tests/unit/test_lint.py
git commit -m "feat(memory-router): POST /api/memory/lint drift detection"
```

---

### Task 37: /api/memory/sources route + health tracker

**Files:**
- Create: `deploy/memory-router/flyn_memory_router/health_tracker.py`
- Modify: `deploy/memory-router/flyn_memory_router/query.py`
- Modify: `deploy/memory-router/flyn_memory_router/server.py`
- Create: `deploy/memory-router/tests/unit/test_health_tracker.py`

- [ ] **Step 1: Write failing tests**

```python
# deploy/memory-router/tests/unit/test_health_tracker.py
from __future__ import annotations

import pytest


def test_health_tracker_records_success():
    from flyn_memory_router.health_tracker import HealthTracker
    ht = HealthTracker(window=100)
    ht.record(source="hot", elapsed_ms=42, error=False)
    snap = ht.snapshot("hot")
    assert snap["last_elapsed_ms"] == 42
    assert snap["last_error_ts"] is None
    assert snap["error_rate_100q"] == 0.0


def test_health_tracker_records_error():
    from flyn_memory_router.health_tracker import HealthTracker
    ht = HealthTracker(window=100)
    ht.record(source="warm", elapsed_ms=0, error=True)
    snap = ht.snapshot("warm")
    assert snap["last_error_ts"] is not None
    assert snap["error_rate_100q"] == 1.0


def test_health_tracker_rolls_window():
    from flyn_memory_router.health_tracker import HealthTracker
    ht = HealthTracker(window=3)
    ht.record("hot", elapsed_ms=10, error=True)
    ht.record("hot", elapsed_ms=10, error=False)
    ht.record("hot", elapsed_ms=10, error=False)
    assert ht.snapshot("hot")["error_rate_100q"] == pytest.approx(1/3)
    ht.record("hot", elapsed_ms=10, error=False)
    assert ht.snapshot("hot")["error_rate_100q"] == 0.0


def test_unknown_source_snapshot_is_empty():
    from flyn_memory_router.health_tracker import HealthTracker
    ht = HealthTracker(window=100)
    snap = ht.snapshot("never-seen")
    assert snap["last_elapsed_ms"] is None
    assert snap["error_rate_100q"] is None
```

- [ ] **Step 2: Implement `health_tracker.py`**

```python
"""Rolling per-source success/error stats (in-memory, process-local)."""
from __future__ import annotations

import time
from collections import deque
from threading import Lock
from typing import Any


class HealthTracker:
    def __init__(self, window: int = 100) -> None:
        self._window = window
        self._stats: dict[str, dict[str, Any]] = {}
        self._lock = Lock()

    def record(self, source: str, elapsed_ms: int, error: bool) -> None:
        with self._lock:
            row = self._stats.setdefault(source, {
                "last_elapsed_ms": None,
                "last_error_ts": None,
                "samples": deque(maxlen=self._window),
            })
            row["last_elapsed_ms"] = elapsed_ms
            if error:
                row["last_error_ts"] = time.time()
            row["samples"].append(1 if error else 0)

    def snapshot(self, source: str) -> dict[str, Any]:
        with self._lock:
            row = self._stats.get(source)
            if row is None:
                return {"last_elapsed_ms": None, "last_error_ts": None, "error_rate_100q": None}
            samples = row["samples"]
            rate = (sum(samples) / len(samples)) if samples else 0.0
            return {
                "last_elapsed_ms": row["last_elapsed_ms"],
                "last_error_ts": row["last_error_ts"],
                "error_rate_100q": rate,
            }

    def all_snapshots(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {src: self.snapshot(src) for src in self._stats}


TRACKER = HealthTracker(window=100)
```

- [ ] **Step 3: Wire `query.py` to record stats**

Add import to query.py: `from .health_tracker import TRACKER`

In `async def query(...)`, replace the per-adapter result loop with:
```python
    for adapter, result in zip(adapters, results):
        if isinstance(result, asyncio.TimeoutError):
            TRACKER.record(adapter.name, elapsed_ms=int(adapter.read_timeout * 1000), error=True)
            errors.append(SourceError(source=adapter.name, error_class="timeout",
                                       message=f"{adapter.read_timeout}s"))
            continue
        if isinstance(result, Exception):
            TRACKER.record(adapter.name, elapsed_ms=0, error=True)
            errors.append(SourceError(source=adapter.name, error_class="exception",
                                       message=f"{type(result).__name__}: {result}"))
            continue
        TRACKER.record(adapter.name, elapsed_ms=int((time.monotonic() - start) * 1000), error=False)
        per_source[adapter.name] = result
```

- [ ] **Step 4: Add sources route to `server.py`**

Add imports:
```python
from .health_tracker import TRACKER
from .config import READ_SOURCES
```

Inside `build_app`, after `lint_route`:
```python
    @app.get("/api/memory/sources")
    def sources_route() -> list[dict[str, Any]]:
        out = []
        for name, rsc in READ_SOURCES.items():
            snap = TRACKER.snapshot(name)
            out.append({
                "name": name,
                "kind": "read",
                "default_included": rsc.default_included,
                "timeout_s": rsc.timeout,
                **snap,
            })
        return out
```

- [ ] **Step 5: Run tests, expect PASS, then commit**

```bash
cd deploy/memory-router && python -m pytest tests/unit/test_health_tracker.py tests/integration/test_query_roundtrip.py -v
git add deploy/memory-router/flyn_memory_router/health_tracker.py \
        deploy/memory-router/flyn_memory_router/query.py \
        deploy/memory-router/flyn_memory_router/server.py \
        deploy/memory-router/tests/unit/test_health_tracker.py
git commit -m "feat(memory-router): GET /api/memory/sources + per-source health tracker"
```

---

## Phase 0f — CLI, logging, install, smoke, rubric (Tasks 38–42)

### Task 38: flyn-mem CLI

**Files:**
- Create: `deploy/memory-router/flyn_memory_router/cli.py`
- Modify: `deploy/memory-router/pyproject.toml`
- Create: `deploy/memory-router/tests/unit/test_cli.py`

- [ ] **Step 1: Write failing tests**

```python
# deploy/memory-router/tests/unit/test_cli.py
from __future__ import annotations

import json

import httpx
import pytest


def _client_factory(handler):
    transport = httpx.MockTransport(handler)
    return lambda: httpx.Client(transport=transport, base_url="http://localhost:8400")


def test_query_subcommand_prints_hits(capsys):
    from flyn_memory_router.cli import main

    def handler(request):
        body = json.loads(request.content)
        assert body["q"] == "Beth"
        return httpx.Response(200, json={
            "query_id": "q-abc",
            "hits": [{"text": "Beth = COO", "source": "hot/MEMORY.md", "score": 0.95, "metadata": {}}],
            "source_errors": [],
            "elapsed_ms": 42,
        })

    rc = main(["query", "Beth"], client_factory=_client_factory(handler))
    captured = capsys.readouterr()
    assert rc == 0
    assert "Beth = COO" in captured.out


def test_query_json_flag(capsys):
    from flyn_memory_router.cli import main

    def handler(request):
        return httpx.Response(200, json={"query_id": "q-x", "hits": [], "source_errors": [], "elapsed_ms": 0})

    rc = main(["query", "anything", "--json"], client_factory=_client_factory(handler))
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["query_id"] == "q-x"


def test_health_subcommand(capsys):
    from flyn_memory_router.cli import main

    def handler(request):
        if request.url.path == "/api/health":
            return httpx.Response(200, json={"ok": True, "service": "flyn-memory-router", "port": 8400})
        if request.url.path == "/api/memory/sources":
            return httpx.Response(200, json=[{"name": "hot", "default_included": True,
                                                "last_elapsed_ms": 5, "error_rate_100q": 0.0}])
        return httpx.Response(404)

    rc = main(["health"], client_factory=_client_factory(handler))
    assert rc == 0


def test_query_unreachable_prints_actionable_error(capsys):
    from flyn_memory_router.cli import main

    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    rc = main(["query", "Beth"], client_factory=_client_factory(handler))
    err = capsys.readouterr().err
    assert rc != 0
    assert "launchctl" in err
```

- [ ] **Step 2: Implement `cli.py`**

```python
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
```

- [ ] **Step 3: Add console_scripts entry**

In `pyproject.toml`'s `[project]` section, add:
```toml
[project.scripts]
flyn-mem = "flyn_memory_router.cli:main"
```

Then reinstall:
```bash
cd deploy/memory-router && pip install -e . --quiet
```

- [ ] **Step 4: Run tests + verify entry point**

```bash
cd deploy/memory-router && python -m pytest tests/unit/test_cli.py -v
which flyn-mem && flyn-mem --help
```

- [ ] **Step 5: Commit**

```bash
git add deploy/memory-router/flyn_memory_router/cli.py \
        deploy/memory-router/pyproject.toml \
        deploy/memory-router/tests/unit/test_cli.py
git commit -m "feat(memory-router): flyn-mem CLI (query/health/sources subcommands)"
```

---

### Task 39: Logging contract enforcement

**Files:**
- Create: `deploy/memory-router/flyn_memory_router/logging_contract.py`
- Modify: `deploy/memory-router/flyn_memory_router/query.py`
- Modify: `deploy/memory-router/flyn_memory_router/cli.py` (add `logs` subcommand)
- Create: `deploy/memory-router/tests/unit/test_logging_contract.py`

- [ ] **Step 1: Write failing tests**

```python
# deploy/memory-router/tests/unit/test_logging_contract.py
from __future__ import annotations

import json
from pathlib import Path


def test_query_log_writer_appends_jsonl(tmp_path: Path):
    from flyn_memory_router.logging_contract import QueryLogWriter
    log_dir = tmp_path / "logs"
    w = QueryLogWriter(log_dir=log_dir)
    w.write({
        "query_id": "q-1", "q": "Beth", "caller": "cli",
        "included_sources": ["hot", "warm"],
        "per_source": {"hot": {"hits": 1, "elapsed_ms": 5},
                       "warm": {"hits": 2, "elapsed_ms": 40}},
        "total_elapsed_ms": 45, "top_k": 10,
    })
    files = list(log_dir.glob("query-*.jsonl"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text().strip().splitlines()[-1])
    assert rec["query_id"] == "q-1"
    assert "ts" in rec


def test_source_error_log_correlated_by_query_id(tmp_path: Path):
    from flyn_memory_router.logging_contract import SourceErrorLogWriter
    log_dir = tmp_path / "logs"
    w = SourceErrorLogWriter(log_dir=log_dir)
    w.write(query_id="q-1", source="ocw_mem", exc=RuntimeError("boom"))
    files = list(log_dir.glob("source-errors-*.jsonl"))
    assert files
    rec = json.loads(files[0].read_text().strip().splitlines()[-1])
    assert rec["query_id"] == "q-1"
    assert rec["source"] == "ocw_mem"
    assert "RuntimeError" in rec["error_class"]


def test_rotation_creates_daily_files(tmp_path: Path, monkeypatch):
    from flyn_memory_router import logging_contract as lc
    log_dir = tmp_path / "logs"
    w = lc.QueryLogWriter(log_dir=log_dir)
    monkeypatch.setattr(lc, "_today_iso", lambda: "2026-05-10")
    w.write({"query_id": "q-1", "q": "x", "caller": "test",
             "included_sources": [], "per_source": {}, "total_elapsed_ms": 0, "top_k": 0})
    monkeypatch.setattr(lc, "_today_iso", lambda: "2026-05-11")
    w.write({"query_id": "q-2", "q": "y", "caller": "test",
             "included_sources": [], "per_source": {}, "total_elapsed_ms": 0, "top_k": 0})
    files = sorted(p.name for p in log_dir.glob("query-*.jsonl"))
    assert files == ["query-2026-05-10.jsonl", "query-2026-05-11.jsonl"]
```

- [ ] **Step 2: Implement `logging_contract.py`**

```python
"""Structured JSONL logging with daily rotation + 90-day/1GB retention."""
from __future__ import annotations

import json
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class _JsonlAppender:
    def __init__(self, log_dir: Path, prefix: str) -> None:
        self._dir = log_dir
        self._prefix = prefix
        self._lock = Lock()

    def _path(self) -> Path:
        self._dir.mkdir(parents=True, exist_ok=True)
        return self._dir / f"{self._prefix}-{_today_iso()}.jsonl"

    def _append(self, record: dict) -> None:
        record["ts"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        line = json.dumps(record, default=str)
        with self._lock:
            with self._path().open("a", encoding="utf-8") as f:
                f.write(line + "\n")


class QueryLogWriter(_JsonlAppender):
    def __init__(self, log_dir: Path) -> None:
        super().__init__(log_dir, "query")

    def write(self, record: dict) -> None:
        self._append(record)


class SourceErrorLogWriter(_JsonlAppender):
    def __init__(self, log_dir: Path) -> None:
        super().__init__(log_dir, "source-errors")

    def write(self, query_id: str, source: str, exc: BaseException) -> None:
        self._append({
            "query_id": query_id,
            "source": source,
            "error_class": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exception(exc),
        })


def gc_logs(log_dir: Path,
             retention_days: int = 90,
             max_bytes: int = 1 * 1024 * 1024 * 1024) -> None:
    import gzip
    import shutil
    if not log_dir.exists():
        return
    cutoff = time.time() - retention_days * 86400
    for jsonl in sorted(log_dir.glob("*.jsonl")):
        try:
            mtime = jsonl.stat().st_mtime
        except FileNotFoundError:
            continue
        if mtime < cutoff:
            gz = jsonl.with_suffix(".jsonl.gz")
            with jsonl.open("rb") as fi, gzip.open(gz, "wb") as fo:
                shutil.copyfileobj(fi, fo)
            jsonl.unlink()
    files = sorted(log_dir.iterdir(), key=lambda p: p.stat().st_mtime)
    total = sum(f.stat().st_size for f in files if f.is_file())
    i = 0
    while total > max_bytes and i < len(files):
        f = files[i]
        total -= f.stat().st_size
        f.unlink()
        i += 1
```

- [ ] **Step 3: Wire query.py to write logs**

Add imports near the top of query.py:
```python
from .logging_contract import QueryLogWriter, SourceErrorLogWriter

_QLOG: QueryLogWriter | None = None
_ELOG: SourceErrorLogWriter | None = None


def _qlog() -> QueryLogWriter:
    global _QLOG
    if _QLOG is None:
        _QLOG = QueryLogWriter(log_dir=Config.from_env().log_dir)
    return _QLOG


def _elog() -> SourceErrorLogWriter:
    global _ELOG
    if _ELOG is None:
        _ELOG = SourceErrorLogWriter(log_dir=Config.from_env().log_dir)
    return _ELOG
```

At the end of `async def query(...)`, just before `return`:
```python
    _qlog().write({
        "query_id": qid,
        "q": q,
        "caller": "rest",
        "included_sources": [a.name for a in adapters],
        "per_source": {a.name: {"hits": len(per_source.get(a.name, [])),
                                  "error": next((e.error_class for e in errors if e.source == a.name), None)}
                        for a in adapters},
        "top_k": top_k,
        "total_elapsed_ms": elapsed,
    })
    for err in errors:
        _elog().write(query_id=qid, source=err.source,
                       exc=RuntimeError(f"{err.error_class}: {err.message}"))
```

- [ ] **Step 4: Add `logs` subcommand to cli.py**

Add to build_parser():
```python
    lg = sub.add_parser("logs", help="tail query log")
    lg.add_argument("--query-id", dest="query_id", default=None)
    lg.add_argument("--grep", default=None)
    lg.add_argument("--errors", action="store_true")
    lg.add_argument("--tail", type=int, default=20)
```

Add command handler:
```python
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
```

Add to dispatch:
```python
        "logs": _cmd_logs,
```

- [ ] **Step 5: Run tests, expect PASS, then commit**

```bash
cd deploy/memory-router && python -m pytest tests/unit/test_logging_contract.py -v
git add deploy/memory-router/flyn_memory_router/logging_contract.py \
        deploy/memory-router/flyn_memory_router/query.py \
        deploy/memory-router/flyn_memory_router/cli.py \
        deploy/memory-router/tests/unit/test_logging_contract.py
git commit -m "feat(memory-router): logging contract — JSONL writers + flyn-mem logs"
```

---

### Task 40: Install-script extensions (symlink + discovery artifacts)

**Files:**
- Create: `deploy/memory-router/flyn_memory_router/discovery.py`
- Modify: `deploy/memory-router/install.sh`
- Create: `deploy/memory-router/tests/unit/test_install_artifacts.py`

- [ ] **Step 1: Write failing tests**

```python
# deploy/memory-router/tests/unit/test_install_artifacts.py
from __future__ import annotations

from pathlib import Path


def test_writes_auto_memory_pointer_when_missing(tmp_path: Path):
    from flyn_memory_router.discovery import write_auto_memory_pointer
    memdir = tmp_path / "memory"
    write_auto_memory_pointer(memdir)
    f = memdir / "feedback_memory_router.md"
    assert f.exists()
    content = f.read_text()
    assert "memory-router-front-door" in content
    assert "flyn-mem query" in content


def test_appends_to_memory_md_index_only_once(tmp_path: Path):
    from flyn_memory_router.discovery import append_memory_md_index
    memdir = tmp_path / "memory"
    memdir.mkdir()
    idx = memdir / "MEMORY.md"
    idx.write_text("- [other](other.md) — example\n")
    append_memory_md_index(memdir)
    append_memory_md_index(memdir)
    text = idx.read_text()
    assert text.count("feedback_memory_router.md") == 1


def test_writes_tools_md_pointer_only_once(tmp_path: Path):
    from flyn_memory_router.discovery import append_tools_md
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tools = workspace / "TOOLS.md"
    tools.write_text("# TOOLS\n\nExisting content.\n")
    append_tools_md(workspace)
    append_tools_md(workspace)
    text = tools.read_text()
    assert text.count("## flyn-mem") == 1
```

- [ ] **Step 2: Implement `discovery.py`**

```python
"""Discovery-artifact writers used by install.sh. All idempotent."""
from __future__ import annotations

from pathlib import Path

AUTO_MEMORY_FILE = "feedback_memory_router.md"

AUTO_MEMORY_BODY = """---
name: memory-router-front-door
description: Cross-system memory queries on this Mac route through `flyn-mem` CLI (or POST :8400/api/memory/query). Spans Flyn workspace, Graphiti, OpenClaw memory, Karpathy vault, auto-memory, ol-wiki.
metadata:
  type: reference
---
For any "what does Ryan know about X" question, prefer `flyn-mem query "X"` before
filesystem grep or per-source reads. Returns ranked hits + citations across 10 sources.

Quick examples:
  flyn-mem query "who is Beth?"                  # all sources, top 10
  flyn-mem query "Flyn memory schema" --include reference lesson
  flyn-mem query "..." --exclude lossless ocw_mem
  flyn-mem sources                                # per-adapter health
  flyn-mem logs --query-id <id>                   # debug a result

Service runs at localhost:8400 (launchd: ai.flyn.memory-router).
If `flyn-mem` is missing: see ~/AI/openclaw/flyn-agent/deploy/memory-router/README.md
"""

MEMORY_MD_INDEX_LINE = "- [memory-router-front-door](feedback_memory_router.md) — flyn-mem CLI for cross-system queries\n"

TOOLS_MD_SECTION = """
## flyn-mem (memory router)

REST: `http://127.0.0.1:8400/api/memory/{query,ingest,lint,sources}`
CLI: `flyn-mem query "<q>"` / `flyn-mem health` / `flyn-mem logs --query-id <id>`

Use `flyn-mem query` before grepping workspace files; it fans out across
hot/warm/cool/cold/lesson/reference/user/ol_wiki sources with RRF rank fusion.
"""


def write_auto_memory_pointer(memory_dir: Path) -> None:
    memory_dir.mkdir(parents=True, exist_ok=True)
    target = memory_dir / AUTO_MEMORY_FILE
    if not target.exists():
        target.write_text(AUTO_MEMORY_BODY)


def append_memory_md_index(memory_dir: Path) -> None:
    idx = memory_dir / "MEMORY.md"
    if not idx.exists():
        idx.write_text(MEMORY_MD_INDEX_LINE)
        return
    text = idx.read_text()
    if AUTO_MEMORY_FILE in text:
        return
    with idx.open("a") as f:
        f.write(MEMORY_MD_INDEX_LINE)


def append_tools_md(workspace_dir: Path) -> None:
    workspace_dir.mkdir(parents=True, exist_ok=True)
    tools = workspace_dir / "TOOLS.md"
    if tools.exists():
        text = tools.read_text()
        if "## flyn-mem" in text:
            return
        with tools.open("a") as f:
            f.write(TOOLS_MD_SECTION)
    else:
        tools.write_text("# TOOLS\n" + TOOLS_MD_SECTION)
```

- [ ] **Step 3: Extend `install.sh`** — append before the final success message:

```bash
# --- Read-side install steps (Task 40) ---

if [[ -d /usr/local/bin && -w /usr/local/bin ]]; then
  ln -sf "$VENV/bin/flyn-mem" /usr/local/bin/flyn-mem
  echo "  ✓ symlinked /usr/local/bin/flyn-mem -> $VENV/bin/flyn-mem"
elif sudo -n true 2>/dev/null; then
  sudo ln -sf "$VENV/bin/flyn-mem" /usr/local/bin/flyn-mem
  echo "  ✓ symlinked /usr/local/bin/flyn-mem (via sudo)"
else
  echo "  ! cannot symlink /usr/local/bin/flyn-mem (no passwordless sudo)"
  echo "    Run manually:  sudo ln -sf $VENV/bin/flyn-mem /usr/local/bin/flyn-mem"
fi

"$VENV/bin/python" - <<'PYEOF'
from pathlib import Path
import os
from flyn_memory_router.discovery import (
    write_auto_memory_pointer, append_memory_md_index, append_tools_md
)

automem = Path(os.environ.get("FLYN_AUTO_MEMORY_DIR",
                              str(Path.home() / ".claude" / "projects" /
                                  "-Users-4c-AI" / "memory")))
workspace = Path(os.environ.get("FLYN_WORKSPACE",
                                str(Path.home() / ".openclaw" / "workspace")))

write_auto_memory_pointer(automem)
append_memory_md_index(automem)
append_tools_md(workspace)
print(f"  ✓ auto-memory pointer at {automem}/feedback_memory_router.md")
print(f"  ✓ TOOLS.md updated at   {workspace}/TOOLS.md")
PYEOF
```

- [ ] **Step 4: Run tests + manual sandbox install**

```bash
cd deploy/memory-router && python -m pytest tests/unit/test_install_artifacts.py -v
FLYN_AUTO_MEMORY_DIR=/tmp/test-automem \
FLYN_WORKSPACE=/tmp/test-ws \
bash install.sh
ls /tmp/test-automem /tmp/test-ws
```

- [ ] **Step 5: Commit**

```bash
git add deploy/memory-router/flyn_memory_router/discovery.py \
        deploy/memory-router/install.sh \
        deploy/memory-router/tests/unit/test_install_artifacts.py
git commit -m "feat(memory-router): install — flyn-mem symlink + discovery artifacts"
```

---

### Task 41: Live smoke test

**Files:**
- Create: `deploy/memory-router/tests/smoke/test_live_query.py`
- Create: `deploy/memory-router/tests/smoke/README.md`
- Modify: `deploy/memory-router/pyproject.toml`

- [ ] **Step 1: Write the smoke tests**

```python
# deploy/memory-router/tests/smoke/test_live_query.py
"""LIVE smoke test — hits the actually-running flyn-memory-router service.

Run manually after install.sh:
    cd deploy/memory-router && python -m pytest tests/smoke/ -v -s

Excluded from the default pytest run via pyproject testpaths.
"""
from __future__ import annotations

import httpx
import pytest

BASE = "http://localhost:8400"


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=10.0) as c:
        r = c.get("/api/health")
        if r.status_code != 200:
            pytest.skip("flyn-memory-router not running on :8400")
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_sources_lists_all_adapters(client):
    r = client.get("/api/memory/sources")
    assert r.status_code == 200
    names = {s["name"] for s in r.json()}
    expected = {"hot", "warm", "cool", "cold", "lesson", "reference", "user",
                "ol_wiki", "ocw_mem", "lossless"}
    assert expected.issubset(names)


def test_query_smoke(client):
    r = client.post("/api/memory/query", json={"q": "Flyn", "top_k": 5})
    assert r.status_code == 200
    body = r.json()
    assert "query_id" in body
    assert "elapsed_ms" in body
    print(f"\nquery_id={body['query_id']} elapsed_ms={body['elapsed_ms']}")
    print(f"hits returned: {len(body['hits'])}")
    print(f"source_errors: {[e['source'] for e in body['source_errors']]}")


def test_query_respects_include_filter(client):
    r = client.post("/api/memory/query", json={"q": "test", "include": ["hot"], "top_k": 5})
    assert r.status_code == 200
    sources_seen = {h["source"].split("/")[0] for h in r.json()["hits"]}
    if sources_seen:
        assert sources_seen == {"hot"}


def test_logs_write_correlates(client):
    import os, datetime, pathlib
    log_dir = pathlib.Path(os.environ.get("FLYN_MEMORY_ROUTER_HOME",
                                           str(pathlib.Path.home() / ".flyn" / "memory-router"))) / "logs"
    today = datetime.date.today().isoformat()
    today_log = log_dir / f"query-{today}.jsonl"
    r = client.post("/api/memory/query", json={"q": "smoke-test-marker", "top_k": 1})
    qid = r.json()["query_id"]
    assert today_log.exists()
    found = any(qid in line for line in today_log.read_text().splitlines())
    assert found, f"query_id {qid} not found in {today_log}"
```

- [ ] **Step 2: Write `tests/smoke/README.md`**

```markdown
# Smoke tests

Manual ship-gate — hits the **actually running** flyn-memory-router on
:8400. Excluded from default pytest run; intended as the post-install
verification step.

## Run

```
cd deploy/memory-router
python -m pytest tests/smoke/ -v -s
```

Expected: 5 tests pass. If the service isn't running, all are skipped
(not failed) via the module-scoped fixture.
```

- [ ] **Step 3: Exclude smoke/ from default pytest** — edit pyproject.toml:

```toml
[tool.pytest.ini_options]
testpaths = ["tests/unit", "tests/integration"]
asyncio_mode = "auto"
```

- [ ] **Step 4: Verify default run skips smoke, then commit**

```bash
cd deploy/memory-router && python -m pytest -v 2>&1 | tail -5
git add deploy/memory-router/tests/smoke/ \
        deploy/memory-router/pyproject.toml
git commit -m "test(memory-router): live smoke tests (manual ship-gate)"
```

---

### Task 42: MEMORY-ROUTER-READ-RUBRIC.md for outcomes_runner

**Files:**
- Create: `deploy/outcomes/MEMORY-ROUTER-READ-RUBRIC.md`

- [ ] **Step 1: Write the rubric**

```markdown
# Memory-Router Read-Side Rubric (Phase 0c-0f)

Machine-gradable success criteria for the read-side extension. Run via
`outcomes_runner.py --rubric deploy/outcomes/MEMORY-ROUTER-READ-RUBRIC.md`.

## Types & adapter contracts

- [ ] `flyn_memory_router.types.Hit` exists with fields text, source, score, metadata
- [ ] `flyn_memory_router.types.QueryResult` exists with query_id, hits, source_errors, elapsed_ms
- [ ] `flyn_memory_router.types.LintFinding` and `LintReport` exist
- [ ] `flyn_memory_router.adapters.base.ReadAdapter` Protocol with async query
- [ ] `flyn_memory_router.config.READ_SOURCES` registers all 10 expected adapters
- [ ] ocw_mem and lossless are default_included=False

## Adapters built

- [ ] adapters/hot_read.py:HotRead exists and tests pass
- [ ] adapters/warm_read.py:WarmRead exists and tests pass
- [ ] adapters/cool_read.py:CoolRead exists and tests pass
- [ ] adapters/cold_read.py:ColdRead exists and tests pass
- [ ] adapters/lesson_read.py:LessonRead exists and tests pass
- [ ] adapters/reference_read.py:ReferenceRead exists and tests pass
- [ ] adapters/user_read.py:UserRead exists and tests pass
- [ ] adapters/ol_wiki_read.py:OLWikiRead exists and tests pass
- [ ] adapters/ocw_mem_read.py:OCWMemRead exists and tests pass
- [ ] adapters/lossless_read.py:LosslessRead exists and tests pass

## Orchestrator & routes

- [ ] query.rrf_merge(per_source, top_k) — RRF_K==60, canonical_id dedup, text-hash dedup
- [ ] async query.query(q, include, exclude, top_k) exists
- [ ] POST /api/memory/query route — integration tests pass
- [ ] POST /api/memory/lint route — drift tests pass
- [ ] GET /api/memory/sources route — returns name, default_included, last_elapsed_ms, error_rate_100q
- [ ] health_tracker.HealthTracker records timeouts/exceptions/success per source

## CLI

- [ ] flyn-mem console-script registered in pyproject.toml
- [ ] flyn-mem query "<q>" prints hits when service reachable
- [ ] flyn-mem query non-zero with launchctl hint when unreachable
- [ ] flyn-mem health prints overall + per-source state
- [ ] flyn-mem sources prints JSON
- [ ] flyn-mem logs --query-id <id> joins query + source-errors logs

## Logging

- [ ] Each query writes ~/.flyn/memory-router/logs/query-YYYY-MM-DD.jsonl
- [ ] Each failure writes source-errors-YYYY-MM-DD.jsonl with matching query_id
- [ ] logging_contract.gc_logs() handles 90-day gzip + 1GB cap

## Install + discovery

- [ ] install.sh symlinks /usr/local/bin/flyn-mem (or prints sudo command)
- [ ] After install, ~/.claude/projects/-Users-4c-AI/memory/feedback_memory_router.md exists
- [ ] MEMORY.md has exactly one index line for feedback_memory_router.md (idempotent)
- [ ] workspace TOOLS.md has exactly one ## flyn-mem section (idempotent)

## Live smoke (manual; only graded with --smoke)

- [ ] /api/health returns {ok: true}
- [ ] /api/memory/query with q="Flyn" returns 200 with query_id
- [ ] today's query-*.jsonl contains that query_id
- [ ] flyn-mem health prints all 10 sources

## Soft commitments

- [ ] No new launchd unit added (one service)
- [ ] No new daemon, no new port
- [ ] All new file sizes meet caps: ≤200 per adapter, ≤250 query.py, ≤300 server.py
- [ ] All commits follow feat(memory-router): prefix
```

- [ ] **Step 2: Commit**

```bash
git add deploy/outcomes/MEMORY-ROUTER-READ-RUBRIC.md
git commit -m "docs(outcomes): rubric for memory-router read-side phase"
```

---

## Self-review

1. **Spec coverage:** every section of `2026-05-16-flyn-memory-router-unified-design.md` maps to a task. Sections 1, 9, 10 are non-code. Sections 2–8 are covered by Tasks 25–42. ✓
2. **Placeholder scan:** no TBD/TODO/fill-in/similar-to. ✓
3. **Type consistency:** Hit (Task 25) → used by all adapters and orchestrator. ReadAdapter Protocol shape (Task 26) → matches every adapter. READ_SOURCES cls_path strings (Task 26) → resolved by _resolve_class in Task 35. ✓
4. **Dependency check:** Tasks are linear. 35 depends on 28–34. 36 and 37 depend on 35. 38 depends on 35/37. 39 modifies 35. 40 depends on 38. 41 depends on 40. 42 depends on everything. ✓

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-17-flyn-memory-router-read-side.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration with quick failure containment.

2. **Inline Execution** — execute tasks in this session using executing-plans, batch with checkpoints.

Which approach?
