# pr-radar

Terminal dashboard for pending GitHub pull requests: the PRs you opened, and the PRs that wait for your review.

## Commands

- `uv run pytest` — full test suite. Tests never touch the network.
- `uv run pr-radar` — run against live GitHub (needs an authenticated `gh`).
- `uv build` — build sdist + wheel.
- `uvx --from actionlint-py actionlint .github/workflows/*.yml` — lint workflows.

## Layout

- `src/pr_radar/__init__.py` — the whole implementation. Fetch layer (subprocess `gh` calls), pure domain functions (`classify`, `reviewer_states`, `normalize_pr`), presentation (`build_output`, `build_json`), CLI (`main`).
- Pipeline: fetch → classify (raw GraphQL nodes: selection and ordering) → `normalize_pr` (complete, untruncated records) → present. Truncation, caps, glyphs, colours, and width fitting live only in the rendering layer; JSON dumps records verbatim.
- `tests/helpers.py` — `make_pr` node builder and `fake_gh` runner (keyed on the full `gh` args tuple; unexpected calls fail loudly). Extend it additively; do not change existing behavior.
- `tests/test_core.py` (domain + rendering), `tests/test_cli.py` (argparse, wiring, end-to-end with the fake runner).

## Design decisions

- All network I/O goes through the `gh` CLI — it supplies auth and retries. `run_gh` is the only subprocess seam; tests monkeypatch it.
- Data comes from `gh api graphql` with a `search(type: ISSUE)` query, not `gh search prs --json` — the JSON export contains no review data.
- Four searches run in parallel threads. A single aliased GraphQL document was measured slower (4.6 s vs 3.2 s); do not "simplify" back to one request without re-measuring.
- A separate `team-review-requested:` search exists because `review-requested:@me` does not match team-routed requests.
- Search strings end in `sort:created-asc` so the 100-result cap drops the newest (least-waiting) PRs, never the longest-waiting head.
- One failed search degrades its section (header gets an `(incomplete: ...)` suffix); the run fails only when every search fails.
- Python ≥ 3.11: `datetime.fromisoformat` accepts the API's trailing `Z` only from 3.11.
- Dependencies: runtime was stdlib-only while the tool was run from a checkout. The package now ships via PyPI, so reasonable runtime dependencies are acceptable when they pay for themselves.
- WAITING is PR-level (time since draft→ready), also the Review section's sort key. The re-review filter compares the viewer's last review to the last commit's `committedDate`, which is author-controlled — a known, documented limitation.

## GitHub API facts (verified against the live API)

- The response envelope is `{"data": {"viewer": ..., "search": ...}}`; fake responses in tests must include the `data` wrapper.
- Repeating a search qualifier ORs for `author:` and `team-review-requested:`; this is per-qualifier behavior (`label:` ANDs). Verify before relying on it for a new qualifier type.
- `asCodeOwner` exists only on review-request nodes; GitHub deletes the request node once the person reviews, so the code-owner mark can only follow a pending mark.
- `latestReviews` includes COMMENTED and DISMISSED, excludes only PENDING.
- `statusCheckRollup` is null for repos with no checks.

## Releases

- release-please drives releases from conventional commit messages; keep the `type: subject` format (`feat:`/`fix:` bump the version, `docs:`/`chore:` do not).
- Merging the release PR tags, builds, and publishes to PyPI via trusted publishing (OIDC, no secrets).
- release-please bumps `pyproject.toml` but not `uv.lock` — never add `uv sync --locked` to CI.
- `astral-sh/setup-uv` has no moving major tag; pin the full version (`@v10.0.1`). `actions/checkout` and `release-please-action` do have major aliases.

## Prose style

Follow ASD-STE100 in docs, comments, and commit messages: short sentences, active voice, one instruction per sentence.
