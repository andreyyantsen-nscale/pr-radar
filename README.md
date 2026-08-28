# pr-radar

Show your pending GitHub pull requests in one table: the PRs you opened, and the PRs that wait for your review.

## Requirements

- Python ≥ 3.11. The runtime uses only the standard library.
- [GitHub CLI](https://cli.github.com/) (`gh`), authenticated. All network I/O goes through `gh`.

## Install

```sh
uv tool install -e .
```

Or run it in place: `uv run pr-radar`.

## Usage

```sh
pr-radar [--orgs a,b | --all] [--highlight-reviewers u1,u2] [--detailed]
```

- Default scope: the orgs you belong to. `--orgs a,b` overrides the list. `--all` searches all of github.com.
- `--highlight-reviewers u1,u2` adds one column per login. The cell shows that person's review state on each PR.
- `--detailed` prints each PR as a multi-line block instead of a table row: full title, uncapped reviewer list, CI status, branches, and change size. With `--highlight-reviewers`, an extra line names the highlighted people on each PR they touch.

Each PR cell is a clickable hyperlink in terminals that support OSC 8 (iTerm2 and others). Piped output contains no escape bytes.

Marks: `✔` approved, `✗` changes requested, `●` commented, `·` review pending, `*` requested as code owner.

## Semantics and limits

- WAITING is PR-level: the time since the PR left draft state (or since creation). It is not the time since you were asked to review. The "waiting for my review" section sorts by it, longest first.
- A PR you reviewed returns to the list when the last commit is newer than your latest review. The commit date is author-controlled: a cherry-picked or rebased older commit can hide a PR that needs a new review.
- Each search reads at most 100 PRs (oldest first) and 50 reviewers per PR. The tool reports truncation and does not paginate.
