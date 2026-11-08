# depsweep

A command line tool that upgrades in-range dependencies across many local git
repos in one pass, a few repos at a time. Each repo's own test script (or
build) is the gate for npm and pnpm repos, and any repo that fails the gate is
put back exactly as it was. When a batch fails, the breaking package is
found by bisection and named, and the rest of the batch still lands. The
sweep runs in a temporary git worktree, so the repo itself never switches
branches. It was built after a real sweep day on 2026-11-06 when more than
20 repos were upgraded by hand, to make that day repeatable in minutes
instead of hours.

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
    depsweep ~/code --html out.html   # also write a self-contained HTML report

The full option set:

    depsweep <root> [--dry-run] [--branch NAME] [--json] [--html PATH]
                      [--no-worktrees] [--max-depth N]
                      [--include PATTERN] [--exclude PATTERN] [--jobs N]

| Option | Meaning |
| --- | --- |
| `--dry-run` | Print what would happen. Touches nothing. |
| `--branch NAME` | Sweep branch name. Default `dep-sweep-YYYYMMDD`. |
| `--json` | Print a machine-readable report instead of the table. |
| `--html PATH` | Also write a single self-contained HTML report there: inline CSS, no scripts, no external assets, one file you can mail or archive. |
| `--no-worktrees` | Sweep each repo in place by switching branches in the repo itself, the pre-0.3.0 behaviour. |
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
ranges, same major), one package at a time, on a new sweep branch. Exit code
1 from either command just means something is outdated; only missing or
invalid JSON counts as an error. Upgrades go through `npm install
pkg@version` or `pnpm add pkg@version`.

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
- Worktrees do not apply here: pip sweeps always run in the repo itself,
  because the venv they upgrade lives there. A throwaway worktree would
  upgrade a copy of the venv and throw it away.

## Worktree isolation

By default each npm and pnpm repo is swept in a temporary git worktree on a
new sweep branch:

- The repo you point depsweep at never switches branches and never gets
  dirty. It stays exactly as it was, on the branch it was on.
- The sweep branch and its commit live in the repo's own refs, so they are
  there after the sweep: `git log dep-sweep-YYYYMMDD` shows the upgrade.
- The worktree is removed on every exit path, success, failure, or crash.
  Worktrees live in private temp directories, one per repo, so parallel
  sweeps cannot step on each other.
- If git cannot commit in the worktree (usually a missing `user.name` or
  `user.email`), nothing is kept and the repo reports an error saying so.
  With `--no-worktrees` the old behaviour applies: changes are left
  uncommitted in the repo on the sweep branch instead.

Use `--no-worktrees` to get the pre-0.3.0 behaviour: depsweep switches the
repo itself to the sweep branch and switches back on failure.

## When the gate fails: bisection

If the gate fails after a batch of upgrades, depsweep does not give up on
the whole batch. It bisects:

1. The batch is known to fail. Halves of it are applied, on their own, to a
   clean tree, and the gate runs again: a failing half becomes the suspect
   set, a passing half clears its packages.
2. A single suspect is then convicted only if it fails alone. With two or
   three packages in the batch this is one-by-one; with more, halving.
3. The culprit is reported as `reverted: NAME broke tests`, held back at its
   old version, and the rest of the batch is applied and gated once more to
   prove it. Only then does it land, with a commit message that names what
   was held back.
4. If no single package is guilty (an interaction, or a gate that was
   already red before the sweep), the whole batch is reverted and the reason
   says exactly that.

The cost is bounded: after the failing batch run, bisection spends at most
ceil(log2(N)) + 2 gate runs for N upgrades, then stops and reverts the whole
batch. Install failures are not bisected; the batch never fully applied, so
the failing install is named and the repo is put back.

Repos where bisection landed the rest are not failures: the run exits 0 and
the table shows `branch left` with the culprit named in the state column.
The `--json` report and the HTML report carry a `reverted` list per repo
with the held-back upgrades.

## The output

The human table has a fixed column order:

    repo | upgraded | majors | held | verify | state

`upgraded` shows each move as `name 1.2.3->1.4.0`. `majors` lists new major
versions depsweep found but did not install. `held` lists exact pins and
other ranges it will not touch, with the reason. `verify` names the gate that
ran (test, build, install, or pip check) and whether it passed. `state` says
how the repo ended: branch left, applied, reverted, skipped, clean, or error.

With `--json` the same fields come back as a report object, one entry per
repo plus its manager (`npm`, `pnpm`, `pip`) and its `reverted` list, ready
for scripts.

With `--html PATH` the same data lands in one self-contained HTML file:
summary table, per-repo detail, and the full upgrade lists. Inline CSS only,
no scripts, no external assets. A 50-repo sweep stays under 15KB, so the
report is cheap to keep next to a run log.

## Safety rules

- Never pushes. depsweep only runs local git commands.
- Skips any repo with a dirty worktree, before doing anything else.
- Never commits to the branch you are on. npm and pnpm sweeps run in a
  temporary worktree on a new sweep branch; the repo you pointed at stays
  put, and the sweep branch is left in it when the gate passes. pip sweeps
  change the venv, never tracked files.
- Runs the repo's own `test` script to verify npm and pnpm repos. No test
  script means `build`. No build means a clean install must exit 0. For pip
  repos the gate is `pip check` in the venv.
- If verify fails, depsweep bisects for the single breaking package, holds
  it back, and lands the rest only after a passing confirming run. When no
  single package is guilty, the whole batch is reverted and the reason says
  so. pip failures restore the previously installed versions.
- Exact version pins are held and reported, never upgraded. For npm that is
  an exact version in package.json; for pip an `==` or `===` requirement.
- New major versions are listed, never installed.
- A pip repo whose venv directory is tracked by git is skipped: upgrading it
  would commit your environment.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | The sweep ran. Skipped repos, and repos where bisection landed part of the batch, do not change this. |
| 1 | At least one repo failed the gate and was reverted, or errored. Also used when the HTML report could not be written. |
| 2 | Usage error, such as a bad root path, a missing `--html` directory, or a missing argument. |

## Roadmap

- Bisection for pip repos (a failing `pip check` currently reverts the whole
  batch there; the gate is rarely batch-shaped).
- yarn lockfile awareness.
- Poetry and pip-tools lockfile styles for pip repos.
- PEP 621 `optional-dependencies`, and following `-r` includes in
  requirements files.
- Treating 0.x minor bumps (0.1.0 to 0.2.0) as major for open ranges.
- Optional opening of one pull request per repo (off by default, never
  automatic).

## License

MIT, see [LICENSE](LICENSE).

## Limitations

- Python environment markers (`pkg>=1; python_version < "3.12"`) are not evaluated: the marker is dropped and the requirement is treated as unconditional. Use `--exclude` for requirements that must only apply on other platforms or Python versions.
