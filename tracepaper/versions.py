"""Which version of Tracepaper a project uses, and whether a newer one is out.

Tracepaper runs from one checkout of its repository, which every project on the computer
uses (README.md). A project records the version it last used, the checkout's commit, in
.tracepaper-version at its root. `tracepaper check`, run by a Claude Code SessionStart
hook, compares that record with the checkout and the checkout with GitHub, and says what
it finds, so that the session can tell the person and offer to migrate: a newer version
on GitHub can wait, but a checkout that has moved past the project's version already is
the project's Tracepaper now, and the project has to be migrated to it. `tracepaper adopt`
writes the record once that is done, or notes that the person keeps their version.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

RECORD = ".tracepaper-version"
CHECKOUT = Path(__file__).resolve().parent.parent  # the Tracepaper repository this runs from
BRANCH = "origin/main"
MAX_LISTED = 30  # commits listed in a report; the session can ask git for the rest
# How a project shows it uses Tracepaper, when it has no record yet.
USE = r"(import|from) tracepaper|tracepaper\.(sheet|model|views)|tracepaper (view|render|inspect)"


def git(*args: str, cwd: Optional[Path] = None, timeout: float = 10) -> Optional[str]:
    """git's output, stripped, or None if it failed, in cwd or the checkout. It never asks for a password."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0",
           "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=5"}
    try:
        r = subprocess.run(["git", *args], cwd=cwd or CHECKOUT, capture_output=True, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def project_root(path: Path) -> Path:
    """The project a path is in: its git repository's top, or the path itself."""
    top = git("rev-parse", "--show-toplevel", cwd=path)
    return Path(top) if top else path.resolve()


def read_record(project: Path) -> dict:
    """The project's record: {"version": commit, "skipped": commit}, either missing."""
    try:
        data = json.loads((project / RECORD).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_record(project: Path, record: dict) -> None:
    (project / RECORD).write_text(json.dumps(record, indent=2) + "\n")


SCANNED = ("*.py", "*.md", "*.toml", "Makefile", "*.sh")
MAX_SCANNED = 2000  # files looked through in a folder that isn't a git repository


def uses_tracepaper(project: Path) -> bool:
    if (project / RECORD).exists():
        return True
    if git("rev-parse", "--is-inside-work-tree", cwd=project) == "true":
        return git("grep", "-q", "-I", "-E", USE, "--", *SCANNED, cwd=project) is not None
    use, seen = re.compile(USE), 0
    for root, dirs, files in os.walk(project):  # not a repository: its own files, a few levels down
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "venv", "__pycache__")
                   and len(Path(root, d).relative_to(project).parts) < 4]
        for f in files:
            if not any(Path(f).match(g) for g in SCANNED):
                continue
            seen += 1
            if seen > MAX_SCANNED:
                return False
            try:
                if use.search(Path(root, f).read_text(errors="ignore")):
                    return True
            except OSError:
                pass
    return False


def _short(commit: str) -> str:
    return git("log", "-1", "--format=%h (%cs)", commit) or commit[:7]


def _log(a: str, b: str) -> list:
    """The commits from a to b, oldest first, as "hash subject" lines."""
    out = git("log", "--reverse", "--format=%h %s", f"{a}..{b}")
    return out.splitlines() if out else []


def _listing(commits: list) -> str:
    shown = commits[-MAX_LISTED:]
    more = f"  ... and {len(commits) - len(shown)} earlier ones\n" if len(commits) > len(shown) else ""
    return more + "".join(f"  {c}\n" for c in shown)


def _is_ancestor(a: str, b: str) -> bool:
    return git("merge-base", "--is-ancestor", a, b) is not None


def stale_viewers(project: Path) -> list:
    """Running `tracepaper view`s in the project that started before the checkout's server
    code last changed, as "pid (started ...)" strings. Linux only: elsewhere, none."""
    changed = git("log", "-1", "--format=%ct", "--", "tracepaper", ":!tracepaper/viewer")
    out = []
    try:
        ps = subprocess.run(["ps", "-eo", "pid=,etimes=,args="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return out
    for line in ps.splitlines():
        m = re.match(r"\s*(\d+)\s+(\d+)\s+(.*)", line)
        if not m or not re.search(r"tracepaper(\.cli)?\s+view\b|-m tracepaper view\b", m[3]):
            continue
        try:
            cwd = Path(os.readlink(f"/proc/{m[1]}/cwd"))
        except OSError:
            continue
        started = time.time() - int(m[2])
        if (cwd == project or project in cwd.parents) and changed and started < int(changed):
            out.append(f"{m[1]} (started {time.strftime('%Y-%m-%d %H:%M', time.localtime(started))})")
    return out


def check(path: Path, fetch: bool = True) -> str:
    """What a session in the project at path should know about its Tracepaper, as
    instructions for it, or "" if nothing: for the SessionStart hook."""
    project = project_root(path)
    if project == CHECKOUT or CHECKOUT in project.parents or not uses_tracepaper(project):
        return ""
    head = git("rev-parse", "HEAD")
    if head is None:
        return ""
    if fetch:
        git("fetch", "--quiet", "origin", timeout=15)  # offline: compare with what is known
    remote = git("rev-parse", "--verify", "--quiet", BRANCH)
    record = read_record(project)
    used, skipped = record.get("version"), record.get("skipped")
    if used and git("cat-file", "-e", f"{used}^{{commit}}") is None:
        used = used + " (not in the checkout: perhaps from another computer; fetch it to compare)"

    notes = []
    if not used:
        notes.append(
            f"This project uses Tracepaper but has no {RECORD} yet, so which version it was written for is "
            f"unknown. The checkout is at {_short(head)}. Check the project's views and scripts against the "
            "current spec (the skill's Migrating section), then run `tracepaper adopt` in the project.")
    elif used != head and " " not in used:
        commits = _log(used, head)
        if _is_ancestor(used, head):
            notes.append(
                f"Tracepaper has been updated since this project last used it: the project was on "
                f"{_short(used)}, the checkout is now at {_short(head)}, {len(commits)} commits later. The old "
                "version is gone from this computer, so the project has to be migrated to the new one: tell the "
                "user, summarise the changes briefly, and migrate (the skill's Migrating section) once they "
                f"agree. The commits:\n{_listing(commits)}")
        else:
            notes.append(
                f"The project was last used with Tracepaper {_short(used)}, which isn't behind the checkout's "
                f"{_short(head)} (the checkout is older, or went another way). Tell the user; don't change "
                "anything until they say which version to use.")
    elif used != head:
        notes.append(f"The project's record names {used}. The checkout is at {_short(head)}.")

    if remote and remote != head and _is_ancestor(head, remote) and remote != skipped:
        commits = _log(head, remote)
        notes.append(
            f"A newer Tracepaper is on GitHub: {_short(remote)}, {len(commits)} commits after the checkout's "
            f"{_short(head)}. Tell the user, summarise the changes briefly, and ask whether to keep the current "
            "version or update. Updating pulls the checkout (`git -C <checkout> pull --ff-only`), which updates "
            "Tracepaper for every project on this computer, then migrates this project; the other projects are "
            "offered their migration when a session next starts in them. Keeping it: run `tracepaper adopt "
            f"--skip`, and this version isn't offered again. The commits:\n{_listing(commits)}")

    viewers = stale_viewers(project)
    if viewers:
        notes.append("These `tracepaper view`s of this project run older server code than the checkout has, so "
                     f"they lack its changes; restart them when the user agrees: {', '.join(viewers)}.")
    if not notes:
        return ""
    return (f"Tracepaper (checkout: {CHECKOUT}). Mention this to the user at the start of your first reply.\n\n"
            + "\n\n".join(notes)
            + f"\n\nThe changes in detail: `git -C {CHECKOUT} log -p <from>..<to> -- SPEC.md skills/tracepaper` "
              f"for what the formats and the skill now say, `git -C {CHECKOUT} log --stat <from>..<to>` for the "
              "rest.\n")


def adopt(path: Path, skip: bool = False) -> str:
    """Record the checkout's version as the project's, after migrating it; or, with skip,
    that the person keeps their version and isn't offered GitHub's newest again."""
    project = project_root(path)
    record = read_record(project)
    head = git("rev-parse", "HEAD")
    if head is None:
        raise SystemExit(f"tracepaper adopt: {CHECKOUT} is not a git checkout")
    if skip:
        remote = git("rev-parse", "--verify", "--quiet", BRANCH)
        if record.get("version") not in (None, head):
            raise SystemExit("tracepaper adopt --skip: the checkout has already moved past this project's version, "
                             "so it can't be kept; migrate the project, then run `tracepaper adopt`")
        record = {"version": head, **({"skipped": remote} if remote and remote != head else {})}
        write_record(project, record)
        return f"{project / RECORD}: keeping {_short(head)}" + (f", not offered {_short(remote)} again"
                                                                 if "skipped" in record else "")
    write_record(project, {"version": head})
    return f"{project / RECORD}: {_short(head)}"
