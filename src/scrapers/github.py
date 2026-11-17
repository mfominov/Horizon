"""GitHub scraper implementation."""

import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

import httpx

from .base import BaseScraper, FetchResult
from ..models import ContentItem, SourceType, GitHubSourceConfig


class GitHubScraper(BaseScraper):
    """Scraper for GitHub events and releases."""

    def __init__(self, sources: List[GitHubSourceConfig], http_client: httpx.AsyncClient):
        """Initialize GitHub scraper.

        Args:
            sources: List of GitHub source configurations
            http_client: Shared async HTTP client
        """
        super().__init__({"sources": sources}, http_client)
        self.token = os.getenv("GITHUB_TOKEN")
        self.base_url = "https://api.github.com"

    def _get_headers(self) -> dict:
        """Get request headers with optional authentication.

        Returns:
            dict: HTTP headers
        """
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Horizon-Aggregator"
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
        return headers

    def _get_authenticated_headers(self, token_env: str) -> dict:
        token = os.getenv(token_env, "").strip()
        if not token:
            raise ValueError(f"Missing environment variable: {token_env}")
        headers = self._get_headers()
        headers["Authorization"] = f"Bearer {token}"
        return headers

    async def fetch(self, since: datetime) -> FetchResult:
        """Fetch GitHub content items.

        Args:
            since: Only fetch items published after this time

        Returns:
            FetchResult: Items and per-user/repository failures
        """
        result = FetchResult()
        sources = self.config["sources"]

        for source in sources:
            if not source.enabled:
                continue

            if source.type == "user_events" and source.username:
                await result.collect(source.username, self._fetch_user_events(source, since))
            elif source.type == "repo_releases" and source.owner and source.repo:
                await result.collect(f"{source.owner}/{source.repo}", self._fetch_repo_releases(source, since))
            elif source.type == "personalized_discovery":
                await result.collect(
                    "personalized_discovery",
                    self._fetch_personalized_discovery(source, since),
                )
            else:
                result.fail(source.username or source.repo or source.type, ValueError("Invalid GitHub source configuration"))

        return result

    async def _fetch_user_events(
        self,
        source: GitHubSourceConfig,
        since: datetime,
    ) -> List[ContentItem]:
        """Fetch public events for a user.

        Args:
            source: GitHub source configuration
            since: Only fetch events after this time

        Returns:
            List[ContentItem]: Event content items
        """
        url = f"{self.base_url}/users/{source.username}/events/public"
        items = []

        response = await self.client.get(url, headers=self._get_headers(), follow_redirects=True)
        response.raise_for_status()
        events = response.json()

        for event in events:
            created_at = datetime.fromisoformat(
                event["created_at"].replace("Z", "+00:00")
            )
            if created_at < since:
                continue
            if event["type"] not in {
                "PushEvent", "CreateEvent", "ReleaseEvent", "PublicEvent", "WatchEvent"
            }:
                continue
            item = self._parse_event(event, source)
            if item:
                items.append(item)

        return items

    async def _fetch_personalized_discovery(
        self,
        source: GitHubSourceConfig,
        since: datetime,
    ) -> List[ContentItem]:
        """Discover public repositories related to the viewer's watched/starred repos."""
        headers = self._get_authenticated_headers(source.token_env)
        watched: list[dict[str, Any]] = []
        starred: list[dict[str, Any]] = []

        if "watched" in source.seed_sources:
            watched = await self._fetch_repository_pages(
                f"{self.base_url}/user/subscriptions",
                headers,
                source.max_watched_seeds,
            )
        if "starred" in source.seed_sources:
            starred = await self._fetch_repository_pages(
                f"{self.base_url}/user/starred",
                headers,
                source.max_starred_seeds,
                params={"sort": "updated", "direction": "desc"},
            )

        watched = [repo for repo in watched if self._usable_public_repository(repo)]
        starred = [repo for repo in starred if self._usable_public_repository(repo)]
        seeds = self._deduplicate_repositories([*watched, *starred])
        if not seeds:
            return []

        topic_weights, language_weights = self._seed_signals(watched, starred)
        search_signals = self._search_signals(
            topic_weights,
            language_weights,
            source.max_search_queries,
        )
        if not search_signals:
            return []

        seed_names = {str(repo["full_name"]).casefold() for repo in seeds}
        configured_cutoff = self._now() - timedelta(days=source.active_within_days)
        since_utc = self._as_utc(since)
        cutoff = max(configured_cutoff, since_utc)
        candidates: dict[str, dict[str, Any]] = {}
        query_limit = min(100, max(10, source.max_candidates * 2))

        for signal_type, signal in search_signals:
            query = self._repository_search_query(signal_type, signal, source, cutoff)
            response = await self.client.get(
                f"{self.base_url}/search/repositories",
                params={
                    "q": query,
                    "sort": "updated",
                    "order": "desc",
                    "per_page": query_limit,
                },
                headers=headers,
                follow_redirects=True,
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("items", []) if isinstance(payload, dict) else []
            for repo in rows:
                name = str(repo.get("full_name") or "").casefold()
                if not name or name in seed_names:
                    continue
                if not self._candidate_allowed(repo, source, cutoff):
                    continue
                candidates.setdefault(name, repo)

        ranked = [
            self._candidate_to_item(
                repo,
                source,
                topic_weights,
                language_weights,
                len(watched),
                len(starred),
            )
            for repo in candidates.values()
        ]
        ranked.sort(
            key=lambda item: (
                int(item.metadata["discovery_signal_score"]),
                int(item.metadata["stars"]),
                item.published_at,
            ),
            reverse=True,
        )
        return self._fan_out_items(ranked[: source.max_candidates], source)

    @staticmethod
    def _fan_out_items(
        items: list[ContentItem],
        source: GitHubSourceConfig,
    ) -> list[ContentItem]:
        if not source.fan_out_profiles or not isinstance(source.profile, list):
            return items
        expanded: list[ContentItem] = []
        for item in items:
            for profile_id in source.profile:
                profile_id = profile_id.strip()
                clone = item.model_copy(
                    deep=True,
                    update={
                        "id": f"{item.id}:{profile_id}",
                        "profile": profile_id,
                    },
                )
                clone.metadata["fan_out_profile"] = profile_id
                expanded.append(clone)
        return expanded

    async def _fetch_repository_pages(
        self,
        url: str,
        headers: dict,
        limit: int,
        params: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page = 1
        while len(rows) < limit:
            per_page = min(100, limit - len(rows))
            request_params = {**(params or {}), "per_page": per_page, "page": page}
            response = await self.client.get(
                url,
                params=request_params,
                headers=headers,
                follow_redirects=True,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError("GitHub repository listing returned an invalid response")
            rows.extend(payload)
            if len(payload) < per_page:
                break
            page += 1
        return rows[:limit]

    @staticmethod
    def _usable_public_repository(repo: dict[str, Any]) -> bool:
        return bool(repo.get("full_name")) and not repo.get("private", False)

    @staticmethod
    def _deduplicate_repositories(
        repositories: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        unique: dict[str, dict[str, Any]] = {}
        for repo in repositories:
            name = str(repo.get("full_name") or "").casefold()
            if name:
                unique.setdefault(name, repo)
        return list(unique.values())

    @staticmethod
    def _seed_signals(
        watched: list[dict[str, Any]],
        starred: list[dict[str, Any]],
    ) -> tuple[Counter[str], Counter[str]]:
        topics: Counter[str] = Counter()
        languages: Counter[str] = Counter()
        for repositories, weight in ((watched, 3), (starred, 1)):
            for repo in repositories:
                for topic in repo.get("topics") or []:
                    normalized = str(topic).strip().casefold()
                    if normalized:
                        topics[normalized] += weight
                language = str(repo.get("language") or "").strip()
                if language:
                    languages[language] += weight
        return topics, languages

    @staticmethod
    def _search_signals(
        topics: Counter[str],
        languages: Counter[str],
        limit: int,
    ) -> list[tuple[str, str]]:
        signals = [("topic", value) for value, _ in topics.most_common(limit)]
        remaining = limit - len(signals)
        if remaining > 0:
            signals.extend(
                ("language", value)
                for value, _ in languages.most_common(remaining)
            )
        return signals

    @staticmethod
    def _repository_search_query(
        signal_type: str,
        signal: str,
        source: GitHubSourceConfig,
        cutoff: datetime,
    ) -> str:
        if signal_type == "language":
            escaped_signal = signal.replace('"', '\\"')
            qualifier = f'language:"{escaped_signal}"'
        else:
            qualifier = f"{signal_type}:{signal}"
        parts = [
            qualifier,
            f"stars:>={source.min_stars}",
            f"pushed:>={cutoff.date().isoformat()}",
            "is:public",
        ]
        if source.exclude_archived:
            parts.append("archived:false")
        if source.exclude_forks:
            parts.append("fork:false")
        return " ".join(parts)

    def _candidate_allowed(
        self,
        repo: dict[str, Any],
        source: GitHubSourceConfig,
        cutoff: datetime,
    ) -> bool:
        if not self._usable_public_repository(repo):
            return False
        if repo.get("id") is None or not repo.get("html_url"):
            return False
        if source.exclude_archived and repo.get("archived", False):
            return False
        if source.exclude_forks and repo.get("fork", False):
            return False
        if self._int(repo.get("stargazers_count")) < source.min_stars:
            return False
        pushed_at = self._parse_datetime(repo.get("pushed_at"))
        return pushed_at is not None and pushed_at >= cutoff

    def _candidate_to_item(
        self,
        repo: dict[str, Any],
        source: GitHubSourceConfig,
        topic_weights: Counter[str],
        language_weights: Counter[str],
        watched_count: int,
        starred_count: int,
    ) -> ContentItem:
        full_name = str(repo["full_name"])
        topics = [str(topic).casefold() for topic in repo.get("topics") or []]
        matching_topics = [topic for topic in topics if topic in topic_weights]
        language = str(repo.get("language") or "")
        signal_score = sum(topic_weights[topic] * 10 for topic in matching_topics)
        signal_score += language_weights.get(language, 0)
        stars = self._int(repo.get("stargazers_count"))
        forks = self._int(repo.get("forks_count"))
        description = str(repo.get("description") or "").strip()
        pushed_at = self._parse_datetime(repo.get("pushed_at"))
        if pushed_at is None:
            raise ValueError(f"GitHub candidate has no pushed_at timestamp: {full_name}")
        content = "\n".join(
            line
            for line in (
                description,
                f"Language: {language}" if language else "",
                f"Topics: {', '.join(topics)}" if topics else "",
                f"Stars: {stars}; forks: {forks}",
            )
            if line
        )
        return ContentItem(
            id=self._generate_id("github", "discovery", str(repo["id"])),
            source_type=SourceType.GITHUB,
            title=f"{full_name}: {description}" if description else full_name,
            url=repo["html_url"],
            content=content,
            author=(repo.get("owner") or {}).get("login"),
            published_at=pushed_at,
            profile=source.profile,
            metadata={
                "repo": full_name,
                "stars": stars,
                "forks": forks,
                "language": language or None,
                "topics": topics,
                "matching_topics": matching_topics,
                "discovery_signal_score": signal_score,
                "watched_seed_count": watched_count,
                "starred_seed_count": starred_count,
                "personalized": True,
                "category": source.category,
            },
        )

    @staticmethod
    def _parse_datetime(value: Any) -> Optional[datetime]:
        if not value:
            return None
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if moment.tzinfo is None:
            return moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)

    @staticmethod
    def _as_utc(moment: datetime) -> datetime:
        if moment.tzinfo is None:
            return moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)

    @staticmethod
    def _int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _parse_event(self, event: dict, source: GitHubSourceConfig) -> Optional[ContentItem]:
        """Parse GitHub event into ContentItem.

        Args:
            event: GitHub event data
            username: GitHub username

        Returns:
            Optional[ContentItem]: Parsed content item or None
        """
        event_type = event["type"]
        event_id = event["id"]
        created_at = datetime.fromisoformat(event["created_at"].replace("Z", "+00:00"))
        username = source.username

        repo_name = event["repo"]["name"]
        repo_url = f"https://github.com/{repo_name}"

        # Generate title and content based on event type
        if event_type == "PushEvent":
            commits = event["payload"].get("commits", [])
            title = f"{username} pushed {len(commits)} commit(s) to {repo_name}"
            content = "\n".join([c.get("message", "") for c in commits[:3]])
        elif event_type == "CreateEvent":
            ref_type = event["payload"].get("ref_type", "repository")
            title = f"{username} created {ref_type} in {repo_name}"
            content = event["payload"].get("description", "")
        elif event_type == "ReleaseEvent":
            release = event["payload"].get("release", {})
            title = f"{username} released {release.get('tag_name', '')} in {repo_name}"
            content = release.get("body", "")
            repo_url = release.get("html_url", repo_url)
        elif event_type == "PublicEvent":
            title = f"{username} made {repo_name} public"
            content = ""
        elif event_type == "WatchEvent":
            title = f"{username} starred {repo_name}"
            content = ""
        else:
            return None

        return ContentItem(
            id=self._generate_id("github", "event", event_id),
            source_type=SourceType.GITHUB,
            title=title,
            url=repo_url,
            content=content,
            author=username,
            published_at=created_at,
            profile=source.profile,
            metadata={
                "event_type": event_type,
                "repo": repo_name,
                "category": source.category,
            }
        )

    async def _fetch_repo_releases(
        self,
        source: GitHubSourceConfig,
        since: datetime,
    ) -> List[ContentItem]:
        """Fetch releases for a repository.

        Args:
            source: GitHub source configuration
            since: Only fetch releases after this time

        Returns:
            List[ContentItem]: Release content items
        """
        owner, repo = source.owner, source.repo
        url = f"{self.base_url}/repos/{owner}/{repo}/releases"
        items = []

        response = await self.client.get(url, headers=self._get_headers(), follow_redirects=True)
        response.raise_for_status()
        releases = response.json()

        for release in releases:
            # Authenticated listings can include drafts without a publication date.
            if release.get("draft") or not release.get("published_at"):
                continue
            published_at = datetime.fromisoformat(
                release["published_at"].replace("Z", "+00:00")
            )
            if published_at < since:
                continue
            items.append(ContentItem(
                id=self._generate_id("github", "release", str(release["id"])),
                source_type=SourceType.GITHUB,
                title=f"{owner}/{repo} released {release['tag_name']}",
                url=release["html_url"],
                content=release.get("body", ""),
                author=release["author"]["login"],
                published_at=published_at,
                profile=source.profile,
                metadata={
                    "repo": f"{owner}/{repo}",
                    "tag": release["tag_name"],
                    "prerelease": release.get("prerelease", False),
                    "category": source.category,
                },
            ))

        return items
