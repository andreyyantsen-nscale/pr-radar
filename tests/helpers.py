"""Test builders for GraphQL PR nodes and a fake gh runner.

This module is additive-only: tests may add parameters and builders,
but must not change existing behavior.
"""

import json


def make_pr(
    number=1,
    title="Add widgets",
    repo="acme/widgets",
    author=("alice", "Alice A"),
    draft=False,
    created="2026-08-01T00:00:00Z",
    reviews=(),
    requests=(),
    ready_at=None,
    last_commit=None,
    decision=None,
    base_ref="main",
    head_ref="feature",
    additions=0,
    deletions=0,
    changed_files=0,
    ci_state=None,
    extra=None,
):
    """Build one PR node in the GraphQL response shape.

    author: (login, name) tuple, or None for a deleted user.
    reviews: (login, state, submitted_at) tuples for latestReviews.
    requests: (login_or_"#slug", as_codeowner) tuples for reviewRequests.
    ready_at: timestamp of the last READY_FOR_REVIEW event, or None.
    last_commit: committedDate of the last commit, or None for no commits.
    ci_state: statusCheckRollup state on the last commit, or None for no
        rollup. Ignored when last_commit is None.
    extra: dict merged over the node, for malformed shapes.
    """
    commit = None
    if last_commit is not None:
        commit = {"committedDate": last_commit}
        if ci_state is not None:
            commit["statusCheckRollup"] = {"state": ci_state}
    node = {
        "number": number,
        "title": title,
        "url": f"https://github.com/{repo}/pull/{number}",
        "isDraft": draft,
        "createdAt": created,
        "author": None if author is None else {"login": author[0], "name": author[1]},
        "repository": {"nameWithOwner": repo},
        "reviewDecision": decision,
        "baseRefName": base_ref,
        "headRefName": head_ref,
        "additions": additions,
        "deletions": deletions,
        "changedFiles": changed_files,
        "reviewRequests": {
            "nodes": [
                {
                    "asCodeOwner": as_codeowner,
                    "requestedReviewer": (
                        {"slug": who[1:]} if who.startswith("#") else {"login": who}
                    ),
                }
                for who, as_codeowner in requests
            ]
        },
        "latestReviews": {
            "nodes": [
                {"state": state, "author": {"login": login}, "submittedAt": at}
                for login, state, at in reviews
            ]
        },
        "timelineItems": {
            "nodes": [] if ready_at is None else [{"createdAt": ready_at}]
        },
        "commits": {"nodes": [] if commit is None else [{"commit": commit}]},
    }
    if extra:
        node.update(extra)
    return node


def search_response(nodes=(), issue_count=None, viewer="me"):
    """Build the gh api graphql stdout for one search, data wrapper included.

    issue_count: override issueCount, to test truncation (> len(nodes)).
    """
    return json.dumps(
        {
            "data": {
                "viewer": {"login": viewer},
                "search": {
                    "issueCount": len(nodes) if issue_count is None else issue_count,
                    "nodes": list(nodes),
                },
            }
        }
    )


def fake_gh(responses):
    """Return a run_gh replacement that serves canned stdout per args tuple.

    responses: dict keyed on tuple(args). An unexpected call fails loudly.
    """

    def fake(args):
        key = tuple(args)
        if key not in responses:
            raise AssertionError(f"unexpected gh call: {args}")
        value = responses[key]
        if isinstance(value, Exception):
            raise value
        return value

    return fake
