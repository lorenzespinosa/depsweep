# Research notes for v0.2.0

Sources checked (public registries and docs, November 2026):
npm-check-updates README, Renovate "How Renovate works", dependabot-core README,
pnpm docs (outdated, install, add) plus the pnpm 12.18.0 tarball from
registry.npmjs.org, pip docs (install, dry-run report, upgrade strategy), and
the PyPI JSON API for a sample package.

## Findings

1. npm-check-updates `--doctor` installs upgrades one at a time and runs tests
   after each, reverting the breaking ones. depsweep already gates on the
   repo's own test script, but per repo, not per package. Adopt nothing now;
   per-package bisection goes on the roadmap (fewer subprocesses, simpler
   state machine, and one revert already matches how a person would fix it).

2. ncu pre-selects patch and minor upgrades and treats 0.x minor bumps (say
   0.1.0 to 0.2.0) as risky, only installing them on explicit request.
   depsweep keeps majors listed, never installed, which is the same line.
   The 0.x caution is a behavior change not in scope; noted on the roadmap.

3. Renovate's pipeline is: extract deps, look up the registry, apply grouping,
   push one branch per update (or group). depsweep already mirrors this
   locally: one sweep branch per repo holding all of that repo's in-range
   upgrades, which is what Renovate calls a grouped update. No change needed.

4. Renovate puts major updates on their own branches because majors break.
   depsweep is stricter: majors are listed and never installed, so the
   question never comes up. Confirmed the current rule is sound.

5. dependabot's core promise is the latest version "that's resolvable given
   a project's other dependencies", then updated manifests and lockfiles. For
   depsweep this means wanted versions must come from the package manager's
   own resolver output (npm or pnpm outdated), never from a raw registry
   "latest" field. Already true for npm; now also the design rule for pip.

6. pip can resolve without installing: `pip install --dry-run --quiet
   --ignore-installed --report - <requirement>` prints a JSON report of what
   it would install. Adopted as the pip "wanted" source. It gets specifier
   semantics right (`~=`, `!=`, compound ranges) that we would otherwise have
   to reimplement. `--ignore-installed` is required or pip reports nothing
   for already-satisfied requirements.

7. pip's default `--upgrade-strategy only-if-needed` upgrades transitive deps
   only when forced. depsweep is narrower still: only declared direct deps,
   one at a time, transitive deps left to the resolver. Consistent; noted so
   the README can say it plainly.

8. pnpm: `pnpm outdated --format json` exists and, like npm, exit code 1 just
   means "something is outdated". `pnpm install` takes no package arguments,
   so a single-package upgrade is `pnpm add pkg@version` (docs for install
   and add). The exact JSON shape varies across pnpm releases (older ones
   printed an array of objects, newer ones an object keyed by package name;
   the shipped pnpm 12 tarball carries a native binary, so the shape could
   not be verified from source). Adopted: parse both shapes defensively.

9. The PyPI JSON API (`pypi.org/pypi/<pkg>/json`) exposes every release and
   would let depsweep compute "latest" without pip. Skipped: it re-implements
   version filtering that pip's resolver report already gets right, and it
   has no notion of the project's declared specifiers.

10. dependabot and Renovate both end in one pull request per update and keep
    per-dependency detail visible in their output. depsweep's fixed table
    (repo, upgraded, majors, held, verify, state) plus `--json` already gives
    that at repo granularity. Pull request opening stays on the roadmap, off
    by default; depsweep never pushes.

## What v0.2.0 adopts

- pnpm support via `pnpm outdated --format json` (both JSON shapes) and
  `pnpm add pkg@wanted`, detected by `pnpm-lock.yaml`.
- pip support driven by pip's own dry-run resolver report; `==` and `===`
  pins held; the venv is the thing upgraded, with `pip check` as the gate.
- Nothing from findings 1, 2, 9, 10 beyond roadmap notes.

## Note for v0.3.0

Finding 1 came off the roadmap for 0.3.0, in a bounded form: instead of
running the gate once per package like ncu's doctor (N runs), the failing
batch is bisected by halves (ceil(log2(N)) + 2 gate runs at most, then a
whole-batch revert). git worktrees, also new in 0.3.0, make each probe
cheap: resetting a throwaway worktree to the base commit leaves the repo
untouched the whole time.
