#!/usr/bin/env python3
"""Compare Work Radar and Personal Radar scores in a Horizon Markdown digest."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urldefrag


SECTION_RE = re.compile(r"^## (?P<name>.+?)\s*$", re.MULTILINE)
ITEM_RE = re.compile(
    r"^### \[(?P<title>(?:\\.|[^\]])+)\]\((?P<url>https?://[^)]+)\) "
    r"⭐️? (?P<score>\d+(?:\.\d+)?)/10\s*$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class RadarItem:
    title: str
    url: str
    score: float


def parse_profile(markdown: str, profile_name: str) -> dict[str, RadarItem]:
    sections = list(SECTION_RE.finditer(markdown))
    for index, section in enumerate(sections):
        if section.group("name").strip() != profile_name:
            continue
        end = sections[index + 1].start() if index + 1 < len(sections) else len(markdown)
        body = markdown[section.end() : end]
        items: dict[str, RadarItem] = {}
        for match in ITEM_RE.finditer(body):
            url = match.group("url")
            key = urldefrag(url)[0].rstrip("/")
            items[key] = RadarItem(
                title=match.group("title").replace("\\]", "]"),
                url=url,
                score=float(match.group("score")),
            )
        return items
    return {}


def render_comparison(
    work_items: dict[str, RadarItem], personal_items: dict[str, RadarItem]
) -> str:
    keys = sorted(set(work_items) | set(personal_items))
    lines = [
        "# Radar comparison",
        "",
        "| Project | Work | Personal | Delta (Work − Personal) |",
        "|---|---:|---:|---:|",
    ]
    for key in keys:
        work = work_items.get(key)
        personal = personal_items.get(key)
        item = work or personal
        assert item is not None
        work_score = f"{work.score:g}" if work else "—"
        personal_score = f"{personal.score:g}" if personal else "—"
        delta = f"{work.score - personal.score:+g}" if work and personal else "—"
        lines.append(
            f"| [{item.title}]({item.url}) | {work_score} | {personal_score} | {delta} |"
        )
    lines.extend(
        [
            "",
            f"Compared URLs: {sum(1 for key in keys if key in work_items and key in personal_items)}.",
            f"Work-only URLs: {sum(1 for key in keys if key in work_items and key not in personal_items)}.",
            f"Personal-only URLs: {sum(1 for key in keys if key in personal_items and key not in work_items)}.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("digest", type=Path, help="Horizon Markdown digest")
    parser.add_argument("--work-name", default="Work Radar")
    parser.add_argument("--personal-name", default="Personal Radar")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    markdown = args.digest.read_text(encoding="utf-8")
    result = render_comparison(
        parse_profile(markdown, args.work_name),
        parse_profile(markdown, args.personal_name),
    )
    if args.output:
        args.output.write_text(result, encoding="utf-8")
    else:
        print(result, end="")


if __name__ == "__main__":
    main()
