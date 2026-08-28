"""Tests for pr-radar's CLI: argument parsing, fetch wiring, end-to-end runs."""

import os
import subprocess

import pytest

from helpers import fake_gh, make_pr, search_response

import pr_radar


def _search_args(name, org_quals=(), team_quals=(), include_drafts=False):
    """Build the exact gh args a search call makes, for fake_gh keys."""
    query = pr_radar._search_query(
        name, list(org_quals), list(team_quals), include_drafts
    )
    return [
        "api",
        "graphql",
        "-f",
        f"q={query}",
        "-f",
        f"query={pr_radar.GRAPHQL_QUERY}",
    ]


# --- argument parsing ------------------------------------------------------


def test_orgs_and_all_conflict_exits_with_status_2():
    with pytest.raises(SystemExit) as exc_info:
        pr_radar.main(["--orgs", "acme", "--all"])
    assert exc_info.value.code == 2


def test_csv_values_are_split_and_stripped():
    args = pr_radar._parse_args(
        ["--orgs", " acme , other ", "--highlight-reviewers", "alice, bob "]
    )
    assert args.orgs == ["acme", "other"]
    assert args.highlight_reviewers == ["alice", "bob"]


def test_detailed_flag_defaults_false():
    args = pr_radar._parse_args([])
    assert args.detailed is False


def test_detailed_flag_parses_true():
    args = pr_radar._parse_args(["--detailed"])
    assert args.detailed is True


def test_include_drafts_defaults_false():
    args = pr_radar._parse_args([])
    assert args.include_drafts is False


def test_include_drafts_flag_parses_true():
    args = pr_radar._parse_args(["--include-drafts"])
    assert args.include_drafts is True


def test_json_flag_defaults_false():
    args = pr_radar._parse_args([])
    assert args.json is False


def test_json_flag_parses_true():
    assert pr_radar._parse_args(["-j"]).json is True
    assert pr_radar._parse_args(["--json"]).json is True


@pytest.mark.parametrize(
    "argv",
    [["--json", "--detailed"], ["--json", "--highlight-reviewers", "alice"]],
    ids=["detailed", "highlight"],
)
def test_json_rejects_rendering_flags(argv):
    with pytest.raises(SystemExit) as exc_info:
        pr_radar._parse_args(argv)
    assert exc_info.value.code == 2


def test_end_to_end_json_output(monkeypatch, capsys):
    import json

    org_quals = ["org:acme"]
    pr = make_pr(number=1, repo="acme/a", title="Some title")
    responses = {
        tuple(pr_radar.ORG_LOOKUP_ARGS): "acme\n",
        tuple(pr_radar.TEAM_LOOKUP_ARGS): "",
        tuple(_search_args("mine", org_quals)): search_response([pr]),
        tuple(_search_args("requested", org_quals)): search_response([]),
        tuple(_search_args("reviewed", org_quals)): search_response([]),
    }
    monkeypatch.setattr(pr_radar, "run_gh", fake_gh(responses))

    exit_code = pr_radar.main(["-j"])

    out, _ = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(out)
    assert payload["mine"][0]["repo"] == "acme/a"
    assert payload["mine"][0]["number"] == 1
    assert payload["searches"] == {
        "mine": "ok",
        "requested": "ok",
        "reviewed": "ok",
    }
    assert "\x1b" not in out
    assert "code owner" not in out


def test_short_flags_parse_like_long_forms():
    args = pr_radar._parse_args(["-o", "acme", "-r", "alice", "-d", "-i"])
    assert args.orgs == ["acme"]
    assert args.highlight_reviewers == ["alice"]
    assert args.detailed is True
    assert args.include_drafts is True
    assert pr_radar._parse_args(["-a"]).all is True


def test_search_query_excludes_drafts_by_default():
    query = pr_radar._search_query("mine", [], [])
    assert "draft:false" in query
    assert query.endswith("sort:created-asc")


def test_search_query_keeps_drafts_when_included():
    query = pr_radar._search_query("mine", [], [], include_drafts=True)
    assert "draft:false" not in query
    assert query.endswith("sort:created-asc")


# --- colour and width gates ---------------------------------------------------


def test_use_color_requires_a_tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    assert pr_radar._use_color(True) is True
    assert pr_radar._use_color(False) is False


def test_use_color_respects_no_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "xterm-256color")
    assert pr_radar._use_color(True) is False


def test_use_color_empty_no_color_does_not_disable(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "")
    monkeypatch.setenv("TERM", "xterm-256color")
    assert pr_radar._use_color(True) is True


def test_use_color_respects_dumb_term(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    assert pr_radar._use_color(True) is False


def test_effective_width_none_when_piped():
    assert pr_radar._effective_width(False) is None


def test_effective_width_floored_at_80(monkeypatch):
    monkeypatch.setattr(
        pr_radar.shutil, "get_terminal_size", lambda: os.terminal_size((60, 24))
    )
    assert pr_radar._effective_width(True) == 80


def test_effective_width_uses_terminal_columns(monkeypatch):
    monkeypatch.setattr(
        pr_radar.shutil, "get_terminal_size", lambda: os.terminal_size((132, 24))
    )
    assert pr_radar._effective_width(True) == 132


# --- highlight matching -----------------------------------------------------


def test_highlight_reviewer_matches_case_insensitively(monkeypatch, capsys):
    org_quals = ["org:acme"]
    pr = make_pr(number=1, repo="acme/a", requests=[("Alice", False)])
    responses = {
        tuple(pr_radar.ORG_LOOKUP_ARGS): "acme\n",
        tuple(pr_radar.TEAM_LOOKUP_ARGS): "",
        tuple(_search_args("mine", org_quals)): search_response([pr]),
        tuple(_search_args("requested", org_quals)): search_response([]),
        tuple(_search_args("reviewed", org_quals)): search_response([]),
    }
    monkeypatch.setattr(pr_radar, "run_gh", fake_gh(responses))

    exit_code = pr_radar.main(["--highlight-reviewers", "alice"])

    out, _ = capsys.readouterr()
    assert exit_code == 0
    header_line = next(line for line in out.splitlines() if "WAITING" in line)
    assert "alice" in header_line
    row_line = next(line for line in out.splitlines() if "acme/a#1" in line)
    assert row_line.rstrip().endswith("·")


# --- end-to-end --------------------------------------------------------------


def test_end_to_end_renders_expected_structure(monkeypatch, capsys):
    org_quals = ["org:acme"]
    old = make_pr(number=1, repo="acme/a", title="Old", created="2026-08-01T00:00:00Z")
    new = make_pr(number=2, repo="acme/b", title="New", created="2026-08-10T00:00:00Z")
    draft = make_pr(
        number=3,
        repo="acme/c",
        title="Draft",
        draft=True,
        created="2026-08-05T00:00:00Z",
    )
    responses = {
        tuple(pr_radar.ORG_LOOKUP_ARGS): "acme\n",
        tuple(pr_radar.TEAM_LOOKUP_ARGS): "",
        tuple(_search_args("mine", org_quals, include_drafts=True)): search_response(
            []
        ),
        tuple(
            _search_args("requested", org_quals, include_drafts=True)
        ): search_response([new, old, draft], issue_count=150),
        tuple(
            _search_args("reviewed", org_quals, include_drafts=True)
        ): search_response([]),
    }
    monkeypatch.setattr(pr_radar, "run_gh", fake_gh(responses))

    exit_code = pr_radar.main(["--include-drafts"])

    out, err = capsys.readouterr()

    assert exit_code == 0
    mine_index = out.index("MY OPEN PULL REQUESTS")
    review_index = out.index("WAITING FOR MY REVIEW")
    assert mine_index < review_index
    assert "MY OPEN PULL REQUESTS  (none)" in out
    assert (
        out.index("acme/a#1")
        < out.index("acme/b#2")
        < out.index(pr_radar.DRAFT_DIVIDER_TEXT)
        < out.index("acme/c#3")
    )
    assert pr_radar.LEGEND in out
    assert "Fetching" not in out
    assert "Fetching" in err
    assert ", truncated at 100" in err
    assert "done (mine 0, to review 3)" in err


def test_end_to_end_detailed_renders_blocks(monkeypatch, capsys):
    org_quals = ["org:acme"]
    pr = make_pr(number=1, repo="acme/a", title="Some title")
    responses = {
        tuple(pr_radar.ORG_LOOKUP_ARGS): "acme\n",
        tuple(pr_radar.TEAM_LOOKUP_ARGS): "",
        tuple(_search_args("mine", org_quals)): search_response([pr]),
        tuple(_search_args("requested", org_quals)): search_response([]),
        tuple(_search_args("reviewed", org_quals)): search_response([]),
    }
    monkeypatch.setattr(pr_radar, "run_gh", fake_gh(responses))

    exit_code = pr_radar.main(["--detailed"])

    out, _ = capsys.readouterr()
    assert exit_code == 0
    assert "Reviewers:" in out
    assert "CI:" in out
    assert "DECISION" not in out
    assert "REVIEWERS" not in out


# --- gh failure paths ---------------------------------------------------------


def test_missing_gh_binary_is_fatal(monkeypatch, capsys):
    def fake(_args):
        raise FileNotFoundError()

    monkeypatch.setattr(pr_radar, "run_gh", fake)

    exit_code = pr_radar.main([])

    _, err = capsys.readouterr()
    assert exit_code == 1
    assert "gh not found. Install the GitHub CLI." in err


def test_org_lookup_failure_is_fatal(monkeypatch, capsys):
    error = subprocess.CalledProcessError(1, ["gh"], stderr="boom\n")
    responses = {
        tuple(pr_radar.ORG_LOOKUP_ARGS): error,
        tuple(pr_radar.TEAM_LOOKUP_ARGS): "",
    }
    monkeypatch.setattr(pr_radar, "run_gh", fake_gh(responses))

    exit_code = pr_radar.main([])

    _, err = capsys.readouterr()
    assert exit_code == 1
    assert "boom" in err


def test_team_lookup_failure_degrades_like_a_failed_search(monkeypatch, capsys):
    org_quals = ["org:acme"]
    error = subprocess.CalledProcessError(1, ["gh"], stderr="team lookup boom\n")
    responses = {
        tuple(pr_radar.ORG_LOOKUP_ARGS): "acme\n",
        tuple(pr_radar.TEAM_LOOKUP_ARGS): error,
        tuple(_search_args("mine", org_quals)): search_response([]),
        tuple(_search_args("requested", org_quals)): search_response([]),
        tuple(_search_args("reviewed", org_quals)): search_response([]),
    }
    monkeypatch.setattr(pr_radar, "run_gh", fake_gh(responses))

    exit_code = pr_radar.main([])

    out, err = capsys.readouterr()
    assert exit_code == 0
    assert "; team search failed" in err
    assert "WAITING FOR MY REVIEW  (incomplete: team search failed)" in out


def test_one_failed_search_degrades_gracefully(monkeypatch, capsys):
    org_quals = ["org:acme"]
    mine_pr = make_pr(number=1, repo="acme/a")
    error = subprocess.CalledProcessError(1, ["gh"], stderr="502\n")
    responses = {
        tuple(pr_radar.ORG_LOOKUP_ARGS): "acme\n",
        tuple(pr_radar.TEAM_LOOKUP_ARGS): "",
        tuple(_search_args("mine", org_quals)): search_response([mine_pr]),
        tuple(_search_args("requested", org_quals)): error,
        tuple(_search_args("reviewed", org_quals)): search_response([]),
    }
    monkeypatch.setattr(pr_radar, "run_gh", fake_gh(responses))

    exit_code = pr_radar.main([])

    out, err = capsys.readouterr()
    assert exit_code == 0
    assert "acme/a#1" in out
    assert "WAITING FOR MY REVIEW  (incomplete: requested search failed)" in out
    assert "; requested search failed" in err


def test_all_searches_failed_returns_1(monkeypatch):
    org_quals = ["org:acme"]
    error = subprocess.CalledProcessError(1, ["gh"], stderr="boom\n")
    responses = {
        tuple(pr_radar.ORG_LOOKUP_ARGS): "acme\n",
        tuple(pr_radar.TEAM_LOOKUP_ARGS): "",
        tuple(_search_args("mine", org_quals)): error,
        tuple(_search_args("requested", org_quals)): error,
        tuple(_search_args("reviewed", org_quals)): error,
    }
    monkeypatch.setattr(pr_radar, "run_gh", fake_gh(responses))

    exit_code = pr_radar.main([])

    assert exit_code == 1
