"""Check commit messages in the commit-msg hook, the pre-push hook and CI; print "<rule>: <what>: <fix>" errors."""

import argparse
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Final

# ── Constants ─────────────────────────────────────────────────────────────────

TYPES: Final[tuple[str, ...]] = (
    "feat", "fix", "perf", "refactor", "test", "docs", "build", "ci", "revert",
)
"""The nine allowed types."""

LABELS: Final[tuple[str, ...]] = ("Problem:", "Change:", "Verification:")
"""The section labels in their required order."""

BOT_AUTHORS: Final[tuple[str, ...]] = ("dependabot[bot]",)
"""Bot authors whose commits from GitHub are checked only for the header."""

REPO_SCOPE: Final[str] = "repo"
"""The scope of a change that is not inside one part of the program, valid in every repository."""

WORK_PREFIXES: Final[tuple[str, ...]] = ("fixup! ", "amend! ")
"""Header prefixes of work commits that the commit-msg hook lets through."""

GITHUB_SEPARATOR: Final[str] = "---------"
"""The line GitHub puts before the co-authors of a squash commit."""

COAUTHOR_KEY: Final[str] = "Co-authored-by:"
"""The trailer key of the co-authors that GitHub appends to a squash commit."""

BREAKING_KEY: Final[str] = "BREAKING-CHANGE:"
"""The trailer key of a breaking change."""

SCISSORS: Final[str] = "# ------------------------ >8 ------------------------"
"""The line below which git ignores an edited message in scissors cleanup mode."""

NO_EDITOR: Final[str] = ":"
"""The GIT_EDITOR value that git gives a hook when the message is not edited in an editor."""

SKILL_HINT: Final[str] = "use the commit skill: load it and write the message again by its rules"
"""The last line of every rejection, because an agent that skipped the skill repeats the same errors."""

ZERO_SHA: Final[str] = "0" * 40
"""The SHA that git puts in the pre-push input for a ref that does not exist."""

DEFAULT_BRANCH: Final[str] = "main"
"""The default branch name when origin/HEAD is not set."""

DEFAULT_REMOTE: Final[str] = "origin"
"""The remote name when prek does not give one."""

FIELD_SEPARATOR: Final[str] = "\x1f"
"""The separator between the fields of one commit in the git log output."""

RECORD_SEPARATOR: Final[str] = "\x1e"
"""The separator between commits in the git log output."""

LOG_FORMAT: Final[str] = "--format=%H%x1f%an%x1f%P%x1f%B%x1e"
"""The git log format: sha, author name, parents and message of each commit."""

HEADER: Final[re.Pattern[str]] = re.compile(
    r"(?P<type>[^\s():!]+)(?:\((?P<scope>[^()]*)\))?(?P<bang>!)?: (?P<summary>.*)"
)
"""The header shape <type>(<scope>)!: <summary>."""

SQUASH_SUFFIX: Final[re.Pattern[str]] = re.compile(r" \(#\d+\)$")
"""The pull request number GitHub appends to a squash header."""

SCOPE_NAME: Final[re.Pattern[str]] = re.compile(r"[a-z0-9-]+")
"""A valid scope name."""

BULLET: Final[str] = "- "
"""The start of every section item."""

UNSAFE_STARTS: Final[tuple[str, ...]] = ("#", "---", "diff -", "Index: ")
"""Line starts that git drops on edit or where git am cuts the message."""

GITHUB_WRAP: Final[int] = 72
"""The longest body line that a GitHub squash merge keeps on one line."""

BREAKING_MARK: Final[re.Pattern[str]] = re.compile(r"(?:\* |- )?breaking[ -]change\s*:", re.IGNORECASE)
"""A breaking change marker that changelog tools read anywhere in the body."""

ISSUE_TRAILER: Final[re.Pattern[str]] = re.compile(
    r"(?:Closes|Refs): (?:#\d+|[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#\d+)"
)
"""The Closes and Refs trailers."""

BREAKING_TRAILER: Final[re.Pattern[str]] = re.compile(r"BREAKING-CHANGE: \S.*")
"""The BREAKING-CHANGE trailer."""

COAUTHOR_TRAILER: Final[re.Pattern[str]] = re.compile(r"Co-authored-by: \S.*? <[^<>\s@]+@[^<>\s]+>")
"""The Co-authored-by trailer with a name and an address."""

TRAILER_START: Final[re.Pattern[str]] = re.compile(r"[A-Z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*: ")
"""A line start shaped like a git trailer key, such as 'Signed-off-by: '."""

CHERRY_PICK: Final[re.Pattern[str]] = re.compile(r"\(cherry picked from commit (?:[0-9a-f]{40}|[0-9a-f]{64})\)")
"""The line git cherry-pick -x appends."""

TEMPLATE_FIELD: Final[re.Pattern[str]] = re.compile(r"<[^<>]+>")
"""A field of the message template, such as '<result>', that stands for real text."""

REVERT_LINE: Final[re.Pattern[str]] = re.compile(r"- revert commit (?:[0-9a-f]{40}|[0-9a-f]{64})")
"""The first item of Change: in a revert."""

ALLOWED_TRAILERS: Final[str] = (
    "Closes: #<number>, Refs: #<number>, BREAKING-CHANGE: <text> "
    "or the cherry-pick line"
)
"""The trailer list quoted in trailers errors."""


class GitError(Exception):
    """git could not read the commits to check."""


# ── Message ───────────────────────────────────────────────────────────────────


def normalize(raw: str, *, scissors: bool = False) -> list[str]:
    """Return the message lines as git stores them, keeping # lines; scissors cuts at the scissors line."""
    lines: list[str] = []
    for line in raw.replace("\r\n", "\n").split("\n"):
        line = line.rstrip()
        if scissors and line == SCISSORS:
            break
        if line or (lines and lines[-1]):
            lines.append(line)
    while lines and not lines[-1]:
        lines.pop()
    return lines


def paragraphs(lines: list[str]) -> list[list[str]]:
    """Split normalized lines into paragraphs separated by one empty line."""
    result: list[list[str]] = [[]]
    for line in lines:
        if line:
            result[-1].append(line)
        else:
            result.append([])
    return [paragraph for paragraph in result if paragraph]


def check_summary(summary: str) -> list[str]:
    """Check the summary after the squash suffix is cut."""
    if not summary:
        return ["summary: the summary is empty: write an imperative verb and what changes after ': '"]
    errors: list[str] = []
    if not re.match(r"[a-z]", summary):
        errors.append(
            f"summary: the summary {summary!r} does not start with a lowercase letter a-z: "
            "start with a lowercase imperative verb, such as 'add', 'remove' or 'handle'"
        )
    if summary.endswith("."):
        errors.append(f"summary: the summary {summary!r} ends with a period: remove it")
    return errors


def check_header(header: str, scopes: tuple[str, ...]) -> tuple[list[str], re.Match[str] | None]:
    """Check the header line and return its match."""
    header = SQUASH_SUFFIX.sub("", header)
    match: re.Match[str] | None = HEADER.fullmatch(header)
    if not match:
        return [(
            f"header: the header {header!r} is not '<type>(<scope>): <summary>': "
            "write a type, a scope in parentheses, ': ' and the summary, "
            "for example 'fix(parser): keep the last line'"
        )], None
    errors: list[str] = []
    kind: str = match.group("type")
    scope: str | None = match.group("scope")
    if kind not in TYPES:
        errors.append(f"type: the type {kind!r} is not allowed: use one of {', '.join(TYPES)}")
    allowed: str = ", ".join((*scopes, REPO_SCOPE))
    if scope is None:
        errors.append(f"scope: the header has no scope: write one of {allowed} in parentheses after the type")
    elif scope == kind:
        errors.append(f"scope: the scope {scope!r} repeats the type: use one of {allowed}")
    elif scope != REPO_SCOPE and scope not in scopes:
        errors.append(f"scope: the scope {scope!r} is not on the list: use one of {allowed}")
    errors += check_summary(match.group("summary"))
    return errors, match


def list_items(lines: list[str]) -> list[str]:
    """Join each list item with the lines that continue it."""
    items: list[str] = []
    for line in lines:
        if line.startswith(BULLET) or not items:
            items.append(line)
        else:
            items[-1] += " " + line.strip()
    return items


def check_items(label: str, lines: list[str]) -> list[str]:
    """Check that the lines of a section form list items without a final period."""
    errors: list[str] = []
    for index, line in enumerate(lines):
        if line in LABELS:
            errors.append(f"sections: no empty line before {line!r}: leave one empty line before each label")
        elif line.strip() == BULLET.strip():
            errors.append(f"sections: an item under {label!r} is empty: write the text after '- '")
        elif index == 0 and not line.startswith(BULLET):
            errors.append(
                f"sections: {line!r} under {label!r} is not a list item: start the line with '- ' and the text"
            )
        elif line.startswith(UNSAFE_STARTS):
            errors.append(
                f"sections: {line!r} starts like '#' or a patch, so git drops it or git am cuts the message there: "
                "join it to the item above or reword its start"
            )
        elif not line.startswith(BULLET) and TRAILER_START.match(line):
            errors.append(
                f"sections: {line!r} under {label!r} looks like a trailer: put trailers in the last paragraph "
                "after an empty line, or join the line to the item above"
            )
    for item in list_items(lines):
        if item.endswith("."):
            errors.append(f"sections: {item!r} ends with a period: remove the period")
        if TEMPLATE_FIELD.fullmatch(item.removeprefix(BULLET).strip()):
            errors.append(f"sections: {item!r} is a template field: write the real text of the item")
    return errors


def check_sections(body: list[list[str]]) -> tuple[list[str], dict[str, list[str]]]:
    """Check the three sections and return their lines by label."""
    errors: list[str] = []
    sections: dict[str, list[str]] = {}
    for index, label in enumerate(LABELS):
        if index >= len(body):
            errors.append(
                f"sections: the section {label!r} is missing: write Problem:, Change:, Verification: in this order, "
                "one empty line between sections"
            )
            continue
        first: str = body[index][0]
        if first == label:
            sections[label] = body[index][1:]
            if not sections[label]:
                errors.append(
                    f"sections: the section {label!r} has no item: write '- <text>' directly under the label"
                )
        elif first.startswith(label):
            errors.append(
                f"sections: {first!r} has text after the label: put {label!r} alone on its line and the text on the next line"
            )
        elif first.startswith(BULLET):
            errors.append(
                f"sections: an empty line splits a list before {first!r}: remove the empty lines between items"
            )
        else:
            errors.append(
                f"sections: expected {label!r} but found {first!r}: write Problem:, Change:, Verification: "
                "in this order, each label alone on a line, one empty line between sections"
            )
    for label, lines in sections.items():
        errors += check_items(label, lines)
    if len(body) > 3 and body[3][0].startswith(BULLET):
        errors.append(
            f"sections: an empty line splits a list before {body[3][0]!r}: remove the empty lines between items"
        )
    if len(body) > 4:
        extra: list[str] = [paragraph[0] for paragraph in body[4:]]
        errors.append(
            f"sections: unexpected paragraphs after the trailers, starting with {extra!r}: "
            "keep only the three sections and one trailer paragraph"
        )
    return errors, sections


def check_verification(lines: list[str]) -> list[str]:
    """Check that every Verification: list item is '- <check>: <result>'."""
    errors: list[str] = []
    for item in list_items(lines):
        if not item.startswith(BULLET):
            continue
        check_name, separator, result = item.removeprefix(BULLET).partition(": ")
        if not separator or not check_name.strip() or not result.strip():
            errors.append(
                f"verification: {item!r} is not '- <check>: <result>': write one item per check, for example "
                "'- pytest tests/parser: 41 passed' or '- Windows runtime: not run, because the host runs macOS'"
            )
        elif TEMPLATE_FIELD.fullmatch(result.strip()):
            errors.append(f"verification: {item!r} has a template field as its result: write what the check showed")
    return errors


def git_trailers(message: str) -> list[str]:
    """Return the trailers git finds in the message."""
    run: subprocess.CompletedProcess[bytes] = subprocess.run(
        ["git", "interpret-trailers", "--parse", "--no-divider"],
        input=message.encode("utf-8"), capture_output=True, check=True,
    )
    return [line.rstrip() for line in run.stdout.decode("utf-8").splitlines() if line.strip()]


def check_trailers(trailers: list[str], author_message: str) -> list[str]:
    """Check the trailer paragraph and how git reads it."""
    errors: list[str] = []
    for line in trailers:
        if not (ISSUE_TRAILER.fullmatch(line) or BREAKING_TRAILER.fullmatch(line) or CHERRY_PICK.fullmatch(line)):
            errors.append(
                f"trailers: {line!r} is not an allowed trailer: move the text into a section, "
                f"or use only {ALLOWED_TRAILERS} in the last paragraph"
            )
        elif len(line) > GITHUB_WRAP:
            errors.append(
                f"trailers: {line!r} has more than {GITHUB_WRAP} characters, and a GitHub squash merge wraps it, "
                "so git reads no trailers: shorten the line"
            )
    breaking: list[int] = [index for index, line in enumerate(trailers) if line.startswith(BREAKING_KEY)]
    if len(breaking) > 1:
        errors.append("trailers: there is more than one BREAKING-CHANGE trailer: keep one sentence in one trailer")
    if breaking and breaking[0] != 0:
        errors.append("trailers: BREAKING-CHANGE is not the first trailer: move it to the first line of the last paragraph")
    expected: list[str] = [line for line in trailers if not CHERRY_PICK.fullmatch(line)]
    parsed: list[str] = git_trailers(author_message)
    if parsed != expected:
        errors.append(
            f"trailers: git reads the trailers as {parsed!r}, not as the last paragraph {expected!r}: "
            f"keep only {ALLOWED_TRAILERS} lines in the last paragraph, after an empty line"
        )
    return errors


def check_breaking(trailers: list[str], other_lines: list[str], *, bang: bool) -> list[str]:
    """Check that '!' and the BREAKING-CHANGE trailer go together."""
    errors: list[str] = []
    count: int = sum(line.startswith(BREAKING_KEY) for line in trailers)
    if bang and count != 1:
        errors.append(
            "breaking: the header has '!' but the trailers have no single BREAKING-CHANGE: add "
            "'BREAKING-CHANGE: <what stops working>; <what to do instead>' as the first line of the last "
            "paragraph, or remove '!'"
        )
    if not bang and count:
        errors.append("breaking: the trailers have BREAKING-CHANGE but the header has no '!': put '!' right before ': '")
    for line in other_lines:
        if BREAKING_MARK.match(line):
            errors.append(
                f"breaking: {line!r} marks a breaking change outside the trailers: move it to the last paragraph as "
                "'BREAKING-CHANGE: <text>' with '!' in the header, or reword the line"
            )
    return errors


def is_github_appendix(paragraph: list[str]) -> bool:
    """Tell whether a paragraph is the co-author list of a GitHub squash, with any cherry-pick line after it."""
    names: list[str] = [line for line in paragraph if not CHERRY_PICK.fullmatch(line)]
    return bool(names) and paragraph[: len(names)] == names and all(line.startswith(COAUTHOR_KEY) for line in names)


def check(
    raw: str, scopes: tuple[str, ...] = (), author: str = "", *, github_appendix: bool = False, scissors: bool = False
) -> list[str]:
    """Return the grammar errors of a commit message."""
    lines: list[str] = normalize(raw, scissors=scissors)
    if not lines:
        return ["header: the message is empty: write the header, the three sections and the trailers"]
    if lines[0].startswith(WORK_PREFIXES):
        return []
    parts: list[list[str]] = paragraphs(lines)
    appendix: list[str] = []
    if github_appendix and len(parts) >= 3 and parts[-2] == [GITHUB_SEPARATOR] and is_github_appendix(parts[-1]):
        appendix = [line for line in parts[-1] if not CHERRY_PICK.fullmatch(line)]
        parts = parts[:-2]
    errors, match = check_header(parts[0][0], scopes)
    if len(parts[0]) > 1:
        errors.append(
            f"header: the header continues on the next line {parts[0][1]!r}: keep the header on one line "
            "and leave one empty line after it"
        )
    if author in BOT_AUTHORS:
        return errors
    body: list[list[str]] = parts[1:]
    trailers: list[str] = body[3] if len(body) >= 4 else []
    other_lines: list[str] = [line for part in parts if part is not trailers for line in part]
    if match:
        errors += check_breaking(trailers, other_lines, bang=bool(match.group("bang")))
    section_errors, sections = check_sections(body)
    errors += section_errors
    errors += check_verification(sections.get("Verification:", []))
    if not section_errors:
        errors += check_trailers(trailers, "\n\n".join("\n".join(part) for part in parts) + "\n")
    for line in appendix:
        if not COAUTHOR_TRAILER.fullmatch(line):
            errors.append(f"trailers: {line!r} is not 'Co-authored-by: <name> <email>': correct the line")
    if match and match.group("type") == "revert":
        change: list[str] = list_items(sections.get("Change:", []))
        if not change or not REVERT_LINE.fullmatch(change[0]):
            errors.append(
                f"revert: the first item of Change: is {change[0] if change else ''!r}: write exactly "
                "'- revert commit <full SHA>' with the SHA from 'git log -1 --format=%H <commit>' and nothing after it; "
                "the reason goes to Problem:"
            )
    return errors


# ── History ───────────────────────────────────────────────────────────────────


def git_output(*args: str) -> str:
    """Return the stripped output of a git command, or '' when it fails."""
    run: subprocess.CompletedProcess[bytes] = subprocess.run(["git", *args], capture_output=True, check=False)
    return run.stdout.decode("utf-8", errors="replace").strip() if run.returncode == 0 else ""


def commits(*rev_args: str) -> list[tuple[str, str, int, str]]:
    """Return sha, author name, parent count and message of each commit in the revision range."""
    run: subprocess.CompletedProcess[bytes] = subprocess.run(
        ["git", "log", "--reverse", LOG_FORMAT, *rev_args], capture_output=True, check=False
    )
    if run.returncode != 0:
        raise GitError(run.stderr.decode("utf-8", errors="replace").strip())
    result: list[tuple[str, str, int, str]] = []
    for record in run.stdout.decode("utf-8", errors="replace").split(RECORD_SEPARATOR):
        fields: list[str] = record.strip("\n").split(FIELD_SEPARATOR)
        if len(fields) == 4:
            sha, name, parents, message = fields
            result.append((sha, name, len(parents.split()), message))
    return result


def is_ancestor(sha: str, commit: str) -> bool:
    """Tell whether sha is in the history of commit."""
    run: subprocess.CompletedProcess[bytes] = subprocess.run(
        ["git", "merge-base", "--is-ancestor", sha, commit], capture_output=True, check=False
    )
    return run.returncode == 0


def check_revert_target(message: str, commit: str = "HEAD") -> list[str]:
    """Check that the commit named in a revert is in the history of the given commit."""
    errors: list[str] = []
    for line in normalize(message):
        if not REVERT_LINE.fullmatch(line):
            continue
        sha: str = line.split()[-1]
        if not is_ancestor(sha, commit):
            errors.append(
                f"revert: the reverted commit {sha} is not in the history of this branch: "
                "use the full SHA of a commit on this branch from 'git log -1 --format=%H <commit>'"
            )
    return errors


def check_commit(
    sha: str, author: str, parents: int, message: str, scopes: tuple[str, ...], *, from_github: bool = False
) -> list[str]:
    """Return the errors of one commit on the default branch."""
    header: str = message.split("\n", 1)[0]
    errors: list[str]
    if author in BOT_AUTHORS and from_github:
        errors = check(message, scopes, author)
    elif parents > 1:
        errors = [
            (
                f"branch: the commit is a merge commit {header!r}: the default branch takes no merge commits, "
                "so squash or rebase the branch instead"
            )
        ]
    elif header.startswith(WORK_PREFIXES):
        errors = [f"branch: {header!r} is a work commit: fold it with 'git rebase --autosquash <base>' before the push"]
    else:
        errors = check(message, scopes, github_appendix=from_github)
        match: re.Match[str] | None = HEADER.fullmatch(SQUASH_SUFFIX.sub("", header))
        if match and match.group("type") == "revert":
            errors += check_revert_target(message, sha)
    return [f"{error} (commit {sha[:12]})" for error in errors]


def check_range(rev_args: list[str], scopes: tuple[str, ...], *, from_github: bool = False) -> list[str]:
    """Return the errors of every commit that the revision arguments select for the default branch."""
    errors: list[str] = []
    for sha, name, parents, message in commits(*rev_args):
        errors += check_commit(sha, name, parents, message, scopes, from_github=from_github)
    return errors


def wrap_like_github(body: str) -> str:
    """Return the body with each list item longer than GITHUB_WRAP broken at spaces, as a squash merge does."""
    lines: list[str] = []
    for line in body.replace("\r\n", "\n").split("\n"):
        item: str = line.rstrip()
        if item.startswith(BULLET) and len(item) > GITHUB_WRAP:
            lines += textwrap.wrap(item, GITHUB_WRAP, break_long_words=False, break_on_hyphens=False)
        else:
            lines.append(line)
    return "\n".join(lines)


def check_pull_request(title: str, body: str, scopes: tuple[str, ...], author: str = "") -> list[str]:
    """Check the title and body of a pull request as the message its squash commit will get."""
    if title.startswith(WORK_PREFIXES):
        return [f"branch: the title {title!r} is the header of a work commit: write '<type>(<scope>): <summary>'"]
    message: str = f"{title}\n\n{wrap_like_github(body)}"
    if author in BOT_AUTHORS:
        return check(message, scopes, author)
    return check(message, scopes) + check_revert_target(message)


def default_branch() -> str:
    """Return the default branch name from origin/HEAD, or main."""
    ref: str = git_output("symbolic-ref", "--short", f"refs/remotes/{DEFAULT_REMOTE}/HEAD")
    return ref.split("/", 1)[1] if "/" in ref else DEFAULT_BRANCH


def pre_push_input(stdin: str) -> str | None:
    """Return the pre-push lines from stdin or prek variables: empty under prek without a target, None without input."""
    if stdin.strip():
        return stdin
    to_ref: str = os.environ.get("PRE_COMMIT_TO_REF", "")
    if not to_ref:
        return "" if os.environ.get("PRE_COMMIT") == "1" else None
    branch: str = os.environ.get("PRE_COMMIT_REMOTE_BRANCH", "")
    if not branch:
        return ""
    if not branch.startswith("refs/"):
        branch = f"refs/heads/{branch}"
    from_ref: str = os.environ.get("PRE_COMMIT_FROM_REF", "") or ZERO_SHA
    lines: str = f"HEAD {to_ref} {branch} {from_ref}\n"
    name: str = default_branch()
    target: str = f"refs/heads/{name}"
    local: str = git_output("rev-parse", "--verify", "--quiet", target)
    if branch == target or not local:
        return lines
    remote_name: str = os.environ.get("PRE_COMMIT_REMOTE_NAME", "") or DEFAULT_REMOTE
    remote: str = git_output("rev-parse", "--verify", "--quiet", f"refs/remotes/{remote_name}/{name}") or ZERO_SHA
    return lines + f"{target} {local} {target} {remote}\n"


def check_pre_push(stdin: str, scopes: tuple[str, ...]) -> list[str]:
    """Check the commits a push sends to the default branch, from the pre-push hook input."""
    target: str = f"refs/heads/{default_branch()}"
    errors: list[str] = []
    for line in stdin.splitlines():
        parts: list[str] = line.split()
        if len(parts) != 4 or parts[2] != target or set(parts[1]) == {"0"}:
            continue
        local, remote = parts[1], parts[3]
        rev_args: list[str] = [local, "--not", "--remotes"] if set(remote) == {"0"} else [f"{remote}..{local}"]
        errors += check_range(rev_args, scopes)
    return errors


def merge_in_progress() -> bool:
    """Tell whether the commit concludes a merge."""
    path: str = git_output("rev-parse", "--git-path", "MERGE_HEAD")
    return bool(path) and Path(path).is_file()


# ── Command line ──────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """Check a message file, a push, a commit range or a pull request; return 1 on any error."""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Check commit messages.",
        epilog=(
            "commit-msg hook: --scopes=<list> <message file>; pre-push hook: --scopes=<list> --pre-push; "
            "CI on the default branch: --scopes=<list> --range BASE..HEAD; "
            "CI on a pull request: --scopes=<list> --pr-title <title> --pr-body-file <file> --pr-author <login>"
        ),
    )
    parser.add_argument("--scopes", default="", help="comma-separated scope list of the repository")
    parser.add_argument("--range", help="check every commit in BASE..HEAD as the default branch would get it")
    parser.add_argument("--pre-push", action="store_true", help="read the pre-push hook input from stdin")
    parser.add_argument("--pr-title", help="pull request title; with --pr-body-file")
    parser.add_argument("--pr-body-file", help="file with the pull request body")
    parser.add_argument("--pr-author", default="", help="login of the pull request author; bots get the header check only")
    parser.add_argument("message_file", nargs="?", help="commit message file of the commit-msg hook")
    args: argparse.Namespace = parser.parse_args(argv)
    message_file: str | None = args.message_file
    revisions: str | None = args.range
    title: str | None = args.pr_title
    body_file: str | None = args.pr_body_file
    pre_push: bool = args.pre_push
    scopes: tuple[str, ...] = tuple(name for name in args.scopes.split(",") if name)
    pull_request: bool = title is not None or body_file is not None
    modes: int = sum((message_file is not None, pre_push, revisions is not None, pull_request))
    if modes != 1 or (pull_request and (title is None or body_file is None)):
        parser.error("use exactly one of: a message file, --pre-push, --range, or --pr-title with --pr-body-file")
    invalid: list[str] = [name for name in scopes if not SCOPE_NAME.fullmatch(name)]
    errors: list[str]
    if invalid:
        errors = [f"scope: the hook declares invalid scope names {invalid!r}: use lowercase letters, digits and hyphens"]
    elif pre_push or revisions is not None:
        try:
            push: str | None = pre_push_input(sys.stdin.read()) if pre_push else ""
            if push is None:
                errors = [
                    (
                        "pre-push: the hook got no push input on stdin and no PRE_COMMIT_TO_REF: "
                        "run it as the git pre-push hook or through prek"
                    )
                ]
            elif pre_push:
                errors = check_pre_push(push, scopes)
            else:
                errors = check_range([revisions or ""], scopes, from_github=True)
        except GitError as failure:
            errors = [f"git: cannot read the commits to check ({failure}): fetch the remote and check the range"]
    elif title is not None and body_file is not None:
        body: str = Path(body_file).read_bytes().decode("utf-8", errors="replace")
        errors = check_pull_request(title, body, scopes, args.pr_author)
    elif message_file is None or merge_in_progress():
        errors = []
    else:
        raw: str = Path(message_file).read_bytes().decode("utf-8", errors="replace")
        edited: bool = os.environ.get("GIT_EDITOR") != NO_EDITOR
        if edited and git_output("config", "commit.cleanup") != "scissors" and any(
            line.startswith("#") for line in raw.splitlines()
        ):
            errors = [
                (
                    "cleanup: the message has lines that start with '#', and commit.cleanup is not scissors, so the "
                    "hook cannot tell the git template from the text: run 'git config commit.cleanup scissors' "
                    "and commit again"
                )
            ]
        else:
            errors = check(raw, scopes, scissors=edited) + check_revert_target(raw)
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        print(SKILL_HINT, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
