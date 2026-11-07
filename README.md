# depsweep

A command line tool that upgrades in-range dependencies across many local git
repos in one pass, a few repos at a time. Each repo's own test script (or
build) is the gate for npm and pnpm repos, and any repo that fails the gate is
put back exactly as it was. It was built after a real sweep day on 2026-11-06
when more than 20 repos were upgraded by hand, to make that day repeatable in
minutes instead of hours.

npm, pnpm and pip repos are supported. Python 3.11 or newer, standard library
only at runtime, pytest for the test suite.

## Install

    pip install .

For development:

    pip install -e .[dev]
    pytest

## Use it with your AI

The repo ships a ready-made skill file (`skill/SKILL.md`). Copy that folder
into your AI assistant's skills directory and it will know when and how to
run depsweep, including the safety rules.

## Quick start

    depsweep ~/code --dry-run     # see what would happen, change nothing
    depsweep ~/code               # do the sweep
    depsweep ~/code --include "apps/*" --exclude "*legacy*"   # sweep a slice
    depsweep ~/code --jobs 8      # more repos in parallel

The full option set:

    depsweep <root> [--dry-run] [--branch NAME] [--json] [--max-depth N]
                      [--include PATTERN] [--exclude PATTERN] [--jobs N]

| Option | Meaning |
| --- | --- |
| `--dry-run` | Print what would happen. Touches nothing. |
| `--branch NAME` | Sweep branch name. Default `dep-sweep-YYYYMMDD`. |
| `--json` | Print a machine-readable report instead of the table. |
| `--max-depth N` | Only scan N directory levels below the root. |
| `--include PATTERN` | Only sweep repos whose path relative to the root matches this glob. Repeatable. `*` also matches across `/`. |
| `--exclude PATTERN` | Skip matching repos. Repeatable, wins over `--include`. |
| `--jobs N` | How many repos to sweep in parallel. Default 4. The table keeps discovery order. |

## What counts as a repo

A directory under the root that has `.git` plus one of:

- `pnpm-lock.yaml` next to a `package.json`: swept with pnpm.
- `package.json`: swept with npm.
- `requirements.txt` or a PEP 621 `pyproject.toml` (a `[project]` table):
  swept with pip.

One manager per repo. A repo carrying markers for more than one is swept by
the first in the list above and the others are ignored for that pass.

### npm and pnpm repos

depsweep reads `npm outdated --json` or `pnpm outdated --format json`, then
upgrades the packages the package.json ranges already admit (caret and tilde
ranges, same major) on a new sweep branch, one package at a time. Exit code 1
from either command just means something is outdated; only missing or invalid
JSON counts as an error. Upgrades go through `npm install pkg@version` or
`pnpm add pkg@version`.

### pip repos

A pip sweep upgrades the environment, not tracked files. Ranges in
requirements stay as they are; the venv moves to the newest versions those
ranges admit.

- The venv: the project's own `.venv`, then `venv`. With neither, depsweep
  creates a scratch venv in a temp directory outside the repo and removes it
  afterwards.
- Wanted versions come from pip's own resolver, asked as
  `pip install --dry-run --ignore-installed --report`, so `~=`, `!=` and
  compound ranges keep pip's semantics. Only declared direct dependencies are
  touched, one at a time.
- `==` and `===` pins are held and reported, never upgraded. Direct URL
  requirements are held too.
- The gate is `pip check` in the venv. A failed gate puts the previously
  installed versions back, best effort, and the repo reports `reverted`.
- A successful sweep reports `applied` rather than `branch left`: no tracked
  file changed, so there is nothing to commit. The table shows a fresh
  install (scratch venv, nothing installed before) as `name new->1.4.0`.

## The output

The human table has a fixed column order:

    repo | upgraded | majors | held | verify | state

`upgraded` shows each move as `name 1.2.3->1.4.0`. `majors` lists new major
versions depsweep found but did not install. `held` lists exact pins and
other ranges it will not touch, with the reason. `verify` names the gate that
ran (test, build, install, or pip check) and whether it passed. `state` says
how the repo ended: branch left, applied, reverted, skipped, clean, or error.

With `--json` the same fields come back as a report object, one entry per
repo plus its manager (`npm`, `pnpm`, `pip`), ready for scripts.

## Safety rules

- Never pushes. depsweep only runs local git commands.
- Skips any repo with a dirty worktree, before doing anything else.
- Never commits to the branch you are on. npm and pnpm changes land on a new
  sweep branch; the original branch is left as it was. pip sweeps change the
  venv, never tracked files.
- Runs the repo's own `test` script to verify npm and pnpm repos. No test
  script means `build`. No build means a clean install must exit 0. For pip
  repos the gate is `pip check` in the venv.
- If verify fails, depsweep puts the repo back: original branch restored,
  sweep branch deleted, and pip versions reinstalled to what they were.
- Exact version pins are held and reported, never upgraded. For npm that is
  an exact version in package.json; for pip an `==` or `===` requirement.
- New major versions are listed, never installed.
- A pip repo whose venv directory is tracked by git is skipped: upgrading it
  would commit your environment.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | The sweep ran. Skipped repos do not change this. |
| 1 | At least one repo failed the gate and was reverted, or errored. |
| 2 | Usage error, such as a bad root path or a missing argument. |

## Roadmap

- yarn lockfile awareness.
- Poetry and pip-tools lockfile styles for pip repos.
- PEP 621 `optional-dependencies`, and following `-r` includes in
  requirements files.
- Treating 0.x minor bumps (0.1.0 to 0.2.0) as major for open ranges.
- Per-package bisection when the gate fails, instead of one revert per repo.
- Optional opening of one pull request per repo (off by default, never
  automatic).

## License

MIT, see [LICENSE](LICENSE).
