# Project Discovery experiment

This fork evaluates Horizon as the Project Discovery MVP without changing Horizon's core pipeline. Shared discovery sources fan out to explicit `work-radar`, `personal-radar`, and `security-radar` routes where an independent score is useful. Dedicated security feeds are routed only to Security Radar so current CVEs and vendor advisories do not displace the other two digests.

## Boundaries

- OpenRouter is the only LLM endpoint; no Ollama or laptop connectivity is required.
- The example uses a small public source set aligned with platform engineering,
  SRE, networking, and self-hosting: Deckhouse, Kubernetes, NetBird, wg-easy,
  the Kubernetes and selfh.st feeds, and DevOps FM. Security Radar adds official
  CISA, Ubuntu Security Notices, GitHub Security, and Google Project Zero feeds.
  Private channels belong in an untracked runtime configuration.
- Security Radar prioritizes fresh, actionable CVEs with affected-version,
  exploitation, and remediation evidence. Its score is review priority, not
  proof that the deployed environment is vulnerable.
- Profile thresholds are zero so the first experiment retains low scores for comparison.
- Output remains Horizon's local Markdown digest. No PostgreSQL or feedback workflow is added.
- Webhook validation is dry-run only. The example URL must never be used for a real send.

## Configure

```bash
cp data/config.radars.example.json data/config.json
cp .env.example .env
```

Set `OPENROUTER_API_KEY` in the untracked `.env`. The example fixes `OPENROUTER_MODEL=openai/gpt-4.1-mini` so all radars use the same model; change it deliberately when comparing model quality. Do not commit `.env`.

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

## Compare Work and Personal Radar

```bash
uv run python scripts/compare_radars.py \
  data/summaries/horizon-YYYY-MM-DD-ru.md \
  --output data/summaries/radar-comparison-YYYY-MM-DD.md
```

Review paired URLs, score deltas, and the explanation/unknowns in both sections. Review Security Radar separately for CVE freshness, affected-version evidence, exploit status, and actionable remediation. Only after several representative runs should the experiment decide whether the single 0-10 score is sufficient, whether additional collector fan-out is needed, and whether PostgreSQL or feedback is justified.

## Container image

The fork's CI is prepared to publish `ghcr.io/mfominov/horizon` only from the
default branch or a `v*` tag after all test and security jobs pass. See
[`docs/container-publishing.md`](container-publishing.md) for tags, provenance,
visibility, and deployment boundaries.
