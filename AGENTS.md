# Agent instructions for depsweep

Two audiences: agents USING depsweep as a tool, and agents WORKING ON this
repository. Plain rules, no ceremony.

## Using depsweep as a tool

Run it against a directory of git repositories:

    depsweep ~/projects --dry-run     # plan only, changes nothing
    depsweep ~/projects               # sweep, verify, auto-revert
    depsweep ~/projects --json        # machine-readable report

Per repository it will: check the tree is clean (dirty repos are skipped and
reported), create a sweep branch for npm and pnpm repos, upgrade only
in-range minor and patch versions, run that repo's own test script, and
delete the branch if verification fails. pip repos (requirements.txt or PEP
621 pyproject.toml) are upgraded in their own venv. Exact pins are held.
Majors are listed, never installed. Nothing is ever pushed anywhere.

Rules for agents driving it:

1. Always run `--dry-run` first, read the plan, then run for real.
2. Exit code 1 means at least one repo failed and was auto-reverted. Read the
   summary table before retrying; a second identical attempt usually fails
   the same way.
3. Point it at the smallest directory that covers the goal. Dirty repos are
   skipped safely, but keep the blast radius small anyway.
4. Use `--include` and `--exclude` to scope by glob, `--jobs N` for parallel
   repos (default 4).
5. Report to your user: repos upgraded, repos skipped with reasons, majors
   waiting. That table is the deliverable, not the exit code.

## Working on this repository

- Python 3.11+, standard library only at runtime. pytest for tests.
- Tests are 100% offline: npm, pnpm and pip are stubbed. Never add a test
  that needs the network or a real registry.
- Keep user-facing docs plain and short. No em dashes.
- Run the full suite before every commit: `python -m pytest`
- One behavior change per commit, described plainly in the message.
