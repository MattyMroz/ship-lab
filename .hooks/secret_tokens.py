"""Reject lines that carry a well-known API token shape, and with --pre-push the lines each pushed commit adds."""

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Final

TOKEN_KINDS: Final[dict[str, re.Pattern[bytes]]] = {
    "AWS access key": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "GitHub fine-grained token": re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    "OpenAI-style API key": re.compile(rb"\bsk-(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{20,}"),
    "Stripe live key": re.compile(rb"\bsk_live_[A-Za-z0-9]{16,}"),
    "Google API key": re.compile(rb"\bAIza[0-9A-Za-z_-]{35}"),
    "Slack token": re.compile(rb"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "Netlify token": re.compile(rb"\bnfp_[A-Za-z0-9]{20,}"),
    "Hugging Face token": re.compile(rb"\bhf_(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{30,}\b"),
}
"""Token kind by the pattern that matches its shape."""

PUSH_KINDS: Final[dict[str, re.Pattern[bytes]]] = {
    **TOKEN_KINDS,
    "private key": re.compile(rb"BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY|PuTTY-User-Key-File-\d|BEGIN OpenVPN Static key V\d"),
}
"""Kinds checked in pushed commits: the token kinds and the private key headers that detect-private-key knows."""

ZERO_SHA: Final[str] = "0" * 40
"""The SHA that git puts in the pre-push input for a ref that does not exist."""

DEFAULT_REMOTE: Final[str] = "origin"
"""The remote name when prek does not give one."""

DEFAULT_BRANCH: Final[str] = "main"
"""The default branch name when the remote has no HEAD."""

RECORD_SEPARATOR: Final[str] = "\x1e"
"""The separator before each commit in the git log output of a push."""

FIELD_SEPARATOR: Final[str] = "\x1f"
"""The separator between the sha and the message of a commit in the git log output of a push."""

COMMIT_MARK: Final[str] = f"{RECORD_SEPARATOR}commit "
"""The line that starts each commit in the git log output of a push."""

HUNK: Final[re.Pattern[bytes]] = re.compile(rb"^(@@+) (?:-\d+(?:,\d+)? )+\+(\d+)(?:,\d+)? \1")
"""A hunk header of a plain or combined diff: its at signs, one more than the parents, and the first result line."""

PUSH_ADVICE: Final[str] = (
    "push: a pushed commit adds a secret: revoke the secret at its issuer, then create the task branch again without "
    "that commit; the commit has not left this machine, so no pushed history changes"
)
"""What to do after a refused push."""


def push_ranges(stdin: str) -> list[list[str]] | None:
    """Return the git log arguments of each pushed ref: empty under prek without a target, None without input."""
    lines: list[tuple[str, str]] = []
    if stdin.strip():
        lines = [(parts[1], parts[3]) for parts in (line.split() for line in stdin.splitlines()) if len(parts) == 4]
    elif os.environ.get("PRE_COMMIT_TO_REF"):
        lines = [(os.environ["PRE_COMMIT_TO_REF"], os.environ.get("PRE_COMMIT_FROM_REF", "") or ZERO_SHA)]
    elif os.environ.get("PRE_COMMIT") != "1":
        return None
    name: str = os.environ.get("PRE_COMMIT_REMOTE_NAME", "") or DEFAULT_REMOTE
    default: list[str] = [
        ref
        for ref in (f"refs/remotes/{name}/HEAD", f"refs/remotes/{name}/{DEFAULT_BRANCH}")
        if _git("rev-parse", "--verify", "--quiet", ref).returncode == 0
    ][:1]
    ranges: list[list[str]] = []
    for local, remote in lines:
        if set(local) == {"0"}:
            continue
        known: bool = set(remote) != {"0"} and _git("cat-file", "-e", f"{remote}^{{commit}}").returncode == 0
        ranges.append([local, "--not", remote, *default] if known else [local, "--not", "--remotes"])
    return ranges


def _git(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", *args], capture_output=True, check=False)


def _kinds(text: bytes) -> list[str]:
    return [kind for kind, pattern in PUSH_KINDS.items() if pattern.search(text)]


def added_line_problems(log: bytes) -> list[str]:
    """Return a problem for each added line and added path of the git log output with a secret in it."""
    problems: list[str] = []
    sha: str = ""
    path: str = ""
    added_file: bool = False
    parents: int = 0
    number: int = 0
    mark: bytes = COMMIT_MARK.encode()
    for line in log.split(b"\n"):
        if parents and line[:1] in (b" ", b"+", b"-", b"\\"):
            prefix: bytes = line[:parents]
            if line[:1] == b"\\" or b"-" in prefix:
                continue
            if prefix == b"+" * parents:
                problems += [f"{sha[:7]} {path}:{number}: {kind}" for kind in _kinds(line)]
            number += 1
            continue
        parents = 0
        if line.startswith(mark):
            sha = line[len(mark) :].decode("ascii")
        elif line.startswith(b"--- "):
            added_file = line == b"--- /dev/null"
        elif line.startswith(b"+++ "):
            name: bytes = line[4:].rstrip(b"\t")
            kinds: list[str] = _kinds(name)
            path = "a path with a secret" if kinds else name.decode("utf-8", errors="replace")
            problems += [f"{sha[:7]} path: {kind}" for kind in kinds if added_file]
        elif match := HUNK.match(line):
            parents = len(match[1]) - 1
            number = int(match[2])
    return problems


def message_problems(log: bytes) -> list[str]:
    """Return a problem for each line of a commit message in the git log output with a secret in it."""
    problems: list[str] = []
    for record in log.split(RECORD_SEPARATOR.encode())[1:]:
        sha, _, message = record.partition(FIELD_SEPARATOR.encode())
        for number, line in enumerate(message.split(b"\n"), start=1):
            problems += [f"{sha.decode('ascii')[:7]} message:{number}: {kind}" for kind in _kinds(line)]
    return problems


def check_push(stdin: str) -> list[str]:
    """Return the secrets that the pushed commits add, or why the push cannot be read."""
    ranges: list[list[str]] | None = push_ranges(stdin)
    if ranges is None:
        return [
            "pre-push: the hook got no push input on stdin and no PRE_COMMIT_TO_REF: run it as the git pre-push hook"
        ]
    problems: list[str] = []
    for rev_args in ranges:
        log: subprocess.CompletedProcess[bytes] = _git(
            "-c",
            "core.quotepath=off",
            "log",
            "-p",
            "--cc",
            "-U0",
            "--text",
            "--no-textconv",
            "--no-color",
            "--no-renames",
            "--no-ext-diff",
            "--no-prefix",
            f"--format={COMMIT_MARK}%H",
            *rev_args,
        )
        messages: subprocess.CompletedProcess[bytes] = _git(
            "log", f"--format={RECORD_SEPARATOR}%H{FIELD_SEPARATOR}%B", *rev_args
        )
        if log.returncode or messages.returncode:
            return [f"git: cannot read the pushed commits {' '.join(rev_args)}: fetch the remote and push again"]
        problems += message_problems(messages.stdout) + added_line_problems(log.stdout)
    return [*problems, PUSH_ADVICE] if problems else []


def check_files(paths: list[str]) -> list[str]:
    """Return a problem for each token-shaped value in the files."""
    problems: list[str] = []
    for arg in paths:
        path: Path = Path(arg)
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_bytes().splitlines(), start=1):
            for kind, pattern in TOKEN_KINDS.items():
                if pattern.search(line):
                    problems.append(f"{path}:{number}: {kind}")
    return problems


def main() -> int:
    """Report token-shaped values without echoing them; return 1 if there is one."""
    args: list[str] = sys.argv[1:]
    problems: list[str] = check_push(sys.stdin.read()) if args == ["--pre-push"] else check_files(args)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
