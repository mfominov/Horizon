import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from pydantic import ValidationError

from src.models import GitHubSourceConfig
from src.scrapers.github import GitHubScraper


NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)


def repository(
    repo_id: int,
    full_name: str,
    *,
    topics: list[str],
    language: str = "Go",
    stars: int = 100,
    private: bool = False,
    archived: bool = False,
    fork: bool = False,
) -> dict:
    return {
        "id": repo_id,
        "full_name": full_name,
        "html_url": f"https://github.com/{full_name}",
        "description": f"Description for {full_name}",
        "owner": {"login": full_name.split("/", 1)[0]},
        "topics": topics,
        "language": language,
        "stargazers_count": stars,
        "forks_count": 7,
        "private": private,
        "archived": archived,
        "fork": fork,
        "pushed_at": (NOW - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        "updated_at": NOW.isoformat().replace("+00:00", "Z"),
    }


def personalized_source(**overrides) -> GitHubSourceConfig:
    values = {
        "type": "personalized_discovery",
        "seed_sources": ["watched", "starred"],
        "max_watched_seeds": 20,
        "max_starred_seeds": 20,
        "active_within_days": 180,
        "max_search_queries": 3,
        "max_candidates": 10,
        "min_stars": 50,
        "category": "github-discovery",
        "profile": ["work-radar", "personal-radar"],
        "fan_out_profiles": True,
    }
    values.update(overrides)
    return GitHubSourceConfig(**values)


def test_personalized_discovery_uses_watched_and_starred_seeds(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    watched = repository(
        1,
        "seed/watched",
        topics=["kubernetes", "platform-engineering"],
        stars=500,
    )
    starred = repository(
        2,
        "seed/starred",
        topics=["self-hosted", "kubernetes"],
        language="Python",
        stars=200,
    )
    private_seed = repository(
        3,
        "private/seed",
        topics=["secret-internal-topic"],
        private=True,
    )
    candidate = repository(
        10,
        "new/platform-tool",
        topics=["kubernetes", "platform-engineering"],
        stars=900,
    )
    archived = repository(
        11,
        "old/archived",
        topics=["kubernetes"],
        stars=2000,
        archived=True,
    )
    private_candidate = repository(
        12,
        "private/candidate",
        topics=["kubernetes"],
        stars=3000,
        private=True,
    )
    search_queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-token"
        if request.url.path == "/user/subscriptions":
            return httpx.Response(200, json=[watched, private_seed])
        if request.url.path == "/user/starred":
            return httpx.Response(200, json=[starred])
        if request.url.path == "/search/repositories":
            search_queries.append(request.url.params["q"])
            return httpx.Response(
                200,
                json={"items": [candidate, watched, archived, private_candidate]},
            )
        raise AssertionError(f"unexpected request: {request.url}")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            scraper = GitHubScraper([personalized_source()], client)
            scraper._now = lambda: NOW
            return await scraper.fetch(NOW - timedelta(hours=24))

    result = asyncio.run(run())

    assert result.status == "success"
    assert len(result.items) == 2
    assert {item.profile for item in result.items} == {
        "work-radar",
        "personal-radar",
    }
    assert len({item.id for item in result.items}) == 2
    item = result.items[0]
    assert item.title.startswith("new/platform-tool")
    assert item.metadata["category"] == "github-discovery"
    assert item.metadata["personalized"] is True
    assert item.metadata["watched_seed_count"] == 1
    assert item.metadata["starred_seed_count"] == 1
    assert item.metadata["matching_topics"] == [
        "kubernetes",
        "platform-engineering",
    ]
    assert len(search_queries) == 3
    assert any("topic:kubernetes" in query for query in search_queries)
    assert all("archived:false" in query for query in search_queries)
    assert all("fork:false" in query for query in search_queries)
    assert all("is:public" in query for query in search_queries)
    assert all("secret-internal-topic" not in query for query in search_queries)


def test_personalized_discovery_requires_token_without_requests(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"request should not be sent: {request.url}")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await GitHubScraper([personalized_source()], client).fetch(NOW)

    result = asyncio.run(run())

    assert result.status == "failure"
    assert "GITHUB_TOKEN" in result.failures["personalized_discovery"]


def test_personalized_discovery_caps_starred_seed_pagination(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    starred_requests: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/starred":
            page = int(request.url.params["page"])
            starred_requests.append(page)
            count = 100 if page == 1 else 1
            start = (page - 1) * 100
            return httpx.Response(
                200,
                json=[
                    repository(
                        start + index,
                        f"seed/repo-{start + index}",
                        topics=["platform-engineering"],
                    )
                    for index in range(count)
                ],
            )
        if request.url.path == "/search/repositories":
            return httpx.Response(200, json={"items": []})
        raise AssertionError(f"unexpected request: {request.url}")

    source = personalized_source(
        seed_sources=["starred"],
        max_starred_seeds=101,
        max_search_queries=1,
    )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            scraper = GitHubScraper([source], client)
            scraper._now = lambda: NOW
            return await scraper.fetch(NOW - timedelta(hours=24))

    result = asyncio.run(run())

    assert result.status == "empty"
    assert starred_requests == [1, 2]


def test_personalized_discovery_rejects_unbounded_search_configuration():
    with pytest.raises(ValidationError):
        personalized_source(max_search_queries=11)


def test_personalized_discovery_quotes_language_search_values():
    query = GitHubScraper._repository_search_query(
        "language",
        "C++",
        personalized_source(),
        NOW,
    )

    assert query.startswith('language:"C++" ')


def test_personalized_discovery_fan_out_requires_profile_list():
    with pytest.raises(ValidationError):
        personalized_source(profile="work-radar", fan_out_profiles=True)


def test_personalized_discovery_validates_secret_reference_and_profiles():
    with pytest.raises(ValidationError):
        personalized_source(token_env="not an env name")
    with pytest.raises(ValidationError):
        personalized_source(
            profile=["work-radar", "work-radar"],
            fan_out_profiles=True,
        )
