import json
from datetime import datetime, timezone
from pathlib import Path

from src.models import Config, ContentItem, SourceType
from src.orchestrator import HorizonOrchestrator
from src.processing import ProfileRegistry
from src.storage.manager import _expand_env_vars


ROOT = Path(__file__).resolve().parents[1]


def load_radar_config(monkeypatch) -> Config:
    monkeypatch.setenv("OPENROUTER_MODEL", "openai/gpt-4.1-mini")
    monkeypatch.setenv("HORIZON_WEBHOOK_URL", "https://example.com/horizon-dry-run")
    raw = json.loads((ROOT / "data/config.radars.example.json").read_text())
    return Config.model_validate(_expand_env_vars(raw))


def test_radar_config_uses_openrouter_and_two_explicit_profiles(monkeypatch):
    config = load_radar_config(monkeypatch)
    registry = ProfileRegistry.load(ROOT / "profiles", config.processing.default_profile)

    assert config.ai.provider == "openai"
    assert config.ai.base_url == "https://openrouter.ai/api/v1"
    assert config.ai.api_key_env == "OPENROUTER_API_KEY"
    assert config.digest.group_by == "profile"
    assert config.digest.profile_order == ["work-radar", "personal-radar"]
    assert registry.get("work-radar").analysis_prompt
    assert registry.get("personal-radar").analysis_prompt
    registry.validate_source_references(config.sources.model_dump(mode="json"))


def test_every_experiment_source_is_routed_to_both_radars(monkeypatch):
    config = load_radar_config(monkeypatch)

    github_routes = {(source.owner, source.repo, source.profile) for source in config.sources.github}
    rss_routes = {(str(source.url), source.profile) for source in config.sources.rss}
    telegram_routes = {(source.channel, source.profile) for source in config.sources.telegram.channels}

    assert github_routes == {
        ("astral-sh", "uv", "work-radar"),
        ("astral-sh", "uv", "personal-radar"),
    }
    assert {profile for _, profile in rss_routes} == {"work-radar", "personal-radar"}
    assert {profile for _, profile in telegram_routes} == {"work-radar", "personal-radar"}


def test_cross_source_dedup_keeps_same_url_for_independent_radars():
    base = {
        "source_type": SourceType.RSS,
        "title": "Example",
        "url": "https://example.com/project",
        "content": "Example project",
        "published_at": datetime(2026, 11, 17, tzinfo=timezone.utc),
    }
    items = [
        ContentItem(id="work", profile="work-radar", **base),
        ContentItem(id="personal", profile="personal-radar", **base),
    ]
    orchestrator = object.__new__(HorizonOrchestrator)

    result = orchestrator.merge_cross_source_duplicates(items)

    assert [item.profile for item in result] == ["work-radar", "personal-radar"]
