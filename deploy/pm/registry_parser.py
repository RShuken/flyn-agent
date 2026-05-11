#!/usr/bin/env python3
"""Parse Beth's master question registry (markdown) into structured facts.

Modes:
  --json              Print parsed registry as JSON to stdout (for piping).
  --bootstrap         Initial sync: POST every question as a Graphiti episode.
  --diff              Diff repo state vs Graphiti, print plan.
  --sync              Diff + apply (UPSERT new/changed questions to Graphiti).
  --staleness-check   Identify questions in pending-answer state >stale_after_hours old.

Registry format expected (Beth's _Beth.md):
  Section headers like `## A. Initial Phonics Skills Survey`
  Question rows like `| A.1 | <text>. **Q:** <ask>. | <bucket> | <source> |`

Output shape per question:
  {
    "id": "A.1",
    "section": "A",
    "section_title": "Initial Phonics Skills Survey",
    "text": "...",
    "ask": "...",                    # the **Q:** sub-prompt if present
    "bucket": "ai-does",             # ai-does | ai-generates | ai-assists | human-only | bucket-unclear
    "source": "S:8, XL:Assessment Word Lists",
    "owner": "Rebecca",              # inferred — see infer_owner()
    "status": "open",                # parsed from optional STATUS column if present
    "depends_on": [],                # explicit cross-refs found in text (e.g., "see A.5")
  }
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _lib import ProjectConfig, graphiti_episode, graphiti_search, load_project  # noqa: E402


SECTION_HEADER = re.compile(r"^##\s+([A-Z])\.\s+(.+?)\s*$")
QUESTION_ROW = re.compile(
    r"^\|\s*([A-Z]\.\d+)\s*\|\s*(.+?)\s*\|\s*([\w/?\- ]+)\s*\|\s*(.+?)\s*\|\s*$"
)
# Section N uses a different 4-column format:
#   | id | conflict description | sources | resolution question |
N_ROW = re.compile(
    r"^\|\s*(N\.\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*$"
)
ASK_SPLIT = re.compile(r"\*\*Q:\*\*\s*(.+)$", re.DOTALL)
XREF = re.compile(r"\b([A-Z]\.\d+)\b")

# Section O is a materials checklist, not questions. Skip during parsing.
SKIP_SECTIONS = {"O"}

# Per-section default owner. A per-question text heuristic (below) can override
# this — used when the question wording or source codes strongly imply a
# different stakeholder. Defaults map section purpose to the most-frequently-
# responsible OL stakeholder.
SECTION_DEFAULT_OWNER = {
    "A": "Rebecca Patterson",       # Initial Phonics Skills Survey
    "B": "Rebecca Patterson",       # Skill Transition & Pre-Assessment
    "C": "Rebecca Patterson",       # Core Lesson Pathways
    "D": "Rebecca Patterson",       # Remediation Logic
    "E": "Rebecca Patterson",       # Progress Monitoring
    "F": "Rebecca Patterson",       # Foundations Mastery & NN
    "G": "Rebecca Patterson",       # Roots / Level Zero (EL)
    "H": "Rebecca Patterson",       # Group Sessions (operational)
    "I": "Greta Phillips Kendall",  # Learning Plan View (Educator UI)
    "J": "Rebecca Patterson",       # Time-in-Lesson Structure
    "K": "Greta Phillips Kendall",  # Brand & Experience
    "L": "Eric Schneider",          # AI Generation Frontier (technical)
    "M": "Eric Schneider",          # Data / Schema Gaps (technical)
    "N": "Sarah Scott Frank",       # Source-of-Truth Conflicts (CEO adjudicates)
    "P": "Rebecca Patterson",       # Group Assessment Cadence (architectural)
}

# Per-question heuristics. Stronger signals first.
#
# Tier 1: Direct person mention in question text → that person owns it.
#   This is the strongest override of the section default.
# Tier 2: Topic-specific keywords that are unambiguous regardless of section.
#   E.g. "print layout" is Greta's domain even if it appears in a curriculum
#   section. We deliberately keep these narrow — source-code matches like
#   "K:6" or "XL:Decision Rules" are NOT used here, since those describe the
#   artifact a question references, not who decides it.
NAME_MENTION = [
    (re.compile(r"\bGreta\b", re.IGNORECASE), "Greta Phillips Kendall"),
    (re.compile(r"\bSarah\b", re.IGNORECASE), "Sarah Scott Frank"),
    (re.compile(r"\bRebecca\b", re.IGNORECASE), "Rebecca Patterson"),
    (re.compile(r"\bEric\b", re.IGNORECASE), "Eric Schneider"),
    (re.compile(r"\bRyan\b", re.IGNORECASE), "Ryan Shuken"),
]
TOPIC_OVERRIDE = [
    (re.compile(
        r"\bbrand\b|\bmockup\b|\bcolor palette\b|\bCanva\b|"
        r"print layout|page size|\bfont\b|educator UI|educator view",
        re.IGNORECASE,
    ), "Greta Phillips Kendall"),
    (re.compile(
        r"\bcontract\b|launch date|business priority|cost ceiling|"
        r"data residency|CEO sign[- ]?off|final adjudication",
        re.IGNORECASE,
    ), "Sarah Scott Frank"),
]


def parse_registry(md_path: Path) -> list[dict]:
    section: str | None = None
    section_title: str | None = None
    questions: list[dict] = []

    for raw_line in md_path.read_text().splitlines():
        line = raw_line.rstrip()
        if m := SECTION_HEADER.match(line):
            section = m.group(1)
            section_title = m.group(2).strip()
            continue
        if not section or section in SKIP_SECTIONS:
            continue
        # Section N has a different column layout
        if section == "N":
            if m := N_ROW.match(line):
                qid, conflict, sources, resolution_q = m.groups()
                if conflict.lower().startswith("conflict"):  # header row
                    continue
                xrefs = sorted(set(XREF.findall(conflict)) - {qid})
                questions.append(
                    {
                        "id": qid,
                        "section": section,
                        "section_title": section_title,
                        "text": conflict.strip(),
                        "ask": resolution_q.strip(),
                        "bucket": "Conflict",
                        "source": sources.strip(),
                        "owner": infer_owner(section, conflict, sources),
                        "status": "open",
                        "depends_on": xrefs,
                    }
                )
            continue
        # Standard A-M, P format
        if m := QUESTION_ROW.match(line):
            qid, text, bucket, source = m.groups()
            if text.lower().startswith("capability"):
                continue
            ask = None
            if a := ASK_SPLIT.search(text):
                ask = a.group(1).strip()
            xrefs = sorted(set(XREF.findall(text)) - {qid})
            questions.append(
                {
                    "id": qid,
                    "section": section,
                    "section_title": section_title,
                    "text": text.strip(),
                    "ask": ask,
                    "bucket": bucket.strip(),
                    "source": source.strip(),
                    "owner": infer_owner(section, text, source),
                    "status": "open",
                    "depends_on": xrefs,
                }
            )
    return questions


def infer_owner(section: str, text: str, source: str) -> str:
    """Three-tier inference, strongest signal wins.

    1. Direct name mention in question text → that person.
    2. Unambiguous topic keyword (e.g., "print layout" → Greta, "contract" → Sarah).
    3. Section default (e.g., Section N → Sarah for CEO adjudication).

    Source codes (K:N, XL:..., S:N) describe the artifact a question references,
    not who decides it. They are NOT used for inference.
    """
    # Tier 1: direct name in text only (source codes don't count)
    for pat, owner in NAME_MENTION:
        if pat.search(text):
            return owner
    # Tier 2: topic-specific override
    for pat, owner in TOPIC_OVERRIDE:
        if pat.search(text):
            return owner
    # Tier 3: section default
    return SECTION_DEFAULT_OWNER.get(section, "TBD")


# ----------------------------------------------------------------------------
# Modes
# ----------------------------------------------------------------------------


def mode_json(cfg: ProjectConfig) -> None:
    qs = parse_registry(cfg.registry_path)
    print(json.dumps({"project": cfg.slug, "count": len(qs), "questions": qs}, indent=2))


def mode_bootstrap(cfg: ProjectConfig) -> None:
    qs = parse_registry(cfg.registry_path)
    sys.stderr.write(f"Bootstrapping {len(qs)} questions to Graphiti...\n")
    for q in qs:
        body = format_episode(cfg, q)
        try:
            graphiti_episode(body=body, name=f"{cfg.slug}-{q['id']}")
            sys.stderr.write(f"  ✓ {q['id']}\n")
        except Exception as exc:
            sys.stderr.write(f"  ✗ {q['id']}: {exc}\n")


def mode_diff(cfg: ProjectConfig, apply: bool = False) -> None:
    qs = parse_registry(cfg.registry_path)
    existing = {f["name"]: f for f in graphiti_search(f"{cfg.slug} question")}
    to_create: list[dict] = []
    for q in qs:
        if f"{cfg.slug}-{q['id']}" not in existing:
            to_create.append(q)
    sys.stderr.write(f"{len(to_create)} new question(s) to sync\n")
    for q in to_create:
        sys.stderr.write(f"  + {q['id']}: {q['text'][:80]}...\n")
        if apply:
            graphiti_episode(body=format_episode(cfg, q), name=f"{cfg.slug}-{q['id']}")


def mode_staleness(cfg: ProjectConfig) -> None:
    stale_hours = cfg.raw["cadence"]["question_staleness_check"]["stale_after_hours"]
    cutoff = datetime.now(timezone.utc).timestamp() - stale_hours * 3600
    facts = graphiti_search(f"{cfg.slug} pending-answer")
    stale = [f for f in facts if f.get("updated_at_ts", 0) < cutoff]
    print(json.dumps({"project": cfg.slug, "stale_count": len(stale), "questions": stale}, indent=2))


def format_episode(cfg: ProjectConfig, q: dict) -> str:
    """Render a question as a prose episode for Graphiti ingest.

    Graphiti extracts entities + edges from prose. We're explicit about the
    semantics so the extractor builds the right graph.
    """
    deps = f" Depends on {', '.join(q['depends_on'])}." if q["depends_on"] else ""
    ask = f" The specific ask: {q['ask']}" if q["ask"] else ""
    return (
        f"Project {cfg.display_name} (slug {cfg.slug}) has open question {q['id']} "
        f"in section {q['section']} ({q['section_title']}). "
        f"Question: {q['text']}.{ask} "
        f"Capability bucket: {q['bucket']}. Source: {q['source']}. "
        f"Owned by {q['owner']}. Status: {q['status']}.{deps}"
    )


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, help="Project slug")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--json", action="store_true")
    g.add_argument("--bootstrap", action="store_true")
    g.add_argument("--diff", action="store_true")
    g.add_argument("--sync", action="store_true")
    g.add_argument("--staleness-check", action="store_true")
    args = ap.parse_args()

    cfg = load_project(args.project)

    if args.json:
        mode_json(cfg)
    elif args.bootstrap:
        mode_bootstrap(cfg)
    elif args.diff:
        mode_diff(cfg, apply=False)
    elif args.sync:
        mode_diff(cfg, apply=True)
    elif args.staleness_check:
        mode_staleness(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
