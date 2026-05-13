#!/usr/bin/env python3
"""Sync OL wiki questions → Linear issues. Idempotent.

For each question in the wiki:
  - If questions.linear_issue_id is set: PATCH the existing issue (state, title, body, priority, labels)
  - If null: CREATE a new issue, store the id+url back

State mapping:
  open / null      → Backlog
  pending-answer   → Todo
  answered         → Done
  deferred         → Backlog (kept visible, not Canceled)

Priority mapping (target_sprint):
  1 → Urgent (1)
  2 → High (2)
  3 → Medium (3)
  null → No priority (0)

Labels (auto-created if missing):
  section-{A..P}, bucket-{ai-does|ai-generates|...}, owner-{rebecca|sarah|greta|eric},
  sprint-{1|2|3}, openliteracy

Usage:
  ./linear_sync.py                  # sync all questions
  ./linear_sync.py --dry-run        # show what would happen
  ./linear_sync.py --limit 5        # just the first 5 (for smoke test)
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

# Config
DB_PATH = Path.home() / ".openclaw" / "data" / "ol-pm.db"
AUTH_FILE = Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"
LINEAR_API = "https://api.linear.app/graphql"

# Project + team (filled at startup)
OL_PROJECT_ID = "524b1f4a-6daf-455a-a972-e61a40ab788d"
RSH_TEAM_ID = "1f206304-868d-4ade-beda-ce5e1a8c7aaf"

# State map: OL status → Linear state UUID (from team RSH state list)
STATE_MAP = {
    "open":             "16f1cbfd-ae55-4d75-808f-aa9e96af1d4c",  # Backlog
    "pending-answer":   "99a5f70f-6f1a-4fac-95e0-5e11e99b6ad5",  # Todo
    "answered":         "a70179f0-f4a3-4b70-9dc3-34e22abf4309",  # Done
    "deferred":         "16f1cbfd-ae55-4d75-808f-aa9e96af1d4c",  # Backlog
}

PRIORITY_MAP = {1: 1, 2: 2, 3: 3, None: 0}  # 0=no priority, 1=urgent


def load_key() -> str:
    return json.loads(AUTH_FILE.read_text())["profiles"]["linear:default"]["token"]


def gql(api_key: str, query: str, variables: dict | None = None) -> dict:
    """Execute a Linear GraphQL query. Surfaces full response body on HTTP errors."""
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        LINEAR_API,
        data=body,
        headers={"Authorization": api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Linear HTTP {e.code}: {err_body[:600]}") from e
    if "errors" in resp:
        raise RuntimeError(f"Linear GraphQL error: {json.dumps(resp['errors'])[:600]}")
    return resp["data"]


def get_or_create_label(api_key: str, team_id: str, name: str, color: str = "#bec2c8") -> str:
    """Return label_id for `name`, creating it if missing.

    Note: Linear's API is inconsistent — filter args use ID type, input fields
    use String type. So queries and mutations need different variable types
    for the same underlying UUID.
    """
    # Lookup: filter uses ID
    data = gql(api_key, """
        query($teamId: ID!, $name: String!) {
          issueLabels(filter: { team: { id: { eq: $teamId } }, name: { eq: $name } }) {
            nodes { id name }
          }
        }
    """, {"teamId": team_id, "name": name})
    existing = data["issueLabels"]["nodes"]
    if existing:
        return existing[0]["id"]
    # Create: input uses String, and the payload field is `issueLabel` not `label`
    data = gql(api_key, """
        mutation($name: String!, $color: String, $teamIdStr: String!) {
          issueLabelCreate(input: { name: $name, color: $color, teamId: $teamIdStr }) {
            success issueLabel { id name }
          }
        }
    """, {"name": name, "color": color, "teamIdStr": team_id})
    return data["issueLabelCreate"]["issueLabel"]["id"]


def build_issue_body(q: dict) -> tuple[str, str]:
    """Return (title, body_md) for a question row."""
    text = q.get("text") or ""
    title_short = text.split(".")[0][:80] if text else f"{q['id']} question"
    title = f"{q['id']}: {title_short}"

    parts = [
        f"### {q['id']}: {q['section_title']}",
        "",
        text,
        "",
    ]
    if q.get("ask"):
        parts.extend([f"**Specific ask:** {q['ask']}", ""])
    parts.extend([
        f"- **Owner:** {q['owner']}",
        f"- **Bucket:** `{q['bucket']}`",
        f"- **Status:** {q['status']}",
        f"- **Source:** {q.get('source', 'n/a')}",
    ])
    if q.get("depends_on"):
        try:
            deps = json.loads(q["depends_on"]) if isinstance(q["depends_on"], str) else q["depends_on"]
            if deps:
                parts.append(f"- **Depends on:** {', '.join(deps)}")
        except Exception:
            pass
    if q.get("target_sprint"):
        parts.append(f"- **Target sprint:** {q['target_sprint']}")
    if q.get("status") == "answered" and q.get("answer_text"):
        parts.extend(["", "---", "", "### Answer", "", q["answer_text"], ""])
        if q.get("answered_by"):
            parts.append(f"_— {q['answered_by']}, {(q.get('answered_at') or '')[:10]}_")

    parts.extend(["", "---", "", "Live wiki: https://ol-explainer-wiki.pages.dev/ (PIN 1080)",
                  f"API: `GET /api/questions/{q['id']}`",
                  f"Auto-synced from the wiki at {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}."])

    return title, "\n".join(parts)


def sync_one(api_key: str, q: dict, label_cache: dict, dry_run: bool) -> tuple[str, str | None]:
    """Sync one question. Returns ('created'|'updated'|'noop', issue_url or None)."""
    title, body = build_issue_body(q)
    state_id = STATE_MAP.get(q["status"], STATE_MAP["open"])
    priority = PRIORITY_MAP.get(q.get("target_sprint"))

    label_names = [
        "openliteracy",
        f"section-{q['section']}",
        f"bucket-{q['bucket'].replace(' ', '-').replace('/', '-').lower()}",
    ]
    owner_first = q["owner"].split()[0].lower() if q["owner"] else "tbd"
    label_names.append(f"owner-{owner_first}")
    if q.get("target_sprint"):
        label_names.append(f"sprint-{q['target_sprint']}")

    label_ids = []
    for name in label_names:
        if name not in label_cache:
            label_cache[name] = get_or_create_label(api_key, RSH_TEAM_ID, name)
        label_ids.append(label_cache[name])

    if dry_run:
        return ("would-create" if not q.get("linear_issue_id") else "would-update", None)

    if q.get("linear_issue_id"):
        data = gql(api_key, """
            mutation($id: String!, $input: IssueUpdateInput!) {  # issue id IS String
              issueUpdate(id: $id, input: $input) {
                success issue { id url state { name } }
              }
            }
        """, {
            "id": q["linear_issue_id"],
            "input": {
                "title": title, "description": body,
                "stateId": state_id, "priority": priority,
                "labelIds": label_ids,
            },
        })
        return ("updated", data["issueUpdate"]["issue"]["url"])
    else:
        data = gql(api_key, """
            mutation($input: IssueCreateInput!) {
              issueCreate(input: $input) {
                success issue { id identifier url }
              }
            }
        """, {
            "input": {
                "teamId": RSH_TEAM_ID, "projectId": OL_PROJECT_ID,
                "title": title, "description": body,
                "stateId": state_id, "priority": priority,
                "labelIds": label_ids,
            },
        })
        issue = data["issueCreate"]["issue"]
        # Persist back to wiki DB
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE questions SET linear_issue_id = ?, linear_url = ?, updated_at = datetime('now') WHERE id = ?",
                     (issue["id"], issue["url"], q["id"]))
        conn.commit()
        conn.close()
        return ("created", issue["url"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--question", help="sync a single question by id")
    args = ap.parse_args()

    api_key = load_key()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    if args.question:
        rows = conn.execute("SELECT * FROM questions WHERE id = ?", (args.question,)).fetchall()
    else:
        sql = "SELECT * FROM questions ORDER BY section, id"
        if args.limit:
            sql += f" LIMIT {int(args.limit)}"
        rows = conn.execute(sql).fetchall()

    print(f"syncing {len(rows)} questions" + (" (DRY RUN)" if args.dry_run else ""))
    label_cache: dict = {}
    counts: dict = {}
    for r in rows:
        q = {k: r[k] for k in r.keys()}
        try:
            action, url = sync_one(api_key, q, label_cache, args.dry_run)
            counts[action] = counts.get(action, 0) + 1
            print(f"  {action:8} {q['id']}" + (f"  {url}" if url else ""))
        except Exception as e:
            print(f"  ERROR    {q['id']}: {e}")
            counts["error"] = counts.get("error", 0) + 1
        # gentle rate limit
        if not args.dry_run:
            time.sleep(0.4)
    print(f"\ndone: {counts}")
    return 0 if counts.get("error", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
