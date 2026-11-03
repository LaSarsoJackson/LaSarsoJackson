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
import signal
import subprocess
import sys
import time


def command(argv, *, capture=False, timeout=300, stdin=None, trim=True):
    # No shell interpolation, including changed filenames and agent arguments.
    process = subprocess.Popen(argv, text=True, encoding="utf-8", errors="replace",
                               stdin=subprocess.PIPE if stdin is not None else None,
                               stdout=subprocess.PIPE if capture else None,
                               stderr=subprocess.PIPE if capture else None,
                               start_new_session=os.name != "nt",
                               creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
    try:
        stdout, stderr = process.communicate(input=stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=15)
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.kill()
        process.wait()
        raise
    if process.returncode:
        detail = (stderr or stdout or "")[-10000:] if capture else ""
        raise RuntimeError(f"Command failed ({process.returncode}): {argv!r}\n{detail}")
    return (stdout.strip() if trim else stdout) if capture else ""


def git(*args):
    return command(["git", *args], capture=True)


def paths_output(*args):
    return [p for p in command(["git", *args], capture=True, trim=False).split("\0") if p]


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
        files = paths_output("diff", "--no-renames", "--name-only", "--diff-filter=ACMRD", "-z",
                             base, args.head, "--") if base else paths_output("ls-files", "-z")
    except RuntimeError:
        # Missing remote objects widen validation rather than silently skipping it.
        files = paths_output("ls-files", "-z")
    if not args.committed:
        files += paths_output("diff", "--no-renames", "--name-only", "--diff-filter=ACMRD", "-z", "HEAD", "--")
        files += paths_output("ls-files", "--others", "--exclude-standard", "-z")
    # Deleted and renamed-away paths still affect their former consumers.
    return sorted(set(files))


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
        if not path.is_file():
            continue
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
            argv.extend([p for p in selected if Path(p).is_file()] if item == "{files}" else [item])
        if not any(Path(p).is_file() for p in selected) and "{files}" in task["argv"]:
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
                "*eslint*", "*tsconfig*", "*vitest*", "*playwright*", "package.json",
                "*/package.json", "*pyproject.toml", "*requirements*.txt", "*.csproj",
                "scripts/*", "checks/*", "*lint*", "*config.*"]
    patterns += policy().get("protectedPaths", [])
    for task in policy().get("tasks", []):
        patterns += [item for item in task["argv"] if Path(item).is_file()]
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
    if current:
        extra = [p for p in Path(current).iterdir() if p.is_file() and p.name != "pre-push"]
        if extra:
            raise RuntimeError(f"Preserve the additional hooks in {current} before changing installation: {extra}")
    hook = Path(git("rev-parse", "--git-path", "hooks/pre-push"))
    backup = hook.with_name("pre-push.agent-ci-original")
    marker = "# agent-ci-native-hook-v1"
    if hook.is_symlink() or backup.is_symlink():
        raise RuntimeError("Integrate symbolic-link hooks explicitly before installation.")
    hook.parent.mkdir(parents=True, exist_ok=True)
    if hook.exists() and marker not in hook.read_text(encoding="utf-8", errors="replace"):
        if backup.exists() and backup.read_bytes() != hook.read_bytes():
            raise RuntimeError(f"Preserve the existing hook and earlier backup before installation: {backup}")
        if not backup.exists():
            shutil.copy2(hook, backup)
    wrapper = '''#!/bin/sh
# agent-ci-native-hook-v1
set -eu
refs=$(cat)
pass_refs() { if [ -n "$refs" ]; then printf '%s\\n' "$refs"; fi; }
root=$(git rev-parse --show-toplevel)
pass_refs | "$root/.githooks/pre-push" "$@"
original=$(git rev-parse --git-path hooks/pre-push.agent-ci-original)
if [ -f "$original" ]; then pass_refs | "$original" "$@"; fi
'''
    temporary = hook.with_name("pre-push.agent-ci-new")
    temporary.write_text(wrapper, encoding="utf-8", newline="\n")
    temporary.chmod(0o755)
    temporary.replace(hook)
    if current:
        git("config", "--local", "--unset", "core.hooksPath")
    print("Installed native pre-push validation. Existing pre-push and other native hooks remain active.")


def affected(args):
    patterns = json.loads(args.patterns)
    excluded = json.loads(args.exclude_patterns)
    selected = any(matches(p, patterns) and not matches(p, excluded) for p in changed(args))
    value = "true" if selected else "false"
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
            stream.write(f"affected={value}\n")
    print(f"affected={value}")


def hydrate(args):
    if git("status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("Sparse CI hydration requires a clean checkout.")
    patterns = ["/.github/"]
    for path in changed(args):
        if "\n" in path or "\r" in path:
            raise RuntimeError("Sparse CI hydration cannot represent a filename containing a newline.")
        escaped = "".join("\\" + char if char in "\\*?[]!# " else char for char in path)
        patterns.append("/" + escaped)
    argv = ["git"]
    if os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("GH_TOKEN"):
        # Scope private lazy-blob authentication to this Git invocation.
        argv += ["-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"]
    command(argv + ["sparse-checkout", "set", "--no-cone", "--stdin"], stdin="\n".join(patterns) + "\n")


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
    parser.add_argument("mode", choices=["check", "repair", "install", "ship", "rollback", "affected", "hydrate"])
    parser.add_argument("--base", help="Previously validated remote commit or comparison base")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--committed", action="store_true", help="Require exact clean HEAD for a push")
    parser.add_argument("--full", action="store_true", help="Also run declared full validation tasks")
    parser.add_argument("--syntax-only", action="store_true", help="Parse changed sources; native CI jobs run separately")
    parser.add_argument("--agent", help="Local agent executable and arguments as a JSON array; prompt is sent on stdin")
    parser.add_argument("--commit", help="Commit to revert through the normal checked delivery path")
    parser.add_argument("--patterns", default='["*"]', help="JSON path patterns for affected-job selection")
    parser.add_argument("--exclude-patterns", default='[]', help="Known paths outside a job's dependency scope")
    args = parser.parse_args()
    os.chdir(git("rev-parse", "--show-toplevel"))
    if args.mode == "repair" and not args.agent:
        parser.error("repair requires --agent")
    if args.mode == "rollback" and not args.commit:
        parser.error("rollback requires --commit")
    {"check": check, "repair": repair, "install": install,
     "ship": ship, "rollback": rollback, "affected": affected, "hydrate": hydrate}[args.mode](args)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.TimeoutExpired, ValueError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
