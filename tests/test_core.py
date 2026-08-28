"""Tests for pr-radar domain logic: durations, classification, and rendering."""

from datetime import datetime, timedelta

import pytest

from helpers import make_pr

from pr_radar import (
    DRAFT_DIVIDER_TEXT,
    build_output,
    classify,
    humanize,
    reviewer_cell,
    waiting_since,
)

# --- durations ---------------------------------------------------------

HUMANIZE_CASES = [
    (timedelta(seconds=59), "0m"),
    (timedelta(seconds=61), "1m"),
    (timedelta(hours=1), "1h"),
    (timedelta(hours=25), "1d 1h"),
    (timedelta(days=3, minutes=12), "3d"),
    (timedelta(days=8), "1w 1d"),
    (timedelta(days=15), "2w 1d"),
    (timedelta(seconds=-5), "0m"),
]


@pytest.mark.parametrize("delta, expected", HUMANIZE_CASES)
def test_humanize(delta, expected):
    assert humanize(delta) == expected


def test_waiting_since_uses_ready_for_review_event():
    pr = make_pr(created="2026-08-01T00:00:00Z", ready_at="2026-08-03T00:00:00Z")
    assert waiting_since(pr) == datetime.fromisoformat("2026-08-03T00:00:00Z")


def test_waiting_since_falls_back_to_created_at():
    pr = make_pr(created="2026-08-01T00:00:00Z", ready_at=None)
    assert waiting_since(pr) == datetime.fromisoformat("2026-08-01T00:00:00Z")


# --- classification ------------------------------------------------------


def test_classify_requested_bypasses_freshness_filter():
    # The viewer's review is not older than the last commit, but even if it
    # were, membership in "requested" must skip the freshness check.
    pr = make_pr(
        number=1,
        reviews=[("me", "APPROVED", "2026-08-05T00:00:00Z")],
        last_commit="2026-08-01T00:00:00Z",
    )
    results = {"mine": [], "requested": [pr], "reviewed": [], "team": []}

    _, review, failed = classify(results, "me")

    assert review == [pr]
    assert failed == []


def test_classify_reviewed_kept_when_commit_newer_than_review():
    pr = make_pr(
        number=2,
        reviews=[("me", "APPROVED", "2026-08-01T00:00:00Z")],
        last_commit="2026-08-02T00:00:00Z",
    )
    results = {"mine": [], "requested": [], "reviewed": [pr], "team": []}

    _, review, _ = classify(results, "me")

    assert review == [pr]


def test_classify_reviewed_dropped_when_commit_older_than_review():
    pr = make_pr(
        number=3,
        reviews=[("me", "APPROVED", "2026-08-02T00:00:00Z")],
        last_commit="2026-08-01T00:00:00Z",
    )
    results = {"mine": [], "requested": [], "reviewed": [pr], "team": []}

    _, review, _ = classify(results, "me")

    assert review == []


def test_classify_reviewed_kept_when_viewer_review_missing():
    pr = make_pr(number=4, reviews=[], last_commit="2026-08-02T00:00:00Z")
    results = {"mine": [], "requested": [], "reviewed": [pr], "team": []}

    _, review, _ = classify(results, "me")

    assert review == [pr]


def test_classify_reviewed_kept_when_viewer_review_dismissed():
    pr = make_pr(
        number=5,
        reviews=[("me", "DISMISSED", "2026-08-05T00:00:00Z")],
        last_commit="2026-08-01T00:00:00Z",
    )
    results = {"mine": [], "requested": [], "reviewed": [pr], "team": []}

    _, review, _ = classify(results, "me")

    assert review == [pr]


def test_classify_reviewed_kept_when_commit_date_unknown():
    pr = make_pr(
        number=6,
        reviews=[("me", "APPROVED", "2026-08-05T00:00:00Z")],
        last_commit=None,
    )
    results = {"mine": [], "requested": [], "reviewed": [pr], "team": []}

    _, review, _ = classify(results, "me")

    assert review == [pr]


def test_classify_team_search_lands_in_review():
    pr = make_pr(number=7)
    results = {"mine": [], "requested": [], "reviewed": [], "team": [pr]}

    _, review, _ = classify(results, "me")

    assert review == [pr]


def test_classify_dedups_by_url():
    pr = make_pr(number=8)
    results = {"mine": [], "requested": [pr], "reviewed": [pr], "team": [pr]}

    _, review, _ = classify(results, "me")

    assert review == [pr]


def test_classify_mine_is_mine_nodes():
    pr = make_pr(number=9)
    results = {"mine": [pr], "requested": [], "reviewed": [], "team": []}

    mine, _, _ = classify(results, "me")

    assert mine == [pr]


def test_classify_reports_failed_searches():
    results = {"mine": None, "requested": [], "reviewed": None, "team": []}

    mine, review, failed = classify(results, "me")

    assert mine == []
    assert review == []
    assert failed == ["mine", "reviewed"]


def test_classify_draft_split_and_sort_orders():
    # Non-drafts sort by waiting duration, longest first (earliest
    # waiting_since first). Drafts sort by age, oldest first.
    pr_a = make_pr(number=1, repo="acme/a", created="2026-08-01T00:00:00Z")
    pr_b = make_pr(number=2, repo="acme/b", created="2026-08-05T00:00:00Z")
    draft_older = make_pr(
        number=3, repo="acme/c", draft=True, created="2026-08-02T00:00:00Z"
    )
    draft_newer = make_pr(
        number=4, repo="acme/d", draft=True, created="2026-08-03T00:00:00Z"
    )
    results = {
        "mine": [pr_b, draft_newer, pr_a, draft_older],
        "requested": [],
        "reviewed": [],
        "team": [],
    }

    mine, _, _ = classify(results, "me")

    assert mine == [pr_a, pr_b, draft_older, draft_newer]


def test_classify_sort_tie_break_by_url():
    pr_z = make_pr(number=1, repo="acme/z", created="2026-08-01T00:00:00Z")
    pr_a = make_pr(number=2, repo="acme/a", created="2026-08-01T00:00:00Z")
    results = {"mine": [pr_z, pr_a], "requested": [], "reviewed": [], "team": []}

    mine, _, _ = classify(results, "me")

    assert mine == [pr_a, pr_z]


# --- reviewer marks / cell -----------------------------------------------

REVIEWER_CELL_CASES = [
    (
        "approved",
        dict(reviews=[("alice", "APPROVED", "2026-08-01T00:00:00Z")]),
        "alice✔",
    ),
    (
        "changes_requested",
        dict(reviews=[("bob", "CHANGES_REQUESTED", "2026-08-01T00:00:00Z")]),
        "bob✗",
    ),
    (
        "commented",
        dict(reviews=[("carol", "COMMENTED", "2026-08-01T00:00:00Z")]),
        "carol●",
    ),
    ("pending_request", dict(requests=[("dave", False)]), "dave·"),
    ("pending_codeowner", dict(requests=[("erin", True)]), "erin·*"),
    (
        "dismissed_hidden",
        dict(reviews=[("frank", "DISMISSED", "2026-08-01T00:00:00Z")]),
        "",
    ),
    (
        "re_request_overrides_verdict",
        dict(
            reviews=[("grace", "APPROVED", "2026-08-01T00:00:00Z")],
            requests=[("grace", False)],
        ),
        "grace·",
    ),
    (
        "re_request_codeowner_overrides_verdict",
        dict(
            reviews=[("henry", "CHANGES_REQUESTED", "2026-08-01T00:00:00Z")],
            requests=[("henry", True)],
        ),
        "henry·*",
    ),
    ("team_codeowner", dict(requests=[("#platform", True)]), "#platform·*"),
    (
        "users_before_teams_alphabetical",
        dict(
            reviews=[("bob", "APPROVED", "2026-08-01T00:00:00Z")],
            requests=[("alice", False), ("#zeta", False), ("#alpha", False)],
        ),
        "alice· bob✔ #alpha· #zeta·",
    ),
    (
        "skip_null_review_author",
        dict(
            extra={
                "latestReviews": {
                    "nodes": [
                        {
                            "state": "APPROVED",
                            "author": None,
                            "submittedAt": "2026-08-01T00:00:00Z",
                        }
                    ]
                }
            }
        ),
        "",
    ),
    (
        "skip_pending_state",
        dict(
            extra={
                "latestReviews": {
                    "nodes": [
                        {
                            "state": "PENDING",
                            "author": {"login": "ivan"},
                            "submittedAt": "2026-08-01T00:00:00Z",
                        }
                    ]
                }
            }
        ),
        "",
    ),
    (
        "skip_empty_requested_reviewer",
        dict(
            extra={
                "reviewRequests": {
                    "nodes": [{"asCodeOwner": False, "requestedReviewer": None}]
                }
            }
        ),
        "",
    ),
]


@pytest.mark.parametrize(
    "kwargs, expected",
    [(c[1], c[2]) for c in REVIEWER_CELL_CASES],
    ids=[c[0] for c in REVIEWER_CELL_CASES],
)
def test_reviewer_cell(kwargs, expected):
    pr = make_pr(**kwargs)
    assert reviewer_cell(pr) == expected


def test_reviewer_cell_caps_at_six_plus_tail():
    requests = [(f"user{i}", False) for i in range(7)]
    pr = make_pr(requests=requests)

    cell = reviewer_cell(pr)

    parts = cell.split(" ")
    assert parts[:6] == [f"user{i}·" for i in range(6)]
    assert parts[6] == "+1"
    assert len(parts) == 7


# --- rendering ------------------------------------------------------------


def test_osc8_padding_stays_outside_the_link():
    now = datetime.fromisoformat("2026-08-15T00:00:00Z")
    short = make_pr(number=1, repo="acme/pr", title="Short")
    long_ = make_pr(number=2, repo="acme/a-long-repo-name", title="Short2")

    output = build_output([short, long_], [], now, [], True, {})

    row_line = next(line for line in output.splitlines() if "acme/pr#1" in line)
    pr_text = "acme/pr#1"
    width = len("acme/a-long-repo-name#2")
    expected_prefix = (
        f"\x1b]8;;{short['url']}\x1b\\{pr_text}\x1b]8;;\x1b\\"
        + " " * (width - len(pr_text))
        + "  "
    )
    assert row_line.startswith(expected_prefix)


def test_title_truncation_boundary():
    now = datetime.fromisoformat("2026-08-15T00:00:00Z")
    title_50 = "x" * 50
    title_51 = "y" * 51
    pr_50 = make_pr(number=1, repo="acme/a", title=title_50)
    pr_51 = make_pr(number=2, repo="acme/b", title=title_51)

    output = build_output([pr_50, pr_51], [], now, [], False, {})

    assert title_50 in output
    assert ("y" * 49 + "…") in output
    assert title_51 not in output


def test_incomplete_section_header_suffix():
    now = datetime.fromisoformat("2026-08-15T00:00:00Z")
    pr = make_pr()

    output = build_output([], [pr], now, [], False, {"review": ["requested"]})

    assert "WAITING FOR MY REVIEW  (incomplete: requested search failed)" in output
    assert "MY OPEN PULL REQUESTS  (none)" in output


def test_links_false_emits_no_escape_bytes():
    now = datetime.fromisoformat("2026-08-15T00:00:00Z")
    pr = make_pr()

    output = build_output([pr], [], now, [], False, {})

    assert "\x1b" not in output


# --- colour ----------------------------------------------------------------


def test_color_paints_reviewer_entries_by_mark():
    now = datetime.fromisoformat("2026-08-15T00:00:00Z")
    pr = make_pr(
        number=1,
        repo="acme/a",
        reviews=[
            ("alice", "APPROVED", "2026-08-01T00:00:00Z"),
            ("bob", "CHANGES_REQUESTED", "2026-08-01T00:00:00Z"),
            ("carol", "COMMENTED", "2026-08-01T00:00:00Z"),
        ],
        requests=[("dave", False)],
    )

    output = build_output([pr], [], now, [], False, {}, color=True)

    assert "\x1b[32malice✔\x1b[0m" in output
    assert "\x1b[31mbob✗\x1b[0m" in output
    assert "\x1b[33mcarol●\x1b[0m" in output
    # Pending stays plain: the entry follows a reset with no new colour.
    assert "\x1b[0m dave·" in output


def test_color_paints_decision_cell():
    now = datetime.fromisoformat("2026-08-15T00:00:00Z")
    pr = make_pr(number=1, repo="acme/a", decision="APPROVED")

    output = build_output([pr], [], now, [], False, {}, color=True)

    assert "\x1b[32mapproved\x1b[0m" in output


def test_color_paints_legend_marks():
    now = datetime.fromisoformat("2026-08-15T00:00:00Z")

    output = build_output([], [], now, [], False, {}, color=True)

    assert "\x1b[32m✔ approved\x1b[0m" in output
    assert "\x1b[31m✗ changes requested\x1b[0m" in output
    assert "\x1b[33m● commented\x1b[0m" in output
    assert "· pending" in output


def test_color_paints_highlight_column_mark():
    now = datetime.fromisoformat("2026-08-15T00:00:00Z")
    pr = make_pr(number=1, repo="acme/a", reviews=[("alice", "APPROVED", "2026-08-01T00:00:00Z")])

    output = build_output([pr], [], now, ["alice"], False, {}, color=True)

    row_line = next(line for line in output.splitlines() if "acme/a#1" in line)
    assert row_line.rstrip().endswith("\x1b[32m✔\x1b[0m")


def test_color_dims_draft_rows_and_divider():
    now = datetime.fromisoformat("2026-08-15T00:00:00Z")
    draft = make_pr(
        number=2,
        repo="acme/b",
        draft=True,
        reviews=[("alice", "APPROVED", "2026-08-01T00:00:00Z")],
    )

    output = build_output([draft], [], now, [], False, {}, color=True)

    assert "\x1b[90macme/b#2\x1b[0m" in output
    assert "\x1b[2;32malice✔\x1b[0m" in output
    assert "\x1b[2;90m── DRAFTS" in output


def test_color_false_emits_no_escape_bytes():
    now = datetime.fromisoformat("2026-08-15T00:00:00Z")
    draft = make_pr(number=1, repo="acme/a", draft=True, decision="APPROVED")

    output = build_output([draft], [], now, [], False, {}, color=False)

    assert "\x1b" not in output


# --- terminal width ---------------------------------------------------------


def test_wide_terminal_lets_title_grow_past_50():
    now = datetime.fromisoformat("2026-08-15T00:00:00Z")
    title = "x" * 60
    pr = make_pr(number=1, repo="acme/a", title=title)

    output = build_output([pr], [], now, [], False, {}, width=200)

    assert title in output


def test_narrow_terminal_floors_title_and_strips_conventional_prefix():
    now = datetime.fromisoformat("2026-08-15T00:00:00Z")
    pr = make_pr(
        number=1,
        repo="acme/averyveryverylongreponame",
        title="feat(api): implement wonderful widget factory thing",
    )

    output = build_output([pr], [], now, [], False, {}, width=80)

    assert "feat(api)" not in output
    assert "implement wond…" in output


def test_narrow_terminal_reduces_reviewer_cap_keeping_highlighted():
    now = datetime.fromisoformat("2026-08-15T00:00:00Z")
    requests = [(f"reviewer{i}", False) for i in range(7)]
    pr = make_pr(number=1, repo="acme/a", title="T", requests=requests)

    output = build_output([pr], [], now, ["reviewer6"], False, {}, width=80)

    row_line = next(line for line in output.splitlines() if "acme/a#1" in line)
    assert "reviewer6·" in row_line
    assert "reviewer0· reviewer6· +5" in row_line


def test_narrow_terminal_drops_author_name_keeps_login():
    now = datetime.fromisoformat("2026-08-15T00:00:00Z")
    pr = make_pr(
        number=1,
        repo="acme/a",
        title="A modest medium title",
        author=("alice", "Alexandrina Considerable-Longname"),
    )

    output = build_output([], [pr], now, [], False, {}, width=90)

    assert "Alexandrina" not in output
    row_line = next(line for line in output.splitlines() if "acme/a#1" in line)
    assert "alice" in row_line
    assert "A modest medium title" in output


def test_table_divider_stretches_to_table_width():
    now = datetime.fromisoformat("2026-08-15T00:00:00Z")
    pr = make_pr(number=1, repo="acme/a", created="2026-08-01T00:00:00Z")
    draft = make_pr(number=2, repo="acme/b", draft=True, created="2026-08-02T00:00:00Z")

    output = build_output([pr, draft], [], now, [], False, {})

    lines = output.splitlines()
    divider_line = next(line for line in lines if line.startswith("── DRAFTS"))
    header_line = next(line for line in lines if line.startswith("PR "))
    assert len(divider_line) > len(DRAFT_DIVIDER_TEXT)
    assert len(divider_line) == len(header_line)


# --- detailed view ---------------------------------------------------------


def test_detailed_title_is_never_truncated():
    now = datetime.fromisoformat("2026-08-15T00:00:00Z")
    title = "z" * 51
    pr = make_pr(number=1, repo="acme/a", title=title)

    output = build_output([pr], [], now, [], False, {}, detailed=True)

    assert title in output


def test_detailed_mine_block_has_decision_not_author():
    now = datetime.fromisoformat("2026-08-15T00:00:00Z")
    pr = make_pr(number=1, repo="acme/a", decision="APPROVED")

    output = build_output([pr], [], now, [], False, {}, detailed=True)

    assert "Decision: approved" in output
    assert "Author:" not in output


def test_detailed_review_block_has_author_not_decision():
    now = datetime.fromisoformat("2026-08-15T00:00:00Z")
    pr = make_pr(
        number=1, repo="acme/a", author=("alice", "Alice A"), decision="APPROVED"
    )

    output = build_output([], [pr], now, [], False, {}, detailed=True)

    assert "Author: Alice A (alice)" in output
    assert "Decision:" not in output


def test_detailed_reviewers_uncapped():
    now = datetime.fromisoformat("2026-08-15T00:00:00Z")
    requests = [(f"user{i}", False) for i in range(7)]
    pr = make_pr(number=1, repo="acme/a", requests=requests)

    output = build_output([pr], [], now, [], False, {}, detailed=True)

    line = next(
        line for line in output.splitlines() if line.strip().startswith("Reviewers:")
    )
    assert "+1" not in line
    for i in range(7):
        assert f"user{i}·" in line


def test_detailed_highlighted_line_shown_when_matched():
    now = datetime.fromisoformat("2026-08-15T00:00:00Z")
    pr = make_pr(number=1, repo="acme/a", requests=[("alice", False)])

    output = build_output([pr], [], now, ["alice"], False, {}, detailed=True)

    assert "Highlighted: alice·" in output


def test_detailed_highlighted_line_omitted_when_no_match():
    now = datetime.fromisoformat("2026-08-15T00:00:00Z")
    pr = make_pr(number=1, repo="acme/a")

    output = build_output([pr], [], now, ["alice"], False, {}, detailed=True)

    assert "Highlighted:" not in output


def test_detailed_ci_cell_shows_state():
    now = datetime.fromisoformat("2026-08-15T00:00:00Z")
    pr = make_pr(
        number=1, repo="acme/a", last_commit="2026-08-01T00:00:00Z", ci_state="SUCCESS"
    )

    output = build_output([pr], [], now, [], False, {}, detailed=True)

    assert "CI: SUCCESS" in output


def test_detailed_ci_cell_none_when_rollup_null():
    now = datetime.fromisoformat("2026-08-15T00:00:00Z")
    pr = make_pr(number=1, repo="acme/a", last_commit="2026-08-01T00:00:00Z")

    output = build_output([pr], [], now, [], False, {}, detailed=True)

    assert "CI: none" in output


def test_detailed_ci_cell_none_when_no_commits():
    now = datetime.fromisoformat("2026-08-15T00:00:00Z")
    pr = make_pr(number=1, repo="acme/a", last_commit=None)

    output = build_output([pr], [], now, [], False, {}, detailed=True)

    assert "CI: none" in output


def test_detailed_branches_and_size_lines():
    now = datetime.fromisoformat("2026-08-15T00:00:00Z")
    pr = make_pr(
        number=1,
        repo="acme/a",
        base_ref="main",
        head_ref="feature/x",
        additions=120,
        deletions=45,
        changed_files=6,
    )

    output = build_output([pr], [], now, [], False, {}, detailed=True)

    assert "Branches: feature/x → main" in output
    assert "Size: +120/-45, 6 files" in output


def test_detailed_osc8_wraps_only_pr_id():
    now = datetime.fromisoformat("2026-08-15T00:00:00Z")
    pr = make_pr(number=1, repo="acme/a", title="Some title")

    output = build_output([pr], [], now, [], True, {}, detailed=True)

    expected = f"\x1b]8;;{pr['url']}\x1b\\acme/a#1\x1b]8;;\x1b\\  Some title"
    assert expected in output


def test_detailed_links_false_emits_no_escape_bytes():
    now = datetime.fromisoformat("2026-08-15T00:00:00Z")
    pr = make_pr(number=1, repo="acme/a")

    output = build_output([pr], [], now, [], False, {}, detailed=True)

    assert "\x1b" not in output


def test_detailed_none_and_incomplete_suffix():
    now = datetime.fromisoformat("2026-08-15T00:00:00Z")
    pr = make_pr(number=1, repo="acme/a")

    output = build_output(
        [], [pr], now, [], False, {"review": ["requested"]}, detailed=True
    )

    assert "MY OPEN PULL REQUESTS  (none)" in output
    assert "WAITING FOR MY REVIEW  (incomplete: requested search failed)" in output


def test_detailed_color_paints_ci_and_dims_draft():
    now = datetime.fromisoformat("2026-08-15T00:00:00Z")
    pr = make_pr(
        number=1, repo="acme/a", last_commit="2026-08-01T00:00:00Z", ci_state="SUCCESS"
    )
    draft = make_pr(
        number=2,
        repo="acme/b",
        draft=True,
        reviews=[("alice", "APPROVED", "2026-08-01T00:00:00Z")],
    )

    output = build_output([pr, draft], [], now, [], False, {}, detailed=True, color=True)

    assert "\x1b[32mSUCCESS\x1b[0m" in output
    assert "\x1b[90macme/b#2\x1b[0m" in output
    assert "\x1b[2;32malice✔\x1b[0m" in output
    assert "\x1b[2;90m── DRAFTS ──\x1b[0m" in output


def test_detailed_drafts_divider():
    now = datetime.fromisoformat("2026-08-15T00:00:00Z")
    pr = make_pr(number=1, repo="acme/a", created="2026-08-01T00:00:00Z")
    draft = make_pr(
        number=2, repo="acme/b", draft=True, created="2026-08-02T00:00:00Z"
    )

    output = build_output([pr, draft], [], now, [], False, {}, detailed=True)

    assert (
        output.index("acme/a#1")
        < output.index(DRAFT_DIVIDER_TEXT)
        < output.index("acme/b#2")
    )
