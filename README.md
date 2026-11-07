# depsweep

A command line tool that upgrades in-range npm dependencies across many local
git repos in one pass, one repo at a time. Each repo's own test script (or
build) is the gate, and any repo that fails the gate is put back exactly as it
was. It was built after a real sweep day on 2026-11-06 when more than 20 repos
were upgraded by hand, to make that day repeatable in minutes instead of
hours.

npm repos only for now, and it says so honestly: pip support is on the
roadmap. Python 3.11 or newer, standard library only at runtime, pytest for
the test suite.

## Install

    pip install .

For development:

    pip install -e .[dev]
    pytest

## Quick start

    depsweep ~/code --dry-run     # see what would happen, change nothing
    depsweep ~/code               # do the sweep

A repo is any directory under the root that has both `.git` and
`package.json`. For each one, depsweep reads `npm outdated --json`, upgrades
the packages your package.json ranges already admit (caret and tilde ranges,
same major), and lists everything it refuses to touch. The full option set:

    depsweep <root> [--dry-run] [--branch NAME] [--json] [--max-depth N]

| Option | Meaning |
| --- | --- |
| `--dry-run` | Print what would happen. Touches nothing. |
| `--branch NAME` | Sweep branch name. Default `dep-sweep-YYYYMMDD`. |
| `--json` | Print a machine-readable report instead of the table. |
| `--max-depth N` | Only scan N directory levels below the root. |

The human table has a fixed column order:

    repo | upgraded | majors | held | verify | state

`upgraded` shows each move as `name 1.2.3->1.4.0`. `majors` lists new major
versions depsweep found but did not install. `held` lists exact pins and
other ranges it will not touch, with the reason. `verify` names the gate that
ran (test, build, or install) and whether it passed. `state` says how the
repo ended: branch left, reverted, skipped, clean, or error.

With `--json` the same fields come back as a report object, one entry per
repo, ready for scripts.

## Safety rules

- Never pushes. depsweep only runs local git commands.
- Skips any repo with a dirty worktree, before doing anything else.
- Never commits to the branch you are on. All changes land on a new sweep
  branch; the original branch is left as it was.
- Runs the repo's own `test` script to verify. No test script means `build`.
  No build means a clean `npm install` must exit 0.
- If verify fails, depsweep switches back to the original branch and deletes
  the sweep branch. The repo ends where it started.
- Exact version pins are held and reported, never upgraded.
- New major versions are listed, never installed.
- `npm outdated` exit code 1 just means something is outdated; only missing
  or invalid JSON counts as an error.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | The sweep ran. Skipped repos do not change this. |
| 1 | At least one repo failed the gate and was reverted, or errored. |
| 2 | Usage error, such as a bad root path or a missing argument. |

## Roadmap

- pip support: `requirements.txt` and `pyproject.toml` repos.
- pnpm and yarn lockfile awareness.
- Optional opening of one pull request per repo (off by default, never
  automatic).

## License

MIT, see [LICENSE](LICENSE).
