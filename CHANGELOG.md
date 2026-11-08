# Changelog

## 0.3.0 - 2026-11-07

- Bisection when the gate fails. A failing batch is no longer reverted
  wholesale: halves of it are applied on their own to a clean tree and
  gated, so the single breaking package is found and named as `reverted:
  NAME broke tests`, held back at its old version, and the rest of the
  batch lands after a passing confirming run (the commit message names what
  was held back). With two or three packages this is one-by-one, with more
  it is halving. Bounded: at most ceil(log2(N)) + 2 gate runs after the
  failing batch run, then the whole batch is reverted and the reason says
  so. That whole-batch revert is also what happens when no single package
  is guilty: an interaction between two upgrades, or a gate that was
  already red before the sweep, lands nothing rather than blaming an
  innocent package. Install failures are not bisected (the batch never
  fully applied). npm and pnpm only; pip keeps its whole-batch restore.
- Worktree isolation, on by default. Each npm and pnpm repo is swept in a
  temporary git worktree on the sweep branch, so the repo itself never
  switches branches and never gets dirty; the branch and its commit live in
  the repo's refs and survive the worktree, which is removed on every exit
  path including crashes. Worktrees sit in private temp directories, one
  per repo, so parallel sweeps stay independent. If git cannot commit in
  the worktree (usually missing `user.name`/`user.email`), nothing is kept
  and the repo reports an error saying so. `--no-worktrees` restores the
  pre-0.3.0 in-place behaviour. pip repos are swept in place either way:
  their venv lives in the repo, and a throwaway worktree would upgrade a
  copy of it and throw that away.
- `--html PATH` writes a single self-contained HTML report: summary table,
  per-repo detail, and the full upgrade lists, with inline CSS only and no
  scripts or external assets. A 50-repo sweep stays under 15KB. The parent
  directory must exist (usage error otherwise).
- The `--json` report carries a `reverted` list per repo with the upgrades
  bisection held back, next to the existing `upgraded` list.
- Internal: worktree plumbing (`worktree add/remove/prune`, `reset-to`,
  `head-sha`) lives in gitops with the rest of the git calls; the narrowing
  logic lives in its own module with its own tests.

## 0.2.0 - 2026-11-07

- pnpm support. A repo with `pnpm-lock.yaml` next to its `package.json` is
  swept with pnpm: `pnpm outdated --format json` for the plan (exit code 1
  counts as data, same as npm) and `pnpm add pkg@version` for the upgrade.
  Both JSON shapes pnpm has printed over its releases are understood.
- pip support. Repos with a `requirements.txt` or a PEP 621 `pyproject.toml`
  are swept in the project's own venv (`.venv`, then `venv`), or in a fresh
  scratch venv created outside the repo when there is none. Wanted versions
  come from pip's own resolver (`pip install --dry-run --report`), so
  specifier semantics stay pip's, not ours. `==` and `===` pins are held.
  The venv is the thing upgraded, so there is no branch: the state is
  `applied`, the gate is `pip check`, and a failed gate puts the previously
  installed versions back.
- `--include PATTERN` and `--exclude PATTERN` glob filters on the repo path
  relative to the root. Repeatable; exclude wins.
- `--jobs N` sweeps repos in parallel, default 4. The summary table keeps
  discovery order whatever the completion order was.
- The JSON report now names the manager (`npm`, `pnpm`, `pip`) for each repo
  and counts `applied` in its totals.
- Version comparison understands PEP 440 prerelease and post-release
  suffixes (`1.2.3rc1`, `1.2.3.post2`) alongside npm style.

## 0.1.0 - 2026-11-06

- First release. npm repos only, said so honestly. One pass over many local
  git repos: dirty repos skipped, sweep branch per repo, `npm outdated`
  driven in-range upgrades, exact pins held, new majors listed but never
  installed, test/build/install as the gate, automatic revert on failure,
  fixed-column table plus `--json`, documented exit codes.
