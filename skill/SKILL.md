---
name: depsweep
description: "Use when the user wants dependency upgrades across many repos."
---

# depsweep: safe dependency sweeps across many git repos

Upgrades in-range minor and patch dependencies across many local git
repositories in one pass. Verifies each repo with its own test script,
bisects a failing batch down to the one package that broke it, holds that
package back, and lands the rest. Sweeps run in temporary git worktrees, so
the repos never switch branches. Reverts automatically when nothing can
land. Never pushes. Skips dirty repos. Lists major versions instead of
installing them. Supports npm, pnpm and pip repos.

## When to use

- The user asks to "update dependencies in all my repos" or "sweep my
  projects folder".
- Routine maintenance pass before bigger upgrade work.
- The user wants a report of what is outdated across many repos.

## Install

    pip install git+https://github.com/lorenzespinosa/depsweep.git

## How to run

    depsweep ~/projects --dry-run     # plan only, changes nothing
    depsweep ~/projects               # sweep, verify, auto-revert
    depsweep ~/projects --json        # machine-readable report
    depsweep ~/projects --html r.html # self-contained HTML report, one file

Always run `--dry-run` first and read the plan. Then run for real. After the
run, report the summary table to the user: repos upgraded, repos skipped
with reasons, culprits held back by bisection, majors waiting.

- Exit code 1 means at least one repo failed and was auto-reverted. Read the
  table before retrying; an identical retry usually fails the same way.
- A repo where bisection held one package back but landed the rest is not a
  failure: the state column says `reverted: NAME broke tests` and the JSON
  and HTML reports carry a `reverted` list.
- `--include` and `--exclude` take glob patterns to scope the sweep.
- `--jobs N` runs repos in parallel (default 4).
- `--no-worktrees` switches to the old in-place behaviour; the default
  sweeps each npm/pnpm repo in a temporary git worktree.
- Point it at the smallest directory that covers the goal.

## Install this skill for your agent

Copy this folder into your agent's skills directory and reload. The file is
plain markdown with a small frontmatter header; any agent that loads skills
from a folder can use it.
