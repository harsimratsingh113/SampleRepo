#!/usr/bin/env python3
"""
Launch a Codex CLI RCA-to-PR workflow.

This script intentionally does not implement custom Jira/GitHub pipeline behavior. It fetches
Jira context through REST before Codex starts, prepares optional MCP access, renders a focused
Codex prompt, and asks Codex CLI to inspect GitHub/repo context, make code changes, and either
stop at dry-run output or push/open a PR.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

from codex_template_runner import (
    DEFAULT_GITHUB_MCP_FILE,
    DEFAULT_JIRA_MCP_FILE,
    ensure_github_mcp,
    ensure_jira_mcp,
    fetch_jira_issue_context,
    JiraRequestError,
    load_env_file,
    normalize_github_repo,
    validate_github_auth,
    validate_jira_auth,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Codex CLI against a Jira issue and optionally push/open a GitHub PR."
    )
    parser.add_argument("jira_key", help="Jira issue key, for example KAN-5")
    parser.add_argument(
        "--repo",
        default=None,
        help="Target GitHub repository as owner/repo. Defaults to GITHUB_DEFAULT_REPO or RCA_DEFAULT_REPO.",
    )
    parser.add_argument(
        "--repo-path",
        default=".",
        help="Local repository path Codex should inspect/edit (default: current directory).",
    )
    parser.add_argument(
        "--context-repo",
        action="append",
        default=[],
        metavar="OWNER/REPO|ABS_PATH",
        help="Additional repository context to scan for RCA, formatted as owner/repo|/absolute/path. Repeatable.",
    )
    parser.add_argument(
        "--github-org",
        default=None,
        help='Optional GitHub search qualifier for historical PR context, e.g. "org:mycompany".',
    )
    parser.add_argument(
        "--module-prefix",
        default=None,
        help="Optional directory focus inside the repository, e.g. src/payments.",
    )
    parser.add_argument(
        "--code-path",
        action="append",
        default=[],
        metavar="REL_PATH",
        help="Specific repo-relative file path Codex should inspect first. Repeatable.",
    )
    parser.add_argument(
        "--depth",
        default="standard",
        choices=("concise", "standard", "deep"),
        help="RCA depth to request from Codex (default: standard).",
    )
    parser.add_argument(
        "--base-branch",
        default=None,
        help="Base branch for the PR. If omitted, Codex should detect the repository default branch.",
    )
    parser.add_argument(
        "--branch-name",
        default=None,
        help="Branch name Codex should create/use. Defaults to codex/<jira-key>-rca-fix.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Prepare changes locally only. This is the default unless --push-pr is set.",
    )
    parser.add_argument(
        "--push-pr",
        action="store_true",
        help="Allow Codex to push the branch and open/update a GitHub PR.",
    )
    parser.add_argument(
        "--rca-only",
        action="store_true",
        help="Generate an RCA report only. Do not edit files, prepare a branch, push, or open a PR.",
    )
    parser.add_argument(
        "--report-file",
        default=os.getenv("CODEX_REPORT_FILE"),
        help="Optional path where Codex should write its final markdown report.",
    )
    parser.add_argument(
        "--post-rca-comment",
        action="store_true",
        help="Ask Codex to post the RCA summary back to Jira after analysis.",
    )
    parser.add_argument(
        "--post-pr-link-comment",
        action="store_true",
        help="In --push-pr mode, ask Codex to post the PR link back to Jira.",
    )
    parser.add_argument(
        "--skip-jira-fetch",
        action="store_true",
        help="Do not fetch Jira over REST before launching Codex.",
    )
    parser.add_argument(
        "--add-jira-mcp",
        action="store_true",
        help="Also register Jira MCP for the inner Codex run. Not needed for default Jira reads.",
    )
    parser.add_argument(
        "--skip-jira-mcp",
        action="store_true",
        help="Do not register Jira MCP before launching Codex. Jira MCP is skipped by default unless --add-jira-mcp is set.",
    )
    parser.add_argument(
        "--skip-jira-auth-check",
        action="store_true",
        help="Do not preflight Jira credentials before launching Codex.",
    )
    parser.add_argument(
        "--skip-github-mcp",
        action="store_true",
        help="Do not register GitHub MCP before launching Codex.",
    )
    parser.add_argument(
        "--skip-github-auth-check",
        action="store_true",
        help="Do not preflight GitHub credentials before launching Codex.",
    )
    parser.add_argument(
        "--jira-mcp-file",
        default=str(DEFAULT_JIRA_MCP_FILE),
        help="JSON file describing the Jira MCP server to add.",
    )
    parser.add_argument(
        "--github-mcp-file",
        default=str(DEFAULT_GITHUB_MCP_FILE),
        help="JSON file describing the GitHub MCP server to add.",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        dest="print_only",
        help="Print the rendered prompt instead of running Codex.",
    )
    return parser.parse_args(argv)


def default_branch_name(jira_key: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", jira_key.strip().lower()).strip("-")
    return f"codex/{slug}-rca-fix"


def format_code_paths(paths: list[str]) -> str:
    if not paths:
        return "- No explicit code paths were provided; discover relevant files from the ticket and repo."
    return "\n".join(f"- {path}" for path in paths)


def parse_context_repos(values: list[str]) -> list[tuple[str, Path]]:
    repos: list[tuple[str, Path]] = []
    for value in values:
        if "|" not in value:
            continue
        repo, path = value.split("|", 1)
        repo = repo.strip()
        path = path.strip()
        if repo and path:
            repos.append((repo, Path(path).expanduser().resolve()))
    return repos


def format_context_repos(repos: list[tuple[str, Path]]) -> str:
    if not repos:
        return "- None"
    return "\n".join(f"- {repo}: {path}" for repo, path in repos)


def render_prompt(
    args: argparse.Namespace,
    repo: str,
    repo_path: Path,
    mode: str,
    jira_context: str,
) -> str:
    jira_key = args.jira_key.strip().upper()
    branch_name = args.branch_name or default_branch_name(jira_key)
    base_branch = args.base_branch or "detect the repository default branch"
    branch_source = args.base_branch or "the detected repository default branch"
    module_focus = args.module_prefix or "entire repository"
    github_org = args.github_org or "no additional org qualifier"
    context_repos = parse_context_repos(args.context_repo)
    rca_template = """RCA report template:
Use this structure exactly:
1. Problem
2. Context
3. Assumptions
4. Proposed approach
5. Edge cases
6. Dependencies
7. Risks
8. Test strategy
9. Open questions"""

    if mode == "rca-only":
        post_rca = (
            "yes (the wrapper posts the final report as a Jira comment after Codex finishes)"
            if args.post_rca_comment
            else "no (rca-only disables Jira writes)"
        )
        post_pr_link = "no (rca-only disables Jira writes)"
    elif mode == "dry-run":
        post_rca = "no (dry-run disables Jira writes)"
        post_pr_link = "no (dry-run disables Jira writes)"
    else:
        post_rca = "yes" if args.post_rca_comment else "no"
        post_pr_link = "yes" if args.post_pr_link_comment else "no"
    rca_only_rules = (
        "Generate an RCA report only. Do not edit files, create branches, commit, run formatters, "
        "push branches, open PRs, post Jira comments, or run validation commands. Do not run build, "
        "test, app execution, dependency install, package restore, formatter, or code-generation "
        "commands. Inspect Jira context, local source, tests, Git history, and GitHub PR evidence in "
        "a read-only manner. Final response must be a markdown RCA report using the RCA template exactly."
    )
    dry_run_rules = (
        "Do not push branches, open PRs, or post Jira comments. You may make local edits and "
        "a local commit only if that helps produce a reviewable dry run."
    )
    push_rules = (
        "After implementing and validating the fix, push the branch to GitHub and open or update "
        "a PR. Include RCA, similar Jira tickets, related historical PRs, changed files, tests, "
        "risks, and rollback notes in the PR body. If GitHub MCP supports PR comments, add a short "
        "PR comment with the similar-ticket/past-PR evidence; otherwise keep that evidence in the PR body."
    )
    if mode == "rca-only":
        mode_rules = rca_only_rules
        workflow_steps = """1. Summarize the confirmed problem, assumptions, related Jira/PR evidence, and likely files.
2. Inspect the local repository and GitHub PR evidence only as needed to support the RCA.
3. Do not make code changes, commits, branches, pushes, PRs, Jira comments, or other write actions.
4. Do not run builds, tests, application commands, dependency restores, installers, formatters, or other validation commands.
5. Produce the final markdown RCA report using the template exactly."""
    else:
        mode_rules = dry_run_rules if mode == "dry-run" else push_rules
        workflow_steps = f"""1. Summarize the confirmed problem, assumptions, related Jira/PR evidence, and likely files.
2. Make the smallest implementation-focused code change that addresses the RCA.
3. Add or update focused tests when the repository has a relevant test pattern.
4. Run appropriate validation commands and capture the exact results.
5. Prepare a clean branch named `{branch_name}` from {branch_source} if code changes are made.
6. Mode behavior: {mode_rules}"""

    return f"""Run a Codex CLI RCA-to-PR workflow for Jira issue {jira_key}.

Target:
- Jira issue: {jira_key}
- Primary GitHub repo: {repo}
- Primary local repo path: {repo_path}
- Additional repository context:
{format_context_repos(context_repos)}
- Module focus: {module_focus}
- Initial code paths:
{format_code_paths(args.code_path)}
- Historical PR search qualifier: {github_org}
- RCA depth: {args.depth}
- Branch name: {branch_name}
- Base branch: {base_branch}
- Mode: {mode}
- Post RCA back to Jira: {post_rca}
- Post PR link back to Jira: {post_pr_link}

Jira context:
{jira_context}

{rca_template}

Hard constraints:
- This must be a Codex CLI-driven workflow. Do not run `jira-rca-assistant`, `./jira-rca-assistant`, or `python -m jira_rca_assistant`.
- You may inspect files under `jira_rca_assistant/` and docs such as `docs/CLI_ORCHESTRATOR_FRAMEWORK.md` only as reference for useful guardrails.
- Preserve unrelated local changes. Do not revert or overwrite changes that are not required for this ticket.
- If you cannot gather enough evidence or tests fail in a risky way, stop before push/PR and report the blocker.
- Keep secrets out of commits, PR bodies, Jira comments, and logs.
- In rca-only mode, do not run any build/test/run/install/restore/format command. Examples that are forbidden in rca-only mode include `dotnet build`, `dotnet run`, `dotnet test`, `npm test`, `npm install`, `mvn test`, `gradle test`, `pytest`, and equivalent commands.

Context gathering:
- Treat the Jira context snapshot above as the source of truth for the current issue.
- Do not call Jira MCP for Jira reads. The wrapper already fetched Jira context before launching Codex.
- If Jira MCP is explicitly enabled, use it only for requested Jira writebacks.
- Use GitHub MCP to search merged and open PRs that mention {jira_key} or similar Jira keys, then inspect relevant PR bodies, comments, changed files, and compact diffs.
- Inspect the primary repository and all additional repository context paths directly.
- Keep evidence grouped by repository when multiple repositories are provided.
- Prefer current source/test files over assumptions.
- Additional repository context is for RCA evidence unless the mode explicitly instructs otherwise.

Implementation workflow:
{workflow_steps}

Final response:
- Start with `# RCA Report: {jira_key}`.
- Include the nine RCA template sections exactly.
- State whether this was rca-only, dry-run, or push-pr mode.
- List changed files, or state `None` when no files were changed.
- For rca-only mode, state that validation commands were not run by design and provide recommended validation only.
- For dry-run or push-pr mode, summarize validation commands and outcomes, or recommended validation if no commands were run.
- Include PR URL if one was opened or updated.
- Include any Jira comment/PR comment actions taken.
"""


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_env_file()

    selected_modes = [args.dry_run, args.push_pr, args.rca_only]
    if sum(1 for selected in selected_modes if selected) > 1:
        print("Choose only one of --rca-only, --dry-run, or --push-pr.", file=sys.stderr)
        return 2

    repo = (
        args.repo
        or os.getenv("GITHUB_DEFAULT_REPO", "").strip()
        or os.getenv("RCA_DEFAULT_REPO", "").strip()
    )
    if not repo:
        print(
            "Missing target repo. Pass --repo owner/name or set GITHUB_DEFAULT_REPO / RCA_DEFAULT_REPO.",
            file=sys.stderr,
        )
        return 2
    try:
        repo = normalize_github_repo(repo, os.getenv("GITHUB_DEFAULT_OWNER", "").strip())
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.push_pr:
        mode = "push-pr"
    elif args.rca_only:
        mode = "rca-only"
    else:
        mode = "dry-run"
    repo_path = Path(args.repo_path).expanduser().resolve()
    jira_context = (
        "Jira context was not pre-fetched because --skip-jira-fetch was used. "
        "If Jira details are required, stop and report the missing Jira context."
    )
    jira_auth_checked = False

    if not args.skip_jira_fetch:
        if not args.skip_jira_auth_check:
            rc = validate_jira_auth(Path(args.jira_mcp_file).expanduser().resolve())
            jira_auth_checked = rc == 0
            if rc != 0:
                return rc
        try:
            jira_context = fetch_jira_issue_context(
                args.jira_key, Path(args.jira_mcp_file).expanduser().resolve()
            )
        except JiraRequestError as exc:
            print(f"Jira issue fetch failed: {exc}", file=sys.stderr)
            return 7
        print(f"Fetched Jira issue context for {args.jira_key.strip().upper()}.")

    prompt = render_prompt(args, repo, repo_path, mode, jira_context)

    if args.print_only:
        print(prompt)
        return 0

    if args.add_jira_mcp and not args.skip_jira_mcp:
        if not jira_auth_checked and not args.skip_jira_auth_check:
            rc = validate_jira_auth(Path(args.jira_mcp_file).expanduser().resolve())
            if rc != 0:
                return rc
        rc = ensure_jira_mcp(Path(args.jira_mcp_file).expanduser().resolve())
        if rc != 0:
            return rc

    if not args.skip_github_mcp:
        if not args.skip_github_auth_check:
            rc = validate_github_auth(Path(args.github_mcp_file).expanduser().resolve(), repo=repo)
            if rc != 0:
                return rc
        rc = ensure_github_mcp(Path(args.github_mcp_file).expanduser().resolve())
        if rc != 0:
            return rc

    codex_command = [
        "codex",
        "exec",
        "--cd",
        str(repo_path),
        "--skip-git-repo-check",
        "--full-auto",
        "--sandbox",
        "read-only" if mode == "rca-only" else "workspace-write",
    ]
    for _, context_path in parse_context_repos(args.context_repo):
        codex_command.extend(["--add-dir", str(context_path)])
    if args.report_file:
        report_file = Path(args.report_file).expanduser().resolve()
        report_file.parent.mkdir(parents=True, exist_ok=True)
        codex_command.extend(["--output-last-message", str(report_file)])
    codex_command.append(prompt)

    try:
        completed = subprocess.run(
            codex_command,
            check=False,
        )
    except FileNotFoundError:
        print("Could not find `codex` on PATH.", file=sys.stderr)
        return 4
    if args.report_file:
        print(f"Codex final report file: {Path(args.report_file).expanduser().resolve()}")
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
