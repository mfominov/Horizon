from scripts.compare_radars import parse_profile, render_comparison


DIGEST = """# Horizon Daily - 2026-11-17

## Work Radar

### [Useful Project](https://example.com/useful) ⭐️ 8/10

Work review.

### [Enterprise Only](https://example.com/enterprise) ⭐️ 7/10

Work review.

## Personal Radar

### [Useful Project](https://example.com/useful/) ⭐️ 9/10

Personal review.

### [Home Lab Only](https://example.com/home) ⭐️ 6/10

Personal review.
"""


def test_compare_radars_matches_urls_and_reports_unpaired_items():
    work = parse_profile(DIGEST, "Work Radar")
    personal = parse_profile(DIGEST, "Personal Radar")

    comparison = render_comparison(work, personal)

    assert "| [Useful Project](https://example.com/useful) | 8 | 9 | -1 |" in comparison
    assert "Compared URLs: 1." in comparison
    assert "Work-only URLs: 1." in comparison
    assert "Personal-only URLs: 1." in comparison
