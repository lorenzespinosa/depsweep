# Changelog

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
