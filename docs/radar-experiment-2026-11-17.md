# Project Discovery radar experiment — 2026-11-17

## Scope

The experiment used the configuration-only implementation from
`data/config.radars.example.json`:

- OpenRouter with `openai/gpt-4.1-mini`;
- separate `work-radar` and `personal-radar` profiles;
- one GitHub Releases source, one RSS feed, and one Telegram channel, each
  routed to both profiles;
- a 24-hour collection window;
- Markdown output;
- no outbound webhook delivery.

No Horizon core source files were changed for this experiment. The OpenRouter
key was supplied at runtime from the existing encrypted secret store and was
not written to the repository or command output.

## Observed result

| Metric | Result |
|---|---:|
| Collected records | 24 |
| Telegram records | 20 |
| RSS records | 4 |
| GitHub records | 0 |
| Unique URLs compared in both radars | 12 |
| Work-only URLs | 0 |
| Personal-only URLs | 0 |
| Mean Work score | 2.08/10 |
| Mean Personal score | 2.42/10 |
| Mean absolute score difference | 0.67 |
| OpenRouter token usage | 46,933 |

Five URLs scored higher in Personal Radar, two scored higher in Work Radar,
and five received equal scores. The largest observed difference was two
points: Claude Code desktop support for the iPhone Duo simulator scored 2 in
Work Radar and 4 in Personal Radar. The highest-scoring item was the `llm`
0.37.3 release note, at 5 in Work Radar and 6 in Personal Radar.

The generated digest was saved only in a local temporary directory. A separate
webhook dry-run rendered the request without sending it. During the live radar
run the webhook URL was deliberately absent, so Horizon skipped delivery.

## Decisions for the MVP

1. **Keep the 0–10 score for now.** The scale is sufficient for an initial
   ranking experiment. Expanding it to 0–100 would add apparent precision
   without fixing calibration. The compressed 1–6 range means the next step is
   to improve source relevance and profile examples, then rerun the comparison.
2. **Do not patch fan-out yet.** Duplicating a source entry per profile produced
   complete paired results without changing the core. This fetches the same
   source twice, so a small fan-out patch becomes worthwhile only if the source
   set or collection cost grows materially.
3. **Do not add PostgreSQL or feedback to this MVP.** Horizon's file-backed
   state and Markdown output are enough to evaluate discovery and ranking.
   Reconsider persistence and feedback after repeated runs demonstrate useful
   ranking and define the actual feedback workflow.

These are experiment-level decisions, not production-readiness evidence. The
sample sources are intentionally small and include low-relevance material; the
next run should use the owner's real DevOps/SRE and homelab source shortlist.
