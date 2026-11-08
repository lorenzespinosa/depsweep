"""pip projects: requirements.txt or a PEP 621 pyproject.toml.

The model differs from npm repos. A pip sweep upgrades the environment, not
tracked files: ranges in requirements stay as they are, the venv moves to
the newest versions those ranges admit. So there is no branch and no commit;
the final state is "applied". Verification is ``pip check`` in the venv, and
a failing verify restores the previously installed versions, best effort.

Which environment: the project's own venv (``.venv`` then ``venv``) when it
has one, else a throwaway venv in a temp directory outside the repo. With no
project venv there is no "current" version for anything, so every upgradable
requirement is reported as a fresh install and majors cannot be judged.

Safety: the venv is selected by putting its ``bin`` directory first on PATH
and invoking ``pip`` by name, the same way an activated venv works. pip is
always driven with ``-r <file>``: requirement text read from a repo never
appears inside a command line, it is written to a temp file after passing a
strict character allowlist. Every subprocess call uses the argument list
form with shell=False.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from . import gitops, semver
from .models import (
    STATE_APPLIED,
    STATE_CLEAN,
    STATE_DRY_RUN,
    STATE_ERROR,
    STATE_REVERTED,
    STATE_SKIPPED,
    Held,
    Major,
    Plan,
    RepoResult,
    Upgrade,
    VerifyResult,
)

_VENV_NAMES = (".venv", "venv")

# Just enough PEP 508 for a requirement line: a name, optional extras,
# optional parenthesized specifiers, optional environment marker.
_REQ_RE = re.compile(
    r"^([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*(\[[^\]]*\])?\s*(.*)$"
)
# The only characters a requirement may carry anywhere downstream: names,
# extras brackets, PEP 440 specifier operators and versions. URLs and
# anything exotic fail this and are held, never passed on.
_SAFE_SPEC_RE = re.compile(r"^[A-Za-z0-9._\[\]>=<~!,;*+-]+$")


def norm(name: str) -> str:
    """PEP 503 normalized name: case, dots, underscores and dashes collapse."""
    return re.sub(r"[-_.]+", "-", name).lower()


@dataclass(frozen=True)
class Requirement:
    """One declared dependency line."""

    name: str  # bare name, no extras
    extras: str  # "[security]" or ""
    spec: str  # ">=2.0,<3" or "" for a bare name
    direct: bool = False  # "name @ https://..." direct URL reference

    @property
    def install_spec(self) -> str:
        return f"{self.name}{self.extras}{self.spec}"

    @property
    def safe(self) -> bool:
        """True when the full requirement text passes the allowlist above."""
        text = self.install_spec
        return bool(text) and _SAFE_SPEC_RE.match(text) is not None

    def pin(self, version: str) -> str:
        return f"{self.name}{self.extras}=={version}"


def parse_requirement(line: str) -> Requirement | None:
    """Parse one requirements.txt or PEP 621 dependency string.

    Comments, blanks and option lines (``-r``, ``-e``, ``--index-url``...)
    return None; includes are not followed. Environment markers are dropped.
    """
    text = line.split("#", 1)[0].strip()
    if not text or text.startswith("-"):
        return None
    text = text.split(";", 1)[0].strip()
    match = _REQ_RE.match(text)
    if not match:
        return None
    name, extras, rest = match.group(1), match.group(2) or "", match.group(3).strip()
    if rest.startswith("@"):
        return Requirement(name, extras, rest, direct=True)
    spec = rest.strip("()").replace(" ", "")  # "(>= 1.2)" -> ">=1.2"
    return Requirement(name, extras, spec)


def has_pip_project(repo: Path) -> bool:
    """requirements.txt, or a pyproject.toml declaring a PEP 621 project."""
    if (repo / "requirements.txt").is_file():
        return True
    return _pyproject_dependencies(repo) is not None


def _pyproject_dependencies(repo: Path) -> list[str] | None:
    """The [project] dependencies list, [] when absent, None when the file
    is not a PEP 621 project (or unreadable/broken)."""
    path = repo / "pyproject.toml"
    if not path.is_file():
        return None
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    project = data.get("project")
    if not isinstance(project, dict):
        return None
    deps = project.get("dependencies") or []
    if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
        return []
    return deps


def read_requirements(repo: Path) -> tuple[dict[str, Requirement], str | None]:
    """Declared dependencies: normalized name -> Requirement.

    requirements.txt wins when both it and a PEP 621 pyproject.toml exist;
    it is the more explicit pinning surface. Returns ``(declared, error)``.
    """
    req_path = repo / "requirements.txt"
    if req_path.is_file():
        try:
            lines = req_path.read_text().splitlines()
        except OSError:
            return {}, "requirements.txt unreadable"
        declared = {}
        for line in lines:
            parsed = parse_requirement(line)
            if parsed is not None:
                declared[norm(parsed.name)] = parsed
        return declared, None

    deps = _pyproject_dependencies(repo)
    if deps is None:
        return {}, "no readable pip requirements"
    declared = {}
    for line in deps:
        parsed = parse_requirement(line)
        if parsed is not None:
            declared[norm(parsed.name)] = parsed
    return declared, None


def find_venv_bin(repo: Path) -> tuple[Path | None, str | None]:
    """The project's own venv bin directory (``.venv`` then ``venv``).

    Returns ``(bin_dir, error)``. A venv directory without ``bin/pip`` is an
    error, not a fall-through to a scratch venv: the project clearly means
    to manage its own environment.
    """
    for name in _VENV_NAMES:
        venv = repo / name
        if not venv.is_dir():
            continue
        pip = venv / "bin" / "pip"
        if pip.is_file():
            return venv / "bin", None
        return None, f"{name} exists but has no pip"
    return None, None


def create_scratch_venv() -> Path | None:
    """A throwaway venv in a fresh temp directory outside any repo.
    Returns its bin directory, or None when creation failed."""
    venv_dir = Path(tempfile.mkdtemp(prefix="depsweep-venv-"))
    proc = subprocess.run(
        ["python3", "-m", "venv", venv_dir],
        capture_output=True,
        text=True,
        check=False,
    )
    bin_dir = venv_dir / "bin"
    if proc.returncode != 0 or not (bin_dir / "pip").is_file():
        shutil.rmtree(venv_dir, ignore_errors=True)
        return None
    return bin_dir


def _pip(repo: Path, venv_bin: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run pip inside the venv, selected the way an activated venv works:
    its bin directory goes first on PATH and pip is invoked by name."""
    env = dict(os.environ)
    env["PATH"] = f"{venv_bin}{os.pathsep}{env.get('PATH', '')}"
    # Argument list form, never a shell string (shell is False by default).
    # Requirement text reaches pip only through "-r" temp files whose
    # content passed Requirement.safe.
    return subprocess.run(
        ["pip", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@contextmanager
def _requirement_file(text: str) -> Iterator[Path]:
    """A one-line temp requirements file, so requirement text is read by pip
    from a file depsweep wrote, never spliced into a command line."""
    fd, name = tempfile.mkstemp(prefix="depsweep-req-", suffix=".txt")
    path = Path(name)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text + "\n")
        yield path
    finally:
        path.unlink(missing_ok=True)


class ResolverError(RuntimeError):
    """pip's resolver failed or produced an unreadable report."""


class PipInventoryError(RuntimeError):
    """pip list failed or returned garbage; the environment is unknown."""


def installed_versions(repo: Path, venv_bin: Path) -> dict[str, str]:
    """What the venv has installed right now: normalized name -> version.

    Raises PipInventoryError when pip itself fails or emits unusable output:
    an unknown environment must never be treated as an empty one, or
    existing packages look like fresh installs and majors get applied.
    """
    proc = _pip(repo, venv_bin, "list", "--local", "--format", "json")
    if proc.returncode != 0:
        raise PipInventoryError(f"pip list exited {proc.returncode}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise PipInventoryError(f"pip list emitted invalid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise PipInventoryError("pip list emitted an unexpected shape")
    return {
        norm(str(item["name"])): str(item["version"])
        for item in data
        if isinstance(item, dict) and item.get("name") and item.get("version") is not None
    }


def resolve_wanted(repo: Path, venv_bin: Path, req: Requirement) -> str | None:
    """The newest version pip's resolver would install for this requirement.

    ``pip install --dry-run --ignore-installed --report -`` resolves without
    touching the venv; ``--ignore-installed`` is what makes pip report a
    version even when the current one already satisfies the range.
    """
    if not req.safe:
        return None
    with _requirement_file(req.install_spec) as reqfile:
        proc = _pip(
            repo,
            venv_bin,
            "install",
            "--dry-run",
            "--quiet",
            "--ignore-installed",
            "--report",
            "-",
            "-r",
            reqfile,
        )
    if proc.returncode != 0:
        raise ResolverError(f"resolver exited {proc.returncode} for {req.name}")
    start = proc.stdout.find("{")
    if start < 0:
        raise ResolverError(f"resolver report missing for {req.name}")
    try:
        report = json.loads(proc.stdout[start:])
    except json.JSONDecodeError as exc:
        raise ResolverError(f"resolver report unreadable for {req.name}: {exc}") from exc
    for item in report.get("install") or []:
        meta = item.get("metadata") or {} if isinstance(item, dict) else {}
        if norm(str(meta.get("name", ""))) == norm(req.name):
            version = meta.get("version")
            if isinstance(version, str) and version.strip():
                return version.strip()
    return None


def install_version(
    repo: Path, venv_bin: Path, req: Requirement, version: str
) -> subprocess.CompletedProcess[str]:
    with _requirement_file(req.pin(version)) as reqfile:
        return _pip(repo, venv_bin, "install", "--upgrade", "-r", reqfile)


def install_requirement(
    repo: Path, venv_bin: Path, req: Requirement
) -> subprocess.CompletedProcess[str]:
    with _requirement_file(req.install_spec) as reqfile:
        return _pip(repo, venv_bin, "install", "-r", reqfile)


def pip_check(repo: Path, venv_bin: Path) -> subprocess.CompletedProcess[str]:
    return _pip(repo, venv_bin, "check")


def build_plan(
    declared: dict[str, Requirement],
    current: dict[str, str],
    wanted: dict[str, str],
) -> Plan:
    """Classification for pip. ``==`` and ``===`` pins are held, direct URL
    references and off-allowlist requirements are held, wanted versions
    crossing a major are listed, and the rest upgrade in place. With no
    current version (fresh scratch venv) every upgradable requirement is a
    fresh install; majors cannot be judged and the range itself is the
    mandate."""
    upgrades: list[Upgrade] = []
    majors: list[Major] = []
    held: list[Held] = []
    for key in sorted(declared):
        req = declared[key]
        want = (wanted.get(key) or "").strip()
        cur = (current.get(key) or "").strip()
        if req.direct:
            held.append(Held(req.name, cur, want, "direct URL"))
            continue
        if not req.safe:
            held.append(Held(req.name, cur, want, "unsupported requirement"))
            continue
        if "==" in req.spec or "===" in req.spec:
            held.append(Held(req.name, cur, want, "exact pin"))
            continue
        if not want:
            continue  # the resolver offered nothing for this requirement
        try:
            want_v = semver.Version(want)
            cur_v = semver.Version(cur) if cur else None
        except ValueError:
            held.append(Held(req.name, cur, want, "unparsable version"))
            continue
        if cur_v is not None:
            if not want_v > cur_v:
                continue  # already at or past wanted
            if want_v.major != cur_v.major:
                majors.append(Major(req.name, cur, want))
                continue
        upgrades.append(Upgrade(req.name, cur, want))
    return Plan(tuple(upgrades), tuple(majors), tuple(held))


def sweep_pip_repo(repo: Path, rel_name: str, dry_run: bool = False) -> RepoResult:
    """Sweep one pip repo. Never raises; the caller has already checked the
    worktree is clean and on a real branch."""
    declared, error = read_requirements(repo)
    if error is not None:
        return RepoResult(rel_name, STATE_ERROR, manager="pip", reason=error)
    if not declared:
        return RepoResult(rel_name, STATE_CLEAN, manager="pip")

    scratch: Path | None = None  # the venv dir to delete afterwards
    venv_bin, venv_error = find_venv_bin(repo)
    if venv_error is not None:
        return RepoResult(rel_name, STATE_ERROR, manager="pip", reason=venv_error)
    if venv_bin is None:
        bin_dir = create_scratch_venv()
        if bin_dir is None:
            return RepoResult(
                rel_name, STATE_ERROR, manager="pip", reason="could not create a scratch venv"
            )
        venv_bin = bin_dir
        scratch = bin_dir.parent

    try:
        if scratch is None and gitops.ls_files(repo, *_VENV_NAMES).strip():
            return RepoResult(
                rel_name,
                STATE_SKIPPED,
                manager="pip",
                reason="venv is tracked by git; refusing to upgrade it",
            )

        try:
            current = installed_versions(repo, venv_bin)
        except PipInventoryError as exc:
            return RepoResult(
                rel_name,
                STATE_ERROR,
                manager="pip",
                reason=f"could not inventory the venv ({exc}); refusing to upgrade",
            )
        wanted = {}
        try:
            for key, req in declared.items():
                if req.direct:
                    continue  # held anyway; a URL is never resolver input
                version = resolve_wanted(repo, venv_bin, req)
                if version is not None:
                    wanted[key] = version
        except ResolverError as exc:
            return RepoResult(
                rel_name,
                STATE_ERROR,
                manager="pip",
                reason=f"dependency resolver failed ({exc})",
            )
        plan = build_plan(declared, current, wanted)
        carried = dict(upgrades=plan.upgrades, majors=plan.majors, held=plan.held)

        if dry_run:
            return RepoResult(rel_name, STATE_DRY_RUN, manager="pip", **carried)
        if not plan.upgrades:
            return RepoResult(rel_name, STATE_CLEAN, manager="pip", **carried)

        applied: list[tuple[Requirement, str]] = []  # (requirement, old version)
        failure: str | None = None
        for upgrade in plan.upgrades:
            req = declared[norm(upgrade.name)]
            old = current.get(norm(upgrade.name), "")
            proc = (
                install_version(repo, venv_bin, req, upgrade.wanted)
                if old
                else install_requirement(repo, venv_bin, req)
            )
            if proc.returncode != 0:
                failure = f"install failed: {upgrade.name}@{upgrade.wanted}"
                # a failed install may still be partially applied; it belongs
                # on the rollback list so cleanup accounts for it
                applied.append((req, old))
                break
            applied.append((req, old))

        verify: VerifyResult | None = None
        if failure is None:
            proc = pip_check(repo, venv_bin)
            verify = VerifyResult("pip check", proc.returncode == 0)
            if not verify.passed:
                failure = "verify failed: pip check"

        if failure is not None:
            rollback_ok = _restore(repo, venv_bin, applied)
            if not rollback_ok:
                return RepoResult(
                    rel_name,
                    STATE_ERROR,
                    manager="pip",
                    reason=f"{failure}; rollback incomplete, see above",
                    verify=verify,
                    **carried,
                )
            return RepoResult(
                rel_name, STATE_REVERTED, manager="pip", reason=failure, verify=verify, **carried
            )
        return RepoResult(
            rel_name, STATE_APPLIED, manager="pip", verify=verify, **carried
        )
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)


def _restore(repo: Path, venv_bin: Path, applied: list[tuple[Requirement, str]]) -> bool:
    """Put the environment back the way it was found. Fresh installs (no
    previous version) are uninstalled; previous versions are reinstalled.
    Returns True only when every restore step succeeded; the caller reports
    an incomplete rollback as an error instead of a clean revert."""
    ok = True
    for req, old in applied:
        if old:
            if install_version(repo, venv_bin, req, old).returncode != 0:
                print(f"depsweep: could not restore {req.name}=={old}", file=sys.stderr)
                ok = False
        else:
            if _pip(repo, venv_bin, "uninstall", "-y", req.name).returncode != 0:
                print(f"depsweep: could not uninstall {req.name}", file=sys.stderr)
                ok = False
    return ok
