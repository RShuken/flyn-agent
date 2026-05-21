---
name: broken-link-fix
triggers:
  - "this link is invalid"
  - "broken link"
  - "404"
  - "link doesn't work"
  - "page not found"
  - "the URL is wrong"
when-not-to-use:
  - User is asking what a link IS, not reporting it's broken (use memory-recall)
---

# broken-link-fix

When Ryan reports a URL is broken / 404 / invalid. **Address the report.
Do not dump memory.**

## Steps

1. **Acknowledge the report.** One sentence: "Right, that branch was renamed / merged / deleted."
2. **Find what replaced it.** For GitHub URLs:
   ```
   gh repo view RShuken/flyn-agent --json defaultBranchRef
   gh api repos/RShuken/flyn-agent/branches | jq '.[].name' | head -20
   git -C ~/AI/openclaw/flyn-agent log --all --oneline -- '<path if any>' | head -10
   ```
3. **Suggest the corrected URL.** If you can find the right one, give it
   directly. If you can't, ask Ryan what the current canonical name is.
4. **Offer to fix the source.** If the broken link came from a file you
   wrote (digest script, AGENTS.md reference, etc.), offer: "Want me to
   update the source so this doesn't repeat?"

## Tone

Direct. One short paragraph. No memory dumps. No "Random pull from..."
framing. Just: "yeah that's broken because X, here's the right URL, want
me to fix the source?"
