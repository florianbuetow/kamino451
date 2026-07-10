# Dispatch-Queue Retention Policy

The dispatch queue (`.kamino/dispatch-queue/<run_id>/`) holds one directory per
run attempt: the instantiated agent, execution graph, route decision, work
artifacts, trace, and run evidence. Together with the task detail and the
ledger record, a run dir is the attempt's **replay capsule**.

## Policy

1. **Referenced capsules are kept indefinitely.** A run dir whose agent file is
   named in any ledger record's `agent_files_used` is part of the factory's
   audit and replay history — `replay`, `failure-analyze`, the trace reviews,
   and the error-analysis UI all resolve into it by path. Deleting it orphans
   the record.
2. **Unreferenced run dirs are prunable.** A dir that no ledger record points
   at (an abandoned instantiation, a compile that never ran, a superseded
   retry that was never recorded) carries no history and may be deleted.
3. Pruning is explicit, never automatic. Run the deterministic pruner:

```bash
# List what would be deleted (default; changes nothing):
uv run .kamino/evals/scripts/prune_dispatch_queue.py \
  --dispatch-dir ".kamino/dispatch-queue" \
  --ledger ".kamino/evals/tasks/task-outcome-ledger.jsonl" \
  --format json

# Actually delete the unreferenced dirs:
uv run ... --apply
```

4. If disk or repository size ever forces trimming *referenced* capsules,
   trim from the inside out — delete `work/tests*` copies first (recoverable
   from the corpus), never `trace.jsonl`, `run-evidence.json`,
   `execution-graph.md`, the instantiated agent, or the produced solution —
   and record the trim in this file. As of 2026-07-04 no such trim has been
   needed (~190 capsules, tens of MB).
