#!/usr/bin/env python3
"""
Automation entrypoint for Jira-triggered Codex runs.

This script reads a Jira/GitHub automation payload from a JSON file, stdin, or
environment variables, extracts the Jira issue key and run options, then delegates
to codex_rca_pr_runner. The delegated runner fetches Jira over REST before
starting `codex exec`, so the inner Codex session does not need Jira MCP for reads.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from codex_template_runner import normalize_github_repo
from codex_rca_pr_runner import main as run_codex_rca_pr


MODE_BY_LABEL = {
    "codex-rca": "rca-only",
    "codex-rca-only": "rca-only",
    "codex-rca-report": "rca-only",
    "codex-open-pr": "push-pr",
    "codex-push-pr": "push-pr",
    "codex-pr": "push-pr",
    "codex-dry-run": "dry-run",
}
RCA_COMMENT_LABELS = {"codex-rca"}
RCA_ARTIFACT_ONLY_LABELS = {"codex-rca-only", "codex-rca-report"}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract Jira automation payload details and run Codex RCA-to-PR flow."
    )
    parser.add_argument(
        "--payload-file",
        default=None,
        help="JSON payload file. Defaults to GITHUB_EVENT_PATH when available.",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read JSON payload from stdin instead of a file.",
    )
    parser.add_argument(
        "--issue-key",
        default=None,
        help="Override Jira issue key. Usually extracted from payload issue.key.",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="Override target GitHub repo as owner/repo.",
    )
    parser.add_argument(
        "--repo-path",
        default=None,
        help="Override local repo path. Defaults to GITHUB_WORKSPACE or current directory.",
    )
    parser.add_argument(
        "--mode",
        choices=("rca-only", "dry-run", "push-pr"),
        default=None,
        help="Override mode. Defaults to payload mode, labels, or dry-run.",
    )
    parser.add_argument(
        "--depth",
        choices=("concise", "standard", "deep"),
        default=None,
        help="Override RCA depth.",
    )
    parser.add_argument("--module-prefix", default=None, help="Optional repo area focus.")
    parser.add_argument("--github-org", default=None, help="Optional GitHub search qualifier.")
    parser.add_argument("--base-branch", default=None, help="Optional PR base branch.")
    parser.add_argument("--branch-name", default=None, help="Optional branch name.")
    parser.add_argument(
        "--code-path",
        action="append",
        default=[],
        metavar="REL_PATH",
        help="Specific repo-relative file path Codex should inspect first. Repeatable.",
    )
    parser.add_argument(
        "--context-file",
        action="append",
        default=[],
        metavar="LABEL|PATH",
        help="Additional external context file to scan for RCA, formatted as label|/path/to/file. Repeatable.",
    )
    parser.add_argument(
        "--post-rca-comment",
        action="store_true",
        help="Ask Codex to post RCA back to Jira when writeback is enabled.",
    )
    parser.add_argument(
        "--post-rca-comment-for-rca-only",
        action="store_true",
        help="After a successful codex-rca run, post the generated report file as a Jira comment. codex-rca-only stays artifact-only.",
    )
    parser.add_argument(
        "--post-pr-link-comment",
        action="store_true",
        help="Ask Codex to post PR link back to Jira in push-pr mode.",
    )
    parser.add_argument("--skip-jira-fetch", action="store_true")
    parser.add_argument("--skip-jira-auth-check", action="store_true")
    parser.add_argument("--skip-github-mcp", action="store_true")
    parser.add_argument("--skip-github-auth-check", action="store_true")
    parser.add_argument("--add-jira-mcp", action="store_true")
    parser.add_argument(
        "--report-file",
        default=None,
        help="Optional path where Codex should write the final markdown report.",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        dest="print_only",
        help="Print rendered Codex prompt instead of launching Codex.",
    )
    return parser.parse_args(argv)


def load_payload(args: argparse.Namespace) -> dict[str, Any]:
    raw = ""
    if args.stdin:
        raw = sys.stdin.read()
    else:
        payload_file = args.payload_file or os.getenv("GITHUB_EVENT_PATH", "")
        if payload_file and Path(payload_file).is_file():
            raw = Path(payload_file).read_text(encoding="utf-8")
        elif not sys.stdin.isatty():
            raw = sys.stdin.read()

    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON payload: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("Automation payload must be a JSON object.")
    return expand_embedded_payload(payload)


def expand_embedded_payload(payload: dict[str, Any]) -> dict[str, Any]:
    client_payload = payload.get("client_payload")
    if isinstance(client_payload, dict):
        merged = dict(payload)
        merged.update(client_payload)
        return merged

    for path in (
        ("jira_payload",),
        ("payload",),
        ("event_payload",),
        ("inputs", "jira_payload"),
        ("inputs", "payload"),
        ("inputs", "event_payload"),
    ):
        value = deep_get(payload, path)
        if not isinstance(value, str) or not value.strip().startswith("{"):
            continue
        try:
            embedded = json.loads(value)
        except json.JSONDecodeError:
            continue
        if isinstance(embedded, dict):
            merged = dict(payload)
            merged.update(embedded)
            return merged
    return payload


def deep_get(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def first_value(payload: dict[str, Any], paths: tuple[tuple[str, ...], ...]) -> Any:
    for path in paths:
        value = deep_get(payload, path)
        if value not in (None, ""):
            return value
    return None


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def as_repo_items(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                return parsed
        return [part.strip() for part in stripped.split(",") if part.strip()]
    if isinstance(value, dict):
        return [value]
    return []


def as_context_items(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                return parsed
        return [part.strip() for part in stripped.split(",") if part.strip()]
    if isinstance(value, dict):
        return [value]
    return []


def extract_codex_labels(value: Any) -> list[str]:
    labels: list[str] = []
    for item in as_list(value):
        matches = [match.lower() for match in re.findall(r"codex-[A-Za-z0-9-]+", item)]
        labels.extend(matches or [item.lower()])
    return labels


def extract_labels(payload: dict[str, Any]) -> list[str]:
    value = first_value(
        payload,
        (
            ("labels",),
            ("issue", "labels"),
            ("issue", "fields", "labels"),
            ("inputs", "labels"),
        ),
    )
    return extract_codex_labels(value)


def mode_from_payload(payload: dict[str, Any], explicit_mode: str | None) -> str:
    if explicit_mode:
        return explicit_mode
    value = first_value(
        payload,
        (
            ("mode",),
            ("codex_mode",),
            ("inputs", "mode"),
            ("inputs", "codex_mode"),
        ),
    )
    if isinstance(value, str) and value.strip() in {"rca-only", "dry-run", "push-pr"}:
        return value.strip()
    for label in extract_labels(payload):
        if label in MODE_BY_LABEL:
            return MODE_BY_LABEL[label]
    env_mode = os.getenv("CODEX_MODE", "dry-run").strip()
    return env_mode if env_mode in {"rca-only", "dry-run", "push-pr"} else "dry-run"


def should_post_rca_comment(args: argparse.Namespace, payload: dict[str, Any], mode: str) -> bool:
    labels = set(extract_labels(payload))
    if labels & RCA_ARTIFACT_ONLY_LABELS:
        return False
    if args.post_rca_comment or as_bool(
        first_value(payload, (("post_rca_comment",), ("inputs", "post_rca_comment")))
    ):
        return True
    if mode == "rca-only" and args.post_rca_comment_for_rca_only:
        return bool(labels & RCA_COMMENT_LABELS)
    return False


def extract_issue_key(args: argparse.Namespace, payload: dict[str, Any]) -> str:
    issue_key = (
        args.issue_key
        or first_value(
            payload,
            (
                ("issue_key",),
                ("jira_key",),
                ("issueKey",),
                ("key",),
                ("issue", "key"),
                ("jira", "key"),
                ("inputs", "issue_key"),
                ("inputs", "jira_key"),
                ("inputs", "issueKey"),
            ),
        )
        or os.getenv("JIRA_ISSUE_KEY")
        or os.getenv("ISSUE_KEY")
        or os.getenv("INPUT_ISSUE_KEY")
    )
    if not issue_key:
        raise SystemExit(
            "Missing Jira issue key. Provide issue.key in the payload, --issue-key, "
            "JIRA_ISSUE_KEY, ISSUE_KEY, or INPUT_ISSUE_KEY."
        )
    return str(issue_key)


def repo_from_value(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("repo", "full_name", "github_repo", "repository"):
            repo_value = value.get(key)
            if isinstance(repo_value, dict):
                repo_value = repo_value.get("full_name")
            if repo_value:
                return str(repo_value)
        return None
    if value:
        return str(value)
    return None


def extract_repo_specs(args: argparse.Namespace, payload: dict[str, Any]) -> list[dict[str, str | None]]:
    top_level_repo_path = (
        args.repo_path
        or first_value(payload, (("repo_path",), ("workspace",), ("inputs", "repo_path")))
        or None
    )
    repo_items = as_repo_items(
        first_value(
            payload,
            (
                ("repos",),
                ("repositories",),
                ("github_repos",),
                ("inputs", "repos"),
                ("inputs", "repositories"),
                ("inputs", "github_repos"),
            ),
        )
    )
    if args.repo:
        repo_items = [args.repo]
    if not repo_items:
        repo_value = first_value(
            payload,
            (
                ("repo",),
                ("repository", "full_name"),
                ("github_repo",),
                ("repository",),
                ("inputs", "repo"),
                ("inputs", "repository"),
                ("inputs", "github_repo"),
            ),
        )
        repo_items = [repo_value] if repo_value else []

    specs: list[dict[str, str | None]] = []
    for item in repo_items:
        repo = repo_from_value(item)
        if not repo:
            continue
        repo_path = None
        if isinstance(item, dict):
            repo_path_value = item.get("repo_path") or item.get("path") or item.get("workspace")
            repo_path = str(repo_path_value) if repo_path_value else None
        elif len(repo_items) == 1 and top_level_repo_path:
            repo_path = str(top_level_repo_path)
        specs.append({"repo": repo, "repo_path": repo_path})

    if not specs:
        repo = os.getenv("GITHUB_REPOSITORY") or os.getenv("GITHUB_DEFAULT_REPO")
        if repo:
            specs.append({"repo": repo, "repo_path": None})
    return specs


def repo_slug(repo: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "__", repo.strip()).strip("_") or "repo"


def safe_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip().lower()).strip("-") or "context"


def github_token_for_repo_clone() -> str:
    return (
        os.getenv("MULTI_REPO_GITHUB_TOKEN")
        or os.getenv("GH_TOKEN")
        or os.getenv("GITHUB_TOKEN")
        or ""
    ).strip()


def resolve_repo_path(repo: str, explicit_path: str | None, index: int) -> Path:
    if explicit_path:
        return Path(explicit_path).expanduser().resolve()

    workspace = Path(os.getenv("GITHUB_WORKSPACE") or ".").expanduser().resolve()
    try:
        normalized_repo = normalize_github_repo(repo, os.getenv("GITHUB_DEFAULT_OWNER", "").strip())
    except ValueError:
        normalized_repo = repo.strip()
    current_repo = os.getenv("GITHUB_REPOSITORY") or os.getenv("GITHUB_DEFAULT_REPO") or ""
    try:
        normalized_current = normalize_github_repo(
            current_repo, os.getenv("GITHUB_DEFAULT_OWNER", "").strip()
        )
    except ValueError:
        normalized_current = current_repo.strip()
    if normalized_current and normalized_repo.lower() == normalized_current.lower():
        return workspace

    base_dir = Path(os.getenv("CODEX_MULTI_REPO_DIR") or workspace / "codex-repos").resolve()
    run_id = os.getenv("GITHUB_RUN_ID") or str(os.getpid())
    target = base_dir / f"{index + 1}-{repo_slug(normalized_repo)}-{run_id}"
    if (target / ".git").is_dir():
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    clone_url = f"https://github.com/{normalized_repo}.git"
    command = ["git"]
    token = github_token_for_repo_clone()
    if token:
        command.extend(["-c", f"http.https://github.com/.extraheader=AUTHORIZATION: bearer {token}"])
    command.extend(["clone", "--depth", "1", clone_url, str(target)])
    print(f"Cloning {normalized_repo} into {target}")
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    subprocess.run(command, check=True, env=env)
    return target


def context_file_from_value(value: Any) -> tuple[str, Path] | None:
    if isinstance(value, dict):
        label = (
            value.get("label")
            or value.get("name")
            or value.get("source")
            or value.get("type")
            or "external-context"
        )
        path = value.get("path") or value.get("file")
        if path:
            return str(label), Path(str(path)).expanduser().resolve()
        text = value.get("text") or value.get("content") or value.get("markdown")
        if text is not None:
            return write_inline_context_file(str(label), str(text))
        return None
    if isinstance(value, str):
        if "|" in value:
            label, path = value.split("|", 1)
            return label.strip(), Path(path.strip()).expanduser().resolve()
        path = Path(value.strip()).expanduser()
        return path.name, path.resolve()
    return None


def write_inline_context_file(label: str, text: str) -> tuple[str, Path]:
    workspace = Path(os.getenv("GITHUB_WORKSPACE") or ".").expanduser().resolve()
    context_dir = Path(os.getenv("CODEX_CONTEXT_DIR") or workspace / "codex-context").resolve()
    context_dir.mkdir(parents=True, exist_ok=True)
    path = context_dir / f"{safe_slug(label)}.md"
    path.write_text(text, encoding="utf-8")
    return label, path


def extract_context_files(args: argparse.Namespace, payload: dict[str, Any]) -> list[tuple[str, Path]]:
    items: list[Any] = []
    items.extend(args.context_file or [])
    items.extend(
        as_context_items(
            first_value(
                payload,
                (
                    ("context_files",),
                    ("external_context_files",),
                    ("data_source_files",),
                    ("inputs", "context_files"),
                    ("inputs", "external_context_files"),
                    ("inputs", "data_source_files"),
                ),
            )
        )
    )

    inline_context = first_value(
        payload,
        (
            ("context_text",),
            ("external_context",),
            ("data_source_context",),
            ("inputs", "context_text"),
            ("inputs", "external_context"),
            ("inputs", "data_source_context"),
        ),
    )
    if inline_context:
        if isinstance(inline_context, dict):
            for label, text in inline_context.items():
                items.append({"label": str(label), "text": str(text)})
        else:
            items.append({"label": "external-context", "text": str(inline_context)})

    files: list[tuple[str, Path]] = []
    for item in items:
        context_file = context_file_from_value(item)
        if context_file:
            files.append(context_file)
    return files


def build_runner_args(
    args: argparse.Namespace,
    payload: dict[str, Any],
    *,
    issue_key: str,
    repo_override: str | None = None,
    repo_path_override: Path | None = None,
    report_file_override: str | None = None,
    context_repos: list[tuple[str, Path]] | None = None,
    context_files: list[tuple[str, Path]] | None = None,
) -> list[str]:
    repo_value = args.repo or first_value(
        payload,
        (
            ("repo",),
            ("repository", "full_name"),
            ("github_repo",),
            ("repository",),
            ("inputs", "repo"),
            ("inputs", "repository"),
            ("inputs", "github_repo"),
        ),
    )
    if isinstance(repo_value, dict):
        repo_value = repo_value.get("full_name")
    repo = repo_override or repo_value or os.getenv("GITHUB_REPOSITORY") or os.getenv("GITHUB_DEFAULT_REPO")
    repo_path = (
        str(repo_path_override)
        if repo_path_override
        else None
    ) or (
        args.repo_path
        or first_value(payload, (("repo_path",), ("workspace",), ("inputs", "repo_path")))
        or os.getenv("GITHUB_WORKSPACE")
        or os.getenv("CODEX_REPO_PATH")
        or "."
    )
    mode = mode_from_payload(payload, args.mode)
    depth = (
        args.depth
        or first_value(payload, (("depth",), ("inputs", "depth")))
        or os.getenv("CODEX_RCA_DEPTH")
        or "standard"
    )
    module_prefix = args.module_prefix or first_value(
        payload, (("module_prefix",), ("module_focus",), ("inputs", "module_prefix"))
    )
    github_org = args.github_org or first_value(
        payload, (("github_org",), ("inputs", "github_org"))
    )
    base_branch = args.base_branch or first_value(
        payload, (("base_branch",), ("inputs", "base_branch"))
    )
    branch_name = args.branch_name or first_value(
        payload, (("branch_name",), ("inputs", "branch_name"))
    )
    report_file = (
        report_file_override
        or args.report_file
        or first_value(payload, (("report_file",), ("inputs", "report_file")))
        or os.getenv("CODEX_REPORT_FILE")
    )
    code_paths = args.code_path or as_list(
        first_value(payload, (("code_paths",), ("code_path",), ("inputs", "code_paths")))
    )

    runner_args = [str(issue_key), "--repo-path", str(repo_path), "--depth", str(depth)]
    if repo:
        runner_args.extend(["--repo", str(repo)])
    for context_repo, context_path in context_repos or []:
        runner_args.extend(["--context-repo", f"{context_repo}|{context_path}"])
    for label, context_path in context_files or []:
        runner_args.extend(["--context-file", f"{label}|{context_path}"])
    if mode == "push-pr":
        runner_args.append("--push-pr")
    elif mode == "rca-only":
        runner_args.append("--rca-only")
    else:
        runner_args.append("--dry-run")
    for value, flag in (
        (module_prefix, "--module-prefix"),
        (github_org, "--github-org"),
        (base_branch, "--base-branch"),
        (branch_name, "--branch-name"),
    ):
        if value:
            runner_args.extend([flag, str(value)])
    for code_path in code_paths:
        runner_args.extend(["--code-path", code_path])

    if should_post_rca_comment(args, payload, mode):
        runner_args.append("--post-rca-comment")
    if args.post_pr_link_comment or as_bool(
        first_value(payload, (("post_pr_link_comment",), ("inputs", "post_pr_link_comment")))
    ):
        runner_args.append("--post-pr-link-comment")
    if args.skip_jira_fetch:
        runner_args.append("--skip-jira-fetch")
    if args.skip_jira_auth_check:
        runner_args.append("--skip-jira-auth-check")
    if args.skip_github_mcp:
        runner_args.append("--skip-github-mcp")
    if args.skip_github_auth_check:
        runner_args.append("--skip-github-auth-check")
    if args.add_jira_mcp:
        runner_args.append("--add-jira-mcp")
    if report_file:
        runner_args.extend(["--report-file", str(report_file)])
    if args.print_only:
        runner_args.append("--print")
    return runner_args


def runner_arg_value(runner_args: list[str], flag: str) -> str | None:
    try:
        index = runner_args.index(flag)
    except ValueError:
        return None
    if index + 1 >= len(runner_args):
        return None
    return runner_args[index + 1]


def markdown_to_adf(markdown: str) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    for line in markdown.splitlines():
        if line.strip():
            content.append(
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": line}],
                }
            )
        else:
            content.append({"type": "paragraph"})
    if not content:
        content.append(
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "Codex RCA report was empty."}],
            }
        )
    return {"type": "doc", "version": 1, "content": content}


def post_jira_comment(issue_key: str, report_file: str) -> int:
    path = Path(report_file).expanduser().resolve()
    if not path.is_file() or path.stat().st_size == 0:
        print(f"Skipping Jira RCA comment because report file is missing or empty: {path}")
        return 0

    jira_base_url = os.getenv("JIRA_BASE_URL", "").rstrip("/")
    jira_email = os.getenv("JIRA_EMAIL", "")
    jira_api_token = os.getenv("JIRA_API_TOKEN", "")
    if not jira_base_url or not jira_email or not jira_api_token:
        print(
            "Cannot post Jira RCA comment because JIRA_BASE_URL, JIRA_EMAIL, or JIRA_API_TOKEN is missing.",
            file=sys.stderr,
        )
        return 8

    try:
        max_chars = int(os.getenv("JIRA_RCA_COMMENT_MAX_CHARS", "30000"))
    except ValueError:
        max_chars = 30000
    report = path.read_text(encoding="utf-8", errors="replace")
    run_url = ""
    if os.getenv("GITHUB_SERVER_URL") and os.getenv("GITHUB_REPOSITORY") and os.getenv("GITHUB_RUN_ID"):
        run_url = (
            f"{os.getenv('GITHUB_SERVER_URL')}/{os.getenv('GITHUB_REPOSITORY')}"
            f"/actions/runs/{os.getenv('GITHUB_RUN_ID')}"
        )
    prefix = "Codex RCA report generated from `codex-rca` automation.\n\n"
    if run_url:
        prefix += f"GitHub Actions artifact: {run_url}\n\n"
    comment = prefix + report
    if len(comment) > max_chars:
        comment = (
            comment[: max_chars - 160].rstrip()
            + "\n\n[Truncated] Full RCA report is available in the GitHub Actions run artifact."
        )

    api_version = os.getenv("JIRA_COMMENT_API_VERSION", "3").strip() or "3"
    url = f"{jira_base_url}/rest/api/{api_version}/issue/{issue_key}/comment"
    if api_version == "2":
        payload = {"body": comment}
    else:
        payload = {"body": markdown_to_adf(comment)}

    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    auth = base64.b64encode(f"{jira_email}:{jira_api_token}".encode("utf-8")).decode("ascii")
    request.add_header("Authorization", f"Basic {auth}")
    request.add_header("Accept", "application/json")
    request.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")[:500]
        print("Failed to post Jira RCA comment.", file=sys.stderr)
        print(f"HTTP status: {exc.code} {exc.reason}", file=sys.stderr)
        if response_body:
            print(f"Response: {response_body}", file=sys.stderr)
        return 8
    except Exception as exc:
        print(f"Failed to post Jira RCA comment: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 8

    print(f"Posted Codex RCA report as Jira comment on {issue_key}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = load_payload(args)
    issue_key = extract_issue_key(args, payload)
    mode = mode_from_payload(payload, args.mode)
    repo_specs = extract_repo_specs(args, payload)
    if not repo_specs:
        raise SystemExit("Missing target repo. Provide repo, repos, or GITHUB_REPOSITORY.")
    if mode == "push-pr" and len(repo_specs) > 1:
        print(
            "Multiple repositories are supported for RCA/dry-run scanning only. "
            "Use one repo for push-pr mode.",
            file=sys.stderr,
        )
        return 2

    resolved_repos: list[tuple[str, Path]] = []
    for index, spec in enumerate(repo_specs):
        raw_repo = spec["repo"]
        if not raw_repo:
            continue
        try:
            normalized_repo = normalize_github_repo(
                raw_repo, os.getenv("GITHUB_DEFAULT_OWNER", "").strip()
            )
            repo_path = resolve_repo_path(normalized_repo, spec.get("repo_path"), index)
        except (subprocess.CalledProcessError, OSError, ValueError) as exc:
            print(f"Failed to prepare repository {raw_repo}: {exc}", file=sys.stderr)
            return 7
        resolved_repos.append((normalized_repo, repo_path))

    if not resolved_repos:
        raise SystemExit("No valid repositories were provided.")

    primary_repo, primary_repo_path = resolved_repos[0]
    report_file = (
        args.report_file
        or first_value(payload, (("report_file",), ("inputs", "report_file")))
        or os.getenv("CODEX_REPORT_FILE")
    )
    runner_args = build_runner_args(
        args,
        payload,
        issue_key=issue_key,
        repo_override=primary_repo,
        repo_path_override=primary_repo_path,
        report_file_override=str(report_file) if report_file else None,
        context_repos=resolved_repos[1:],
        context_files=extract_context_files(args, payload),
    )
    print(f"Automation payload resolved Jira issue {issue_key} with mode {mode}.")
    if len(resolved_repos) > 1:
        print("Scanning repositories in one Codex run:")
        for repo, path in resolved_repos:
            print(f"- {repo}: {path}")
    rc = run_codex_rca_pr(runner_args)
    if (
        rc == 0
        and mode == "rca-only"
        and should_post_rca_comment(args, payload, mode)
        and not args.print_only
    ):
        report_file = runner_arg_value(runner_args, "--report-file")
        if report_file:
            return post_jira_comment(issue_key, report_file)
        print("Skipping Jira RCA comment because no --report-file was configured.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
