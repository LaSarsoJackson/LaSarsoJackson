#!/usr/bin/env python3
"""Local, bounded validation and delivery using existing repository tools.

The maintained source is preference-backup/system-tools/agent-ci. Copies in
.github run without a network download. Python 3.10+ is required.
"""
import argparse
import ast
from contextlib import contextmanager
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def command(argv, *, capture=False, timeout=300, stdin=None):
    # No shell interpolation, including changed filenames and agent arguments.
    result = subprocess.run(argv, text=True, input=stdin, capture_output=capture,
                            timeout=timeout, encoding="utf-8", errors="replace")
    if result.returncode:
        detail = (result.stderr or result.stdout or "")[-10000:] if capture else ""
        raise RuntimeError(f"Command failed ({result.returncode}): {argv!r}\n{detail}")
    return result.stdout.strip() if capture else ""


def git(*args):
    return command(["git", *args], capture=True)


def paths_output(*args):
    return [p for p in git(*args).split("\0") if p]


def changed(args):
    if args.committed:
        if git("rev-parse", args.head) != git("rev-parse", "HEAD"):
            raise RuntimeError("The pushed commit is not the checked-out HEAD.")
        if git("status", "--porcelain", "--untracked-files=all"):
            raise RuntimeError("Commit or preserve working changes before validating a push.")
    if args.base and set(args.base) != {"0"}:
        base = args.base
    else:
        try:
            base = git("merge-base", "HEAD", "origin/main")
        except RuntimeError:
            base = None
    try:
        files = paths_output("diff", "--name-only", "--diff-filter=ACMR", "-z",
                             base, args.head, "--") if base else paths_output("ls-files", "-z")
    except RuntimeError:
        # Missing remote objects widen validation rather than silently skipping it.
        files = paths_output("ls-files", "-z")
    if not args.committed:
        files += paths_output("diff", "--name-only", "--diff-filter=ACMR", "-z", "HEAD", "--")
        files += paths_output("ls-files", "--others", "--exclude-standard", "-z")
    return sorted({p for p in files if Path(p).is_file()})


def matches(path, patterns):
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def policy():
    path = Path(".github/agent-ci.json")
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {"tasks": []}


def check(args):
    started = time.monotonic()
    files = changed(args)
    settings = policy()
    errors = []
    for filename in files:
        path = Path(filename)
        suffix = path.suffix.lower()
        try:
            if suffix == ".py":
                # compile() honors Python encoding declarations and writes no pycache.
                compile(path.read_bytes(), filename, "exec", flags=ast.PyCF_ONLY_AST)
            elif suffix == ".json":
                json.loads(path.read_text(encoding="utf-8-sig"))
            elif suffix in (".yml", ".yaml"):
                try:
                    import yaml
                except ImportError as error:
                    raise RuntimeError("YAML checks require: python -m pip install PyYAML==6.0.3") from error
                yaml.safe_load(path.read_text(encoding="utf-8-sig"))
            elif suffix in (".mjs", ".cjs", ".js"):
                command(["node", "--check", filename], capture=True, timeout=30)
            elif suffix == ".sh":
                command(["bash", "-n", filename], capture=True, timeout=30)
        except (ValueError, SyntaxError, RuntimeError, OSError, subprocess.TimeoutExpired) as error:
            errors.append(f"{filename}: {error}")
        except Exception as error:
            # YAML parser errors must produce a failed gate, never a skipped file.
            errors.append(f"{filename}: {error}")
    for task in ([] if args.syntax_only else settings.get("tasks", [])):
        if task.get("full", False) and not args.full:
            continue
        selected = [p for p in files if matches(p, task.get("paths", ["*"]))]
        if not selected and not args.full:
            continue
        argv = []
        for item in task["argv"]:
            argv.extend(selected if item == "{files}" else [item])
        if not selected and "{files}" in task["argv"]:
            continue
        print(f"Run {task['name']}", flush=True)
        try:
            command(argv, timeout=task.get("timeoutSeconds", 300))
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
            errors.append(f"{task['name']}: {error}")
    elapsed = round(time.monotonic() - started, 3)
    result = {"commit": git("rev-parse", "HEAD"), "files": len(files),
              "seconds": elapsed, "passed": not errors, "errors": errors}
    print(json.dumps(result, indent=2), flush=True)
    if errors:
        raise RuntimeError("Local validation failed. Fix the reported errors and rerun the same command.")
    return result


def protected_state():
    patterns = [".github/*", ".githooks/*", "*test*", "*spec*", "*lock*",
                "*eslint*", "*tsconfig*", "*vitest*", "*playwright*", "package.json"]
    candidates = paths_output("ls-files", "-z") + paths_output("ls-files", "--others", "--exclude-standard", "-z")
    return {p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
            for p in candidates if matches(p.lower(), patterns) and Path(p).is_file()}


@contextmanager
def writer_lock():
    lock = Path(git("rev-parse", "--git-path", "agent-ci-writer.lock"))
    try:
        lock.mkdir()
    except FileExistsError as error:
        raise RuntimeError(f"Another delivery or repair owns this checkout: {lock}") from error
    try:
        yield
    finally:
        lock.rmdir()


def repair(args):
    agent = json.loads(args.agent)
    if not isinstance(agent, list) or not agent or not all(isinstance(p, str) for p in agent):
        raise RuntimeError("--agent must be a JSON array containing an executable and its arguments.")
    initial = protected_state()
    with writer_lock():
        for attempt in range(3):
            try:
                return check(args)
            except RuntimeError:
                if attempt == 2:
                    raise RuntimeError("Repair exhausted its two attempts. Preserve changes for investigation.")
            prompt = ("Repair the local validation failure in this checkout. Run "
                      "python .github/agent-ci.py check to read the diagnostics. "
                      "Change application source only. Do not change tests, validation, workflows, "
                      "dependencies, permissions, or credentials. Do not commit, push, or deploy. "
                      "Return after fixing the failure.\n")
            command(agent, stdin=prompt, timeout=300)
            if protected_state() != initial:
                raise RuntimeError("Repair changed tests, dependency locks, or validation policy. Delivery stopped.")


def install(_args):
    current = subprocess.run(["git", "config", "--local", "--get", "core.hooksPath"],
                             text=True, capture_output=True).stdout.strip()
    if current and current != ".githooks":
        raise RuntimeError(f"Existing hooksPath is {current}; integrate its hooks before installation.")
    old = Path(git("rev-parse", "--git-path", "hooks/pre-push"))
    if not current and old.exists():
        raise RuntimeError(f"Preserve the existing hook at {old}; integrate it before installation.")
    git("config", "--local", "core.hooksPath", ".githooks")
    print("Installed .githooks for this checkout. Run check before committing and ship after committing.")


def affected(args):
    patterns = json.loads(args.patterns)
    selected = any(matches(p, patterns) for p in changed(args))
    value = "true" if selected else "false"
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
            stream.write(f"affected={value}\n")
    print(f"affected={value}")


def ship(args):
    with writer_lock():
        args.committed = True
        args.head = "HEAD"
        check(args)
        branch = git("branch", "--show-current")
        if not branch or branch in ("main", "master"):
            raise RuntimeError("Ship requires a focused branch; direct pushes to main are not used.")
        sha = git("rev-parse", "HEAD")
        command(["git", "push", "--set-upstream", "origin", branch])
        existing = json.loads(command(["gh", "pr", "list", "--head", branch,
                                       "--state", "open", "--json", "number,url"], capture=True))
        if not existing:
            command(["gh", "pr", "create", "--base", "main", "--head", branch, "--fill"])
        command(["gh", "pr", "merge", branch, "--auto", "--squash", "--match-head-commit", sha])
        print("Auto-merge requested using the existing local GitHub identity. Required checks remain enforced.")


def rollback(args):
    if git("status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("Rollback requires a clean isolated checkout.")
    git("fetch", "origin", "main")
    sha = git("rev-parse", "--verify", f"{args.commit}^{{commit}}")
    git("merge-base", "--is-ancestor", sha, "origin/main")
    git("switch", "-c", f"revert/{sha[:10]}-{int(time.time())}", "origin/main")
    git("revert", "--no-edit", sha)
    args.base = "origin/main"
    args.full = True
    ship(args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["check", "repair", "install", "ship", "rollback", "affected"])
    parser.add_argument("--base", help="Previously validated remote commit or comparison base")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--committed", action="store_true", help="Require exact clean HEAD for a push")
    parser.add_argument("--full", action="store_true", help="Also run declared full validation tasks")
    parser.add_argument("--syntax-only", action="store_true", help="Parse changed sources; native CI jobs run separately")
    parser.add_argument("--agent", help="Local agent executable and arguments as a JSON array; prompt is sent on stdin")
    parser.add_argument("--commit", help="Commit to revert through the normal checked delivery path")
    parser.add_argument("--patterns", default='["*"]', help="JSON path patterns for affected-job selection")
    args = parser.parse_args()
    os.chdir(git("rev-parse", "--show-toplevel"))
    if args.mode == "repair" and not args.agent:
        parser.error("repair requires --agent")
    if args.mode == "rollback" and not args.commit:
        parser.error("rollback requires --commit")
    {"check": check, "repair": repair, "install": install,
     "ship": ship, "rollback": rollback, "affected": affected}[args.mode](args)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.TimeoutExpired, ValueError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
