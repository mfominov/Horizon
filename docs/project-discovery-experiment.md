# Project Discovery experiment

This fork evaluates Horizon as the Project Discovery MVP without changing Horizon's core pipeline. The same public sources are intentionally listed twice with explicit `work-radar` and `personal-radar` routes. Horizon therefore produces two independent 0-10 evaluations for the same URL.

## Boundaries

- OpenRouter is the only LLM endpoint; no Ollama or laptop connectivity is required.
- The example uses one GitHub release feed, one RSS feed, and one public Telegram channel already present in Horizon's upstream example. Replace these public examples with the desired source list after the experiment.
- Profile thresholds are zero so the first experiment retains low scores for comparison.
- Output remains Horizon's local Markdown digest. No PostgreSQL or feedback workflow is added.
- Webhook validation is dry-run only. The example URL must never be used for a real send.

## Configure

```bash
cp data/config.radars.example.json data/config.json
cp .env.example .env
```

Set `OPENROUTER_API_KEY` in the untracked `.env`. The example fixes `OPENROUTER_MODEL=openai/gpt-4.1-mini` so both radars use the same model; change it deliberately when comparing model quality. Do not commit `.env`.

## Validate without sending

```bash
OPENROUTER_MODEL=openai/gpt-4.1-mini \
HORIZON_WEBHOOK_URL=https://example.com/horizon-dry-run \
uv run horizon-webhook --config data/config.radars.example.json --lang ru --dry-run
```

This renders the exact request locally and sends no HTTP request.

## Run the collection experiment

```bash
uv run horizon --config data/config.radars.example.json --hours 24
```

The digest is written to `data/summaries/horizon-YYYY-MM-DD-ru.md`.

## Compare the radars

```bash
uv run python scripts/compare_radars.py \
  data/summaries/horizon-YYYY-MM-DD-ru.md \
  --output data/summaries/radar-comparison-YYYY-MM-DD.md
```

Review paired URLs, score deltas, and the explanation/unknowns in both sections. Only after several representative runs should the experiment decide whether the single 0-10 score is sufficient, whether source collection should fan out to both profiles after one fetch, and whether PostgreSQL or feedback is justified.
