# Conversation Memory Slice 1 — Rubric

Machine-gradable success criteria. Run via:

```
deploy/outcomes/outcomes_runner.py score \
  --rubric deploy/outcomes/CONV-MEMORY-SLICE-1-RUBRIC.md \
  --checklist
```

## Types & schema

- [x] flyn_memory_router.types.Tier.CONV.value == "conv"
- [x] Importance Literal includes "conv"
- [x] flyn_memory_router.conv.schema.ConvMessage exists with required fields
- [x] flyn_memory_router.conv.schema.ConvDb provides write/search/update_summary/get_by_thread/get_by_id/stats
- [x] flyn_memory_router.conv.schema.StoredMessage exists with fts_score

## Owner registry

- [x] flyn_memory_router.conv.owner.OwnerRegistry exists
- [x] resolve_from_chat returns Owner for seeded principal
- [x] viewer_can_read returns True for viewer == owner
- [x] viewer_can_read returns False for unrelated viewer (default-deny)
- [x] grant() persists a row; subsequent viewer_can_read returns True
- [x] append_audit writes to audit_log
- [x] Schema: owners, grants, audit_log tables exist

## Encryption

- [x] conv.encrypted_raw.seal/unseal round-trip works with stubbed key
- [x] KeychainLocked raised when subprocess fails
- [x] Tamper of ciphertext byte raises InvalidTag on unseal
- [x] Per-owner key isolation: sealing with owner A's key can't be unsealed with owner B's

## Summarizer

- [x] conv.summarizer.SummarizerWorker exists with start/stop methods
- [x] SummarizeJob dataclass + enqueue function exist
- [x] Worker polls disk queue; success deletes the file
- [x] Worker timeout failure leaves the file in place for retry

## Adapters

- [x] adapters/conv_write.py:ConvWriteAdapter implements MemoryAdapter Protocol
- [x] Happy path returns WriteResult(ok=True) with row_id in detail
- [x] Unknown sender returns WriteResult(ok=False, detail contains "unknown sender")
- [x] KeychainLocked returns WriteResult(ok=False, detail contains "keychain")
- [x] adapters/conv_read.py:ConvReadAdapter implements ReadAdapter Protocol
- [x] name="conv", read_timeout=1.5, default_included=True
- [x] Cross-owner read writes audit row

## Server wiring

- [x] POST /api/memory/ingest branches on event_type="conversation_message"
- [x] Branch returns tiers_written=["conv"] on success
- [x] conv_write is registered under Tier.CONV in build_app
- [x] conv_read is registered in query.py's adapter list

## CLI

- [x] flyn-mem conv health prints per-owner stats table
- [x] flyn-mem conv search <q> prints hits with body + summary status
- [x] flyn-mem conv thread <id> prints messages in thread
- [x] flyn-mem conv replay <id> decrypts and prints original payload
- [x] replay writes an audit row with op="replay"
- [x] replay without grant returns non-zero exit code

## Install + pulse + hook

- [x] install.sh creates conv_root dir
- [x] install.sh seeds principals.json if missing
- [x] install.sh installs flyn-conv-memory-tap.sh into ~/.openclaw/hooks/
- [x] deploy/pulses/conv_summarize_backfill.sh is executable
- [x] Backfill pulse no-ops when no DBs exist
- [x] plist is valid XML and registers the daily 04:15 schedule

## Live smoke (manual; only graded with --smoke)

- [ ] Real Telegram message lands in ryan.db within 10s
- [ ] Summary fills in within 30s
- [ ] flyn-mem query returns the conv hit cross-source
- [ ] flyn-mem conv replay decrypts the original payload
- [ ] Audit log contains the replay row
- [ ] Graphiti has an episode for the message under group_id=flyn-ryan

## Soft commitments

- [x] No new launchd unit added (one new pulse only)
- [x] No new port
- [x] File size caps: each new file ≤ 200 lines
- [x] All commits follow feat(memory-router): / test(memory-router): prefix
- [x] 18+ unit + integration tests + 1 smoke + 1 ship-gate doc
