---
name: ship-gate-check
triggers:
  - "is X deployed"
  - "did Y ship"
  - "what's the gate status"
  - "rubric for"
  - "is X done"
  - "phase 5 ship gate"
when-not-to-use:
  - Ryan is asking about Cora-business decisions (use project-status-update)
---

# ship-gate-check

When Ryan asks about deployment / phase / rubric status.

## Steps

1. **Find the relevant rubric.** Rubrics live at:
   ```
   /Users/4c/AI/openclaw/flyn-agent/deploy/outcomes/*RUBRIC.md
   ```
   Common ones: `CONV-MEMORY-SLICE-1-RUBRIC.md`, `CONV-TIER-2.0-RUBRIC.md`,
   `ORCHESTRATOR-PHASE-RUBRIC.md`.

2. **Read it.** Count checked vs unchecked items:
   ```
   grep -c '^- \[x\]' <rubric>   # done
   grep -c '^- \[ \]' <rubric>   # remaining
   ```

3. **Report.** "Phase X: N/M green. The remaining items are: ..." Be specific
   about what's still blocking ship.

## Format

Concise. One paragraph. If Ryan wants more detail, he'll ask.
