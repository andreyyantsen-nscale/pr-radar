"""pr-radar: show your pending GitHub pull requests in one table."""

import argparse
import concurrent.futures
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone

MINE_COLUMNS = ["PR", "TITLE", "REVIEWERS", "DECISION", "AGE", "WAITING"]
REVIEW_COLUMNS = ["PR", "TITLE", "AUTHOR", "REVIEWERS", "AGE", "WAITING"]
LEGEND = "✔ approved  ✗ changes requested  ● commented  · pending  * code owner"
DRAFT_DIVIDER_TEXT = "── DRAFTS ──"
_DRAFT_DIVIDER = object()

_MARKS = {"APPROVED": "✔", "CHANGES_REQUESTED": "✗", "COMMENTED": "●"}
_DECISIONS = {
    "APPROVED": "approved",
    "CHANGES_REQUESTED": "changes requested",
    "REVIEW_REQUIRED": "review required",
}

ORG_LOOKUP_ARGS = ["api", "user/orgs", "--jq", ".[].login"]
TEAM_LOOKUP_ARGS = ["api", "user/teams", "--jq", '.[] | .organization.login + "/" + .slug']

SEARCH_NAMES = ["mine", "requested", "reviewed", "team"]

GRAPHQL_QUERY = """query($q: String!) {
  viewer { login }
  search(query: $q, type: ISSUE, first: 100) { issueCount nodes { ...PR } }
}
fragment PR on PullRequest {
  number title url isDraft createdAt baseRefName headRefName additions deletions changedFiles
  author { login ... on User { name } }
  repository { nameWithOwner }
  reviewDecision
  reviewRequests(first: 50) { nodes { asCodeOwner requestedReviewer {
    ... on User { login name }
    ... on Mannequin { login }
    ... on Bot { login }
    ... on Team { slug }
  } } }
  latestReviews(first: 50) { nodes { state author { login } submittedAt } }
  timelineItems(itemTypes: [READY_FOR_REVIEW_EVENT], last: 1) { nodes { ... on ReadyForReviewEvent { createdAt } } }
  commits(last: 1) { nodes { commit { committedDate statusCheckRollup { state } } } }
}"""


def run_gh(args: list[str]) -> str:
    """Run gh and return stdout. Raise CalledProcessError on a non-zero exit."""
    return subprocess.run(
        ["gh", *args], capture_output=True, text=True, check=True
    ).stdout


def _tick() -> None:
    """Print one flushed progress dot to stderr."""
    print(".", end="", file=sys.stderr, flush=True)


def _split_lines(text: str) -> list[str]:
    """Split gh's newline-delimited output, dropping blank lines."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def _search_query(name: str, org_quals: list[str], team_quals: list[str]) -> str:
    """Build one search's query string, ending in sort:created-asc."""
    base = {
        "mine": ["is:pr", "is:open", "author:@me"],
        "requested": ["is:pr", "is:open", "review-requested:@me"],
        "reviewed": ["is:pr", "is:open", "reviewed-by:@me", "-author:@me"],
        "team": ["is:pr", "is:open", "-author:@me", *team_quals],
    }[name]
    return " ".join([*base, *org_quals, "sort:created-asc"])


def fetch_pull_requests(
    orgs: list[str] | None, all_orgs: bool
) -> tuple[dict[str, list[dict] | None], set[str], str]:
    """Resolve scope, then run the four PR searches.

    Return the per-search PR nodes (a failed search maps to None), the
    names of searches truncated at 100 results, and the viewer's login
    from the first successful search.

    An org-lookup failure is fatal and propagates to the caller. A
    team-lookup failure degrades the "team" search itself to a failure,
    like any other search. A missing gh binary always propagates,
    whichever lookup or search first hits it.
    """
    results: dict[str, list[dict] | None] = {}
    truncated: set[str] = set()
    viewer_login = ""

    with concurrent.futures.ThreadPoolExecutor() as executor:
        need_org_lookup = orgs is None and not all_orgs
        phase1 = [("orgs", ORG_LOOKUP_ARGS)] if need_org_lookup else []
        phase1.append(("teams", TEAM_LOOKUP_ARGS))
        futures1 = [executor.submit(run_gh, cmd_args) for _, cmd_args in phase1]
        for future in concurrent.futures.as_completed(futures1):
            _tick()

        org_logins = list(orgs) if orgs is not None else []
        team_logins: list[str] = []
        team_lookup_failed = False
        for future, (kind, _) in zip(futures1, phase1):
            if kind == "orgs":
                org_logins = _split_lines(future.result())
                if not org_logins:
                    print(
                        "\nno orgs visible to this token; "
                        "searching all of github.com",
                        file=sys.stderr,
                    )
            else:
                try:
                    team_logins = _split_lines(future.result())
                except FileNotFoundError:
                    raise
                except Exception:
                    team_lookup_failed = True

        if team_lookup_failed:
            results["team"] = None

        org_quals = [f"org:{login}" for login in org_logins]
        team_quals = [f"team-review-requested:{login}" for login in team_logins]

        search_names = [
            name
            for name in SEARCH_NAMES
            if name != "team" or (team_logins and not team_lookup_failed)
        ]
        futures2 = [
            executor.submit(
                run_gh,
                [
                    "api",
                    "graphql",
                    "-f",
                    f"q={_search_query(name, org_quals, team_quals)}",
                    "-f",
                    f"query={GRAPHQL_QUERY}",
                ],
            )
            for name in search_names
        ]
        for future in concurrent.futures.as_completed(futures2):
            _tick()

        for future, name in zip(futures2, search_names):
            try:
                data = json.loads(future.result())["data"]
            except FileNotFoundError:
                raise
            except Exception:
                results[name] = None
                continue
            search = data["search"]
            nodes = search["nodes"]
            results[name] = nodes
            if search["issueCount"] > len(nodes):
                truncated.add(name)
            if not viewer_login:
                login = (data.get("viewer") or {}).get("login")
                if login:
                    viewer_login = login

    return results, truncated, viewer_login


def humanize(delta: timedelta) -> str:
    """Turn a duration into a short label, e.g. "3d 4h".

    Show the largest non-zero unit. Append the next smaller unit only
    when it is non-zero. Clamp negative durations to zero.
    """
    total_seconds = max(delta.total_seconds(), 0)
    total_minutes = int(total_seconds // 60)
    weeks, remainder = divmod(total_minutes, 7 * 24 * 60)
    days, remainder = divmod(remainder, 24 * 60)
    hours, minutes = divmod(remainder, 60)
    units = [("w", weeks), ("d", days), ("h", hours), ("m", minutes)]
    for index, (label, value) in enumerate(units):
        if not value:
            continue
        parts = [f"{value}{label}"]
        if index + 1 < len(units):
            next_label, next_value = units[index + 1]
            if next_value:
                parts.append(f"{next_value}{next_label}")
        return " ".join(parts)
    return "0m"


def waiting_since(pr: dict) -> datetime:
    """Return the moment a PR started waiting for review.

    Use the last READY_FOR_REVIEW event. Fall back to the PR's
    creation time when no such event exists.
    """
    ready_events = pr["timelineItems"]["nodes"]
    timestamp = ready_events[-1]["createdAt"] if ready_events else pr["createdAt"]
    return datetime.fromisoformat(timestamp)


def reviewer_marks(pr: dict) -> dict[str, str]:
    """Map each reviewer to a mark string.

    A login (or "#slug" for a team) maps to one mark: a verdict
    (approved / changes requested / commented), or a pending mark
    ("·", "·*" for a code owner). A dismissed review counts for
    nothing. A pending request overrides an earlier verdict, because
    a re-request voids it.
    """
    marks: dict[str, str] = {}
    for review in pr["latestReviews"]["nodes"]:
        author = review.get("author")
        if author is None:
            continue
        mark = _MARKS.get(review["state"])
        if mark is None:
            continue
        marks[author["login"]] = mark
    for request in pr["reviewRequests"]["nodes"]:
        reviewer = request.get("requestedReviewer")
        if reviewer is None:
            continue
        slug = reviewer.get("slug")
        login = reviewer.get("login")
        if slug:
            key = f"#{slug}"
        elif login:
            key = login
        else:
            continue
        marks[key] = "·*" if request.get("asCodeOwner") else "·"
    return marks


def _reviewer_entries(pr: dict) -> list[str]:
    """List a PR's reviewer marks, sorted and uncapped.

    Order: users alphabetically by login, then teams alphabetically
    by slug.
    """
    marks = reviewer_marks(pr)
    users = sorted(key for key in marks if not key.startswith("#"))
    teams = sorted(key for key in marks if key.startswith("#"))
    return [f"{key}{marks[key]}" for key in users + teams]


def reviewer_cell(pr: dict) -> str:
    """Join a PR's reviewer marks into one cell.

    Cap the cell at 6 entries, with a "+k" tail for the rest, so a PR
    with many reviewers cannot wrap the row.
    """
    entries = _reviewer_entries(pr)
    if len(entries) > 6:
        entries = entries[:6] + [f"+{len(entries) - 6}"]
    return " ".join(entries)


def _passes_freshness_filter(pr: dict, viewer_login: str) -> bool:
    """Decide whether a "reviewed" PR still needs the viewer's attention.

    Find the viewer's latest non-dismissed review. Keep the PR when
    that review is older than the last commit. Keep the PR (fail
    open) when there is no such review, or no known commit date.
    """
    viewer_reviews = [
        review
        for review in pr["latestReviews"]["nodes"]
        if review.get("author") is not None
        and review["author"]["login"] == viewer_login
        and review["state"] != "DISMISSED"
    ]
    if not viewer_reviews:
        return True
    latest_review = max(viewer_reviews, key=lambda review: review["submittedAt"])
    commit_nodes = pr["commits"]["nodes"]
    if not commit_nodes:
        return True
    commit_date = datetime.fromisoformat(commit_nodes[-1]["commit"]["committedDate"])
    review_date = datetime.fromisoformat(latest_review["submittedAt"])
    return review_date < commit_date


def _sorted_section(nodes: list[dict]) -> list[dict]:
    """Order one section's PRs: non-drafts first, then drafts.

    Non-drafts sort by waiting duration, longest first (earliest
    waiting_since first). Drafts sort by age, oldest first. Both tie
    break by url, so output is deterministic.
    """
    non_drafts = sorted(
        (pr for pr in nodes if not pr["isDraft"]),
        key=lambda pr: (waiting_since(pr), pr["url"]),
    )
    drafts = sorted(
        (pr for pr in nodes if pr["isDraft"]),
        key=lambda pr: (datetime.fromisoformat(pr["createdAt"]), pr["url"]),
    )
    return non_drafts + drafts


def classify(
    results: dict[str, list[dict] | None], viewer_login: str
) -> tuple[list[dict], list[dict], list[str]]:
    """Split search results into the Mine and Review sections.

    Mine is the "mine" search's nodes. Review is every "requested"
    and "team" node, plus "reviewed" nodes that pass the freshness
    filter, deduplicated by url. A failed search (a None value) is
    named in the third element instead of raising.
    """
    failed = [name for name, nodes in results.items() if nodes is None]

    mine = _sorted_section(list(results.get("mine") or []))

    seen_urls: set[str] = set()
    review_nodes = []
    for name in ("requested", "team"):
        for pr in results.get(name) or []:
            if pr["url"] not in seen_urls:
                seen_urls.add(pr["url"])
                review_nodes.append(pr)
    for pr in results.get("reviewed") or []:
        if pr["url"] in seen_urls:
            continue
        if _passes_freshness_filter(pr, viewer_login):
            seen_urls.add(pr["url"])
            review_nodes.append(pr)
    review = _sorted_section(review_nodes)

    return mine, review, failed


def _pr_cell_text(pr: dict) -> str:
    return f"{pr['repository']['nameWithOwner']}#{pr['number']}"


def _title_cell(pr: dict) -> str:
    title = pr["title"]
    if len(title) > 50:
        return title[:49] + "…"
    return title


def _author_cell(pr: dict) -> str:
    author = pr.get("author")
    login = "ghost" if author is None else author["login"]
    name = None if author is None else author.get("name") or None
    return f"{name} ({login})" if name else login


def _decision_cell(pr: dict) -> str:
    return _DECISIONS.get(pr["reviewDecision"], "—")


def _age_cell(pr: dict, now: datetime) -> str:
    return humanize(now - datetime.fromisoformat(pr["createdAt"]))


def _waiting_cell(pr: dict, now: datetime) -> str:
    if pr["isDraft"]:
        return "—"
    return humanize(now - waiting_since(pr))


def _highlight_cell(pr: dict, login: str) -> str:
    login_cf = login.casefold()
    for key, mark in reviewer_marks(pr).items():
        if not key.startswith("#") and key.casefold() == login_cf:
            return mark
    return ""


def _highlighted_line(pr: dict, highlight: list[str]) -> str | None:
    """Build the "Highlighted: ..." detailed-view line, or None.

    Return None when no highlighted login has a mark on this PR.
    """
    entries = [
        f"{login}{mark}" for login in highlight if (mark := _highlight_cell(pr, login))
    ]
    if not entries:
        return None
    return "Highlighted: " + " ".join(entries)


def _ci_cell(pr: dict) -> str:
    """Show the head commit's CI status, or "none" when unavailable."""
    commit_nodes = pr["commits"]["nodes"]
    if not commit_nodes:
        return "none"
    rollup = commit_nodes[-1]["commit"].get("statusCheckRollup")
    return rollup["state"] if rollup else "none"


def _branches_cell(pr: dict) -> str:
    """Show the PR's branch pair as "head → base"."""
    return f"{pr['headRefName']} → {pr['baseRefName']}"


def _size_cell(pr: dict) -> str:
    """Show the PR's diff size as "+additions/-deletions, N files"."""
    return f"+{pr['additions']}/-{pr['deletions']}, {pr['changedFiles']} files"


def _mine_cells(pr: dict, now: datetime) -> list[str]:
    return [
        _pr_cell_text(pr),
        _title_cell(pr),
        reviewer_cell(pr),
        _decision_cell(pr),
        _age_cell(pr, now),
        _waiting_cell(pr, now),
    ]


def _review_cells(pr: dict, now: datetime) -> list[str]:
    return [
        _pr_cell_text(pr),
        _title_cell(pr),
        _author_cell(pr),
        reviewer_cell(pr),
        _age_cell(pr, now),
        _waiting_cell(pr, now),
    ]


def _pad_row(cells: list[str], widths: list[int], url: str | None, links: bool) -> str:
    parts = []
    last = len(cells) - 1
    for index, cell in enumerate(cells):
        text = cell
        if index == 0 and links and url:
            text = f"\x1b]8;;{url}\x1b\\{cell}\x1b]8;;\x1b\\"
        if index < last:
            text += " " * (widths[index] - len(cell))
        parts.append(text)
    return "  ".join(parts)


def _render_table(columns: list[str], rows: list, links: bool) -> list[str]:
    widths = [len(column) for column in columns]
    for row in rows:
        if row is _DRAFT_DIVIDER:
            continue
        _, cells = row
        for index, cell in enumerate(cells):
            widths[index] = max(widths[index], len(cell))

    lines = [_pad_row(columns, widths, None, False)]
    for row in rows:
        if row is _DRAFT_DIVIDER:
            lines.append(DRAFT_DIVIDER_TEXT)
            continue
        url, cells = row
        lines.append(_pad_row(cells, widths, url, links))
    return lines


def _section_header(title: str, key: str, incomplete: dict[str, list[str]]) -> str:
    """Build one section's header line, with an incomplete-search suffix."""
    header = title
    failed = incomplete.get(key)
    if failed:
        detail = ", ".join(f"{name} search failed" for name in failed)
        header += f"  (incomplete: {detail})"
    return header


def _section_lines(
    title: str,
    key: str,
    prs: list[dict],
    now: datetime,
    links: bool,
    highlight: list[str],
    incomplete: dict[str, list[str]],
    columns: list[str],
    cell_builder,
) -> list[str]:
    header = _section_header(title, key, incomplete)
    if not prs:
        return [header + "  (none)"]

    rows = []
    divider_added = False
    for pr in prs:
        if pr["isDraft"] and not divider_added:
            rows.append(_DRAFT_DIVIDER)
            divider_added = True
        cells = cell_builder(pr, now) + [
            _highlight_cell(pr, login) for login in highlight
        ]
        rows.append((pr["url"], cells))

    return [header] + _render_table(columns + list(highlight), rows, links)


def _detail_lines(
    pr: dict, now: datetime, highlight: list[str], links: bool, section: str
) -> list[str]:
    """Render one PR as a multi-line block for the --detailed view."""
    pr_id = _pr_cell_text(pr)
    if links:
        pr_id = f"\x1b]8;;{pr['url']}\x1b\\{pr_id}\x1b]8;;\x1b\\"
    lines = [f"{pr_id}  {pr['title']}"]

    if section == "review":
        lines.append(f"  Author: {_author_cell(pr)}")
    lines.append(f"  Reviewers: {' '.join(_reviewer_entries(pr))}")

    highlighted = _highlighted_line(pr, highlight)
    if highlighted is not None:
        lines.append(f"  {highlighted}")

    age = _age_cell(pr, now)
    waiting = _waiting_cell(pr, now)
    if section == "mine":
        lines.append(f"  Decision: {_decision_cell(pr)} · Age: {age} · Waiting: {waiting}")
    else:
        lines.append(f"  Age: {age} · Waiting: {waiting}")

    lines.append(
        f"  CI: {_ci_cell(pr)} · Branches: {_branches_cell(pr)} · Size: {_size_cell(pr)}"
    )
    return lines


def _detailed_section(
    title: str,
    key: str,
    prs: list[dict],
    now: datetime,
    links: bool,
    highlight: list[str],
    incomplete: dict[str, list[str]],
) -> list[str]:
    """Render one section as --detailed blocks, mirroring `_section_lines`."""
    header = _section_header(title, key, incomplete)
    if not prs:
        return [header + "  (none)"]

    lines = [header]
    divider_added = False
    for pr in prs:
        if pr["isDraft"] and not divider_added:
            lines.append("")
            lines.append(DRAFT_DIVIDER_TEXT)
            divider_added = True
        lines.append("")
        lines.extend(_detail_lines(pr, now, highlight, links, key))
    return lines


def build_output(
    mine: list[dict],
    review: list[dict],
    now: datetime,
    highlight: list[str],
    links: bool,
    incomplete: dict[str, list[str]],
    detailed: bool = False,
) -> str:
    """Render the Mine and Review sections into one block of text.

    `mine` and `review` must already be in display order (see
    `classify`); this function only lays them out, splits off drafts
    at the "── DRAFTS ──" divider, and adds the legend. In table mode
    (the default) each section is a table; in detailed mode each PR
    is a multi-line block.
    """
    lines = []
    if detailed:
        lines.extend(
            _detailed_section(
                "MY OPEN PULL REQUESTS", "mine", mine, now, links, highlight, incomplete
            )
        )
        lines.append("")
        lines.extend(
            _detailed_section(
                "WAITING FOR MY REVIEW",
                "review",
                review,
                now,
                links,
                highlight,
                incomplete,
            )
        )
    else:
        lines.extend(
            _section_lines(
                "MY OPEN PULL REQUESTS",
                "mine",
                mine,
                now,
                links,
                highlight,
                incomplete,
                MINE_COLUMNS,
                _mine_cells,
            )
        )
        lines.append("")
        lines.extend(
            _section_lines(
                "WAITING FOR MY REVIEW",
                "review",
                review,
                now,
                links,
                highlight,
                incomplete,
                REVIEW_COLUMNS,
                _review_cells,
            )
        )
    lines.append("")
    lines.append(LEGEND)
    return "\n".join(lines)


def _split_csv(value: str) -> list[str]:
    """Split a comma-separated CLI value, stripping whitespace."""
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="pr-radar")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--orgs", type=_split_csv, default=None)
    scope.add_argument("--all", action="store_true")
    parser.add_argument("--highlight-reviewers", type=_split_csv, default=[])
    parser.add_argument("--detailed", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run pr-radar: parse args, fetch, classify, render, print.

    Return 1 when gh fails outright or every search fails; 0 otherwise.
    """
    args = _parse_args(argv)
    now = datetime.now(timezone.utc)

    print("Fetching pull requests...", end="", file=sys.stderr, flush=True)
    try:
        results, truncated, viewer_login = fetch_pull_requests(args.orgs, args.all)
    except FileNotFoundError:
        print(file=sys.stderr)
        print("gh not found. Install the GitHub CLI.", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(file=sys.stderr)
        print((exc.stderr or "").rstrip("\n"), file=sys.stderr)
        return 1

    mine, review, failed = classify(results, viewer_login)

    trunc_suffix = ", truncated at 100" if truncated else ""
    failed_suffix = "".join(f"; {name} search failed" for name in failed)
    print(
        f" done (mine {len(mine)}, to review {len(review)}){trunc_suffix}{failed_suffix}",
        file=sys.stderr,
    )

    incomplete: dict[str, list[str]] = {}
    mine_failed = [name for name in failed if name == "mine"]
    review_failed = [name for name in failed if name != "mine"]
    if mine_failed:
        incomplete["mine"] = mine_failed
    if review_failed:
        incomplete["review"] = review_failed

    output = build_output(
        mine,
        review,
        now,
        args.highlight_reviewers,
        sys.stdout.isatty(),
        incomplete,
        args.detailed,
    )
    print(output)

    if failed and all(value is None for value in results.values()):
        return 1
    return 0
