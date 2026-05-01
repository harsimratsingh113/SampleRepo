#!/usr/bin/env python3
"""
Render a named Codex CLI prompt template from docs/CODEX_CLI_INSTRUCTIONS.md and optionally
invoke `codex` with the rendered prompt.
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
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_INSTRUCTIONS = Path("docs/CODEX_CLI_INSTRUCTIONS.md")
DEFAULT_JIRA_MCP_FILE = Path("config/mcp_servers/jira.local.json")
DEFAULT_GITHUB_MCP_FILE = Path("config/mcp_servers/github.local.json")
DEFAULT_REPO_SCOPE = "entire repository"
DEFAULT_DEPTH = "standard"
DEFAULT_OUTPUT_ACTION = "return RCA in chat"
DEFAULT_COMMENT_POLICY = (
    "structured markdown with sections for problem, context, risks, test strategy, and open questions"
)
ENV_REF_PATTERN = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render and run a named Codex CLI prompt template."
    )
    parser.add_argument("template", help="Template name, for example RCA_AND_COMMENT")
    parser.add_argument("jira_key", help="Jira issue key, for example KAN-5")
    parser.add_argument(
        "--instructions-file",
        default=str(DEFAULT_INSTRUCTIONS),
        help="Markdown file containing '## Template: ...' sections",
    )
    parser.add_argument(
        "--repo-scope",
        default=DEFAULT_REPO_SCOPE,
        help=f"Value for <REPO_SCOPE> (default: {DEFAULT_REPO_SCOPE!r})",
    )
    parser.add_argument(
        "--depth",
        default=DEFAULT_DEPTH,
        help=f"Value for <DEPTH> (default: {DEFAULT_DEPTH!r})",
    )
    parser.add_argument(
        "--output-action",
        default=DEFAULT_OUTPUT_ACTION,
        help=f"Value for <OUTPUT_ACTION> (default: {DEFAULT_OUTPUT_ACTION!r})",
    )
    parser.add_argument(
        "--comment-policy",
        default=DEFAULT_COMMENT_POLICY,
        help="Value for <COMMENT_POLICY>",
    )
    parser.add_argument(
        "--add-jira-mcp",
        action="store_true",
        help="Register the Jira MCP server from a JSON config file before launching codex",
    )
    parser.add_argument(
        "--add-github-mcp",
        action="store_true",
        help="Register the GitHub MCP server from a JSON config file before launching codex",
    )
    parser.add_argument(
        "--add-all-mcp",
        action="store_true",
        help="Register both Jira and GitHub MCP servers before launching codex",
    )
    parser.add_argument(
        "--check-jira-auth",
        action="store_true",
        help="Validate Jira credentials from the MCP config before launching codex",
    )
    parser.add_argument(
        "--check-github-auth",
        action="store_true",
        help="Validate GitHub credentials from the MCP config before launching codex",
    )
    parser.add_argument(
        "--jira-mcp-file",
        default=str(DEFAULT_JIRA_MCP_FILE),
        help="JSON file describing the Jira MCP server to add",
    )
    parser.add_argument(
        "--github-mcp-file",
        default=str(DEFAULT_GITHUB_MCP_FILE),
        help="JSON file describing the GitHub MCP server to add",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        dest="print_only",
        help="Print the rendered prompt instead of running codex",
    )
    return parser.parse_args(argv)


def extract_template(markdown: str, template_name: str) -> str:
    pattern = re.compile(
        rf"^## Template:\s*{re.escape(template_name)}\s*$"
        r"(?:.*?\n)*?"
        r"```text\n(.*?)\n```",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(markdown)
    if not match:
        raise KeyError(template_name)
    return match.group(1).strip()


def render_template(template: str, args: argparse.Namespace) -> str:
    replacements = {
        "<JIRA_KEY>": args.jira_key.strip().upper(),
        "<REPO_SCOPE>": args.repo_scope,
        "<DEPTH>": args.depth,
        "<OUTPUT_ACTION>": args.output_action,
        "<COMMENT_POLICY>": args.comment_policy,
    }
    rendered = template
    for needle, value in replacements.items():
        rendered = rendered.replace(needle, value)
    return rendered


def load_mcp_config(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"MCP config in {path} must be a JSON object")
    return data


def load_env_file(path: Path = Path(".env")) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def resolve_mcp_config_path(path: Path) -> Path:
    if path.is_file():
        return path
    if path.name.endswith(".local.json"):
        example = path.with_name(path.name.replace(".local.json", ".example.json"))
        if example.is_file():
            return example
    return path


def resolve_mcp_env_value(key: str, value: object) -> str:
    raw = str(value).strip()
    match = ENV_REF_PATTERN.match(raw)
    if not match:
        return raw
    env_name = match.group(1)
    resolved = os.getenv(env_name, "").strip()
    if not resolved:
        raise ValueError(
            f"MCP config env value for {key!r} references ${env_name}, but {env_name} is not set"
        )
    return resolved


def atlassian_site_name_from_url(base_url: str) -> str:
    raw = base_url.strip().rstrip("/")
    if not raw:
        return ""
    parsed = urllib.parse.urlparse(raw if "://" in raw else f"https://{raw}")
    host = parsed.netloc or parsed.path.split("/", 1)[0]
    host = host.strip().lower()
    if host.endswith(".atlassian.net"):
        return host.removesuffix(".atlassian.net").split(".")[-1]
    return host.split(".")[0] if host else ""


def add_jira_env_aliases(env: dict[str, str]) -> dict[str, str]:
    enriched = dict(env)
    if "ATLASSIAN_SITE_NAME" not in enriched and enriched.get("JIRA_BASE_URL"):
        site_name = atlassian_site_name_from_url(enriched["JIRA_BASE_URL"])
        if site_name:
            enriched["ATLASSIAN_SITE_NAME"] = site_name
    if "ATLASSIAN_USER_EMAIL" not in enriched and enriched.get("JIRA_EMAIL"):
        enriched["ATLASSIAN_USER_EMAIL"] = enriched["JIRA_EMAIL"]
    if "ATLASSIAN_API_TOKEN" not in enriched and enriched.get("JIRA_API_TOKEN"):
        enriched["ATLASSIAN_API_TOKEN"] = enriched["JIRA_API_TOKEN"]

    if "JIRA_BASE_URL" not in enriched and enriched.get("ATLASSIAN_SITE_NAME"):
        enriched["JIRA_BASE_URL"] = f"https://{enriched['ATLASSIAN_SITE_NAME']}.atlassian.net"
    if "JIRA_EMAIL" not in enriched and enriched.get("ATLASSIAN_USER_EMAIL"):
        enriched["JIRA_EMAIL"] = enriched["ATLASSIAN_USER_EMAIL"]
    if "JIRA_API_TOKEN" not in enriched and enriched.get("ATLASSIAN_API_TOKEN"):
        enriched["JIRA_API_TOKEN"] = enriched["ATLASSIAN_API_TOKEN"]
    return enriched


def build_mcp_add_command(config: dict[str, object]) -> list[str]:
    name = str(config.get("name") or "").strip()
    if not name:
        raise ValueError("MCP config must include a non-empty 'name'")

    env = resolved_mcp_env(config)

    url = str(config.get("url") or "").strip()
    command = config.get("command")
    has_command = isinstance(command, list) and bool(command)
    if url and has_command:
        raise ValueError("MCP config must include either 'url' or 'command', not both")
    if not url and not has_command:
        raise ValueError("MCP config must include either a 'url' string or a 'command' array")

    for key, value in env.items():
        os.environ[key] = value

    cmd = ["codex", "mcp", "add", name]
    if url:
        cmd.extend(["--url", url])
        bearer_token_env_var = str(config.get("bearer_token_env_var") or "").strip()
        if bearer_token_env_var:
            if bearer_token_env_var not in env and not os.getenv(bearer_token_env_var):
                raise ValueError(
                    f"bearer_token_env_var {bearer_token_env_var!r} is not present in env"
                )
            cmd.extend(["--bearer-token-env-var", bearer_token_env_var])
        return cmd

    if not all(isinstance(x, str) and x for x in command):
        raise ValueError("MCP config 'command' must be an array of non-empty strings")
    for key, value in env.items():
        cmd.extend(["--env", f"{key}={value}"])
    cmd.append("--")
    cmd.extend(command)
    return cmd


def resolved_mcp_env(config: dict[str, object]) -> dict[str, str]:
    env_map = config.get("env")
    if not isinstance(env_map, dict) or not env_map:
        raise ValueError("MCP config must include a non-empty 'env' object")
    resolved: dict[str, str] = {}
    for key, value in env_map.items():
        k = str(key).strip()
        v = resolve_mcp_env_value(k, value)
        if not k or not v:
            raise ValueError("MCP config env keys and values must be non-empty strings")
        resolved[k] = v
    return add_jira_env_aliases(resolved)


def validate_jira_auth(path: Path) -> int:
    resolved_path = resolve_mcp_config_path(path)
    if not resolved_path.is_file():
        print(f"Jira MCP config file not found: {path}", file=sys.stderr)
        return 5
    try:
        env = resolved_mcp_env(load_mcp_config(resolved_path))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 6

    base_url = env.get("JIRA_BASE_URL", "").rstrip("/")
    email = env.get("JIRA_EMAIL", "")
    token = env.get("JIRA_API_TOKEN", "")
    missing = [
        name
        for name, value in (
            ("JIRA_BASE_URL", base_url),
            ("JIRA_EMAIL", email),
            ("JIRA_API_TOKEN", token),
        )
        if not value
    ]
    if missing:
        print(f"Jira MCP config missing required env values: {', '.join(missing)}", file=sys.stderr)
        return 6

    request = urllib.request.Request(f"{base_url}/rest/api/3/myself")
    auth = base64.b64encode(f"{email}:{token}".encode("utf-8")).decode("ascii")
    request.add_header("Authorization", f"Basic {auth}")
    request.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            data = json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        print("Jira auth check failed.", file=sys.stderr)
        print(f"HTTP status: {exc.code} {exc.reason}", file=sys.stderr)
        if body:
            print(f"Response: {body}", file=sys.stderr)
        return 7
    except Exception as exc:
        print(f"Jira auth check failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 7

    print("Jira auth check OK.")
    print(f"Display name: {data.get('displayName') or '(unknown)'}")
    if data.get("emailAddress"):
        print(f"Email: {data.get('emailAddress')}")
    print(f"Active: {data.get('active')}")
    print(f"Account type: {data.get('accountType') or '(unknown)'}")
    return 0


class JiraRequestError(RuntimeError):
    """Raised when the wrapper cannot fetch Jira context before launching Codex."""


def jira_env_from_mcp_file(path: Path) -> dict[str, str]:
    resolved_path = resolve_mcp_config_path(path)
    if not resolved_path.is_file():
        raise JiraRequestError(f"Jira MCP config file not found: {path}")
    try:
        env = resolved_mcp_env(load_mcp_config(resolved_path))
    except ValueError as exc:
        raise JiraRequestError(str(exc)) from exc

    missing = [
        name
        for name in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")
        if not env.get(name, "").strip()
    ]
    if missing:
        raise JiraRequestError(
            f"Jira config missing required env values: {', '.join(missing)}"
        )
    return env


def jira_api_get(
    env: dict[str, str],
    path: str,
    query: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict[str, object]:
    base_url = env["JIRA_BASE_URL"].rstrip("/")
    request_path = path if path.startswith("/") else f"/{path}"
    url = f"{base_url}{request_path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"

    request = urllib.request.Request(url)
    auth = base64.b64encode(
        f"{env['JIRA_EMAIL']}:{env['JIRA_API_TOKEN']}".encode("utf-8")
    ).decode("ascii")
    request.add_header("Authorization", f"Basic {auth}")
    request.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            data = json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        detail = f"HTTP status: {exc.code} {exc.reason}"
        if body:
            detail = f"{detail}; response: {body}"
        raise JiraRequestError(detail) from exc
    except Exception as exc:
        raise JiraRequestError(f"{type(exc).__name__}: {exc}") from exc

    if not isinstance(data, dict):
        raise JiraRequestError(f"Unexpected Jira response type: {type(data).__name__}")
    return data


def adf_to_text(value: object) -> str:
    chunks: list[str] = []

    def walk(node: object) -> None:
        if node is None:
            return
        if isinstance(node, str):
            chunks.append(node)
            return
        if isinstance(node, list):
            for child in node:
                walk(child)
            return
        if not isinstance(node, dict):
            return

        node_type = str(node.get("type") or "")
        if node_type == "text":
            chunks.append(str(node.get("text") or ""))
            return
        if node_type == "hardBreak":
            chunks.append("\n")
            return

        for child in node.get("content") or []:
            walk(child)
        if node_type in {
            "paragraph",
            "heading",
            "blockquote",
            "bulletList",
            "orderedList",
            "listItem",
        }:
            chunks.append("\n")

    walk(value)
    text = "".join(chunks)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}\n\n... truncated ..."


def _field_name(value: object, key: str = "name") -> str:
    if isinstance(value, dict):
        return str(value.get(key) or "")
    return ""


def _user_name(value: object) -> str:
    return _field_name(value, "displayName") or "(unassigned)"


def _names(values: object, key: str = "name") -> str:
    if not isinstance(values, list):
        return ""
    names = [_field_name(item, key) for item in values]
    return ", ".join(name for name in names if name)


def render_jira_issue_context(issue: dict[str, object]) -> str:
    fields = issue.get("fields")
    if not isinstance(fields, dict):
        raise JiraRequestError("Jira issue response did not include fields")

    issue_key = str(issue.get("key") or "")
    project = fields.get("project") if isinstance(fields.get("project"), dict) else {}
    comments_obj = fields.get("comment") if isinstance(fields.get("comment"), dict) else {}
    comments = comments_obj.get("comments") if isinstance(comments_obj, dict) else []
    if not isinstance(comments, list):
        comments = []

    attachments = fields.get("attachment") if isinstance(fields.get("attachment"), list) else []
    links = fields.get("issuelinks") if isinstance(fields.get("issuelinks"), list) else []
    description = adf_to_text(fields.get("description"))

    lines = [
        "## Jira Context Snapshot",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Key | {issue_key} |",
        f"| Project | {_field_name(project, 'key')} - {_field_name(project)} |",
        f"| Summary | {str(fields.get('summary') or '').strip()} |",
        f"| Issue Type | {_field_name(fields.get('issuetype'))} |",
        f"| Status | {_field_name(fields.get('status'))} |",
        f"| Priority | {_field_name(fields.get('priority'))} |",
        f"| Assignee | {_user_name(fields.get('assignee'))} |",
        f"| Reporter | {_user_name(fields.get('reporter'))} |",
        f"| Creator | {_user_name(fields.get('creator'))} |",
        f"| Labels | {', '.join(fields.get('labels') or []) if isinstance(fields.get('labels'), list) else ''} |",
        f"| Components | {_names(fields.get('components'))} |",
        f"| Fix Versions | {_names(fields.get('fixVersions'))} |",
        f"| Affects Versions | {_names(fields.get('versions'))} |",
        f"| Created | {fields.get('created') or ''} |",
        f"| Updated | {fields.get('updated') or ''} |",
        f"| Due Date | {fields.get('duedate') or ''} |",
        "",
        "### Description",
        "",
        truncate_text(description or "(no description)", 5000),
    ]

    if comments:
        lines.extend(["", "### Latest Comments", ""])
        for comment in comments[-5:]:
            if not isinstance(comment, dict):
                continue
            author = _user_name(comment.get("author"))
            created = str(comment.get("created") or "")
            body = truncate_text(adf_to_text(comment.get("body")) or "(empty comment)", 1200)
            lines.extend([f"#### {author} - {created}", "", body, ""])

    if attachments:
        names = [
            str(item.get("filename") or "")
            for item in attachments
            if isinstance(item, dict) and item.get("filename")
        ]
        if names:
            lines.extend(["", "### Attachments", "", ", ".join(names)])

    if links:
        rendered_links: list[str] = []
        for link in links[:10]:
            if not isinstance(link, dict):
                continue
            outward = link.get("outwardIssue")
            inward = link.get("inwardIssue")
            linked = outward if isinstance(outward, dict) else inward if isinstance(inward, dict) else None
            if isinstance(linked, dict):
                rendered_links.append(str(linked.get("key") or ""))
        if rendered_links:
            lines.extend(["", "### Linked Issues", "", ", ".join(rendered_links)])

    return "\n".join(lines).strip()


def fetch_jira_issue_context(jira_key: str, path: Path) -> str:
    env = jira_env_from_mcp_file(path)
    issue_path = f"/rest/api/3/issue/{urllib.parse.quote(jira_key.strip().upper(), safe='-_')}"
    fields = ",".join(
        [
            "summary",
            "description",
            "comment",
            "attachment",
            "issuelinks",
            "labels",
            "components",
            "issuetype",
            "status",
            "priority",
            "assignee",
            "reporter",
            "creator",
            "project",
            "fixVersions",
            "versions",
            "parent",
            "created",
            "updated",
            "duedate",
        ]
    )
    issue = jira_api_get(env, issue_path, {"fields": fields})
    return render_jira_issue_context(issue)


def _github_token_from_env(env: dict[str, str]) -> str:
    return (
        env.get("GITHUB_PERSONAL_ACCESS_TOKEN")
        or env.get("GITHUB_TOKEN")
        or env.get("GH_TOKEN")
        or ""
    ).strip()


def _github_request(url: str, token: str) -> tuple[dict[str, object], object]:
    request = urllib.request.Request(url)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8")
        return json.loads(body), response


def normalize_github_repo(repo: str, default_owner: str | None = None) -> str:
    spec = repo.strip()
    if spec.startswith("https://github.com/"):
        spec = spec.removeprefix("https://github.com/")
    elif spec.startswith("http://github.com/"):
        spec = spec.removeprefix("http://github.com/")
    elif spec.startswith("git@github.com:"):
        spec = spec.removeprefix("git@github.com:")
    spec = spec.removesuffix(".git").strip("/")
    parts = [part for part in spec.split("/") if part]
    if len(parts) == 1 and default_owner:
        return f"{default_owner.strip()}/{parts[0]}"
    if len(parts) < 2:
        raise ValueError(
            "GitHub repo must be owner/repo, a github.com repo URL, or a repo name with GITHUB_DEFAULT_OWNER set"
        )
    return "/".join(parts[:2])


def validate_github_auth(path: Path, repo: str | None = None) -> int:
    resolved_path = resolve_mcp_config_path(path)
    if not resolved_path.is_file():
        print(f"GitHub MCP config file not found: {path}", file=sys.stderr)
        return 5
    try:
        env = resolved_mcp_env(load_mcp_config(resolved_path))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 6

    token = _github_token_from_env(env)
    if not token:
        print(
            "GitHub MCP config missing GITHUB_PERSONAL_ACCESS_TOKEN, GITHUB_TOKEN, or GH_TOKEN.",
            file=sys.stderr,
        )
        return 6

    if repo:
        try:
            repo_spec = normalize_github_repo(repo, os.getenv("GITHUB_DEFAULT_OWNER", ""))
            repo_data, _ = _github_request(f"https://api.github.com/repos/{repo_spec}", token)
        except ValueError as exc:
            print(f"GitHub repo check failed: {exc}", file=sys.stderr)
            return 6
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:300]
            print(f"GitHub repo access check failed for {repo}.", file=sys.stderr)
            print(f"HTTP status: {exc.code} {exc.reason}", file=sys.stderr)
            if body:
                print(f"Response: {body}", file=sys.stderr)
            return 7
        except Exception as exc:
            print(f"GitHub repo access check failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 7
        print(f"Repo access OK: {repo_data.get('full_name') or repo_spec}")
        print(f"Default branch: {repo_data.get('default_branch') or '(unknown)'}")
        print("GitHub auth check OK.")
        return 0

    try:
        data, response = _github_request("https://api.github.com/user", token)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        print("GitHub auth check failed.", file=sys.stderr)
        print(f"HTTP status: {exc.code} {exc.reason}", file=sys.stderr)
        if body:
            print(f"Response: {body}", file=sys.stderr)
        return 7
    except Exception as exc:
        print(f"GitHub auth check failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 7

    print("GitHub auth check OK.")
    print(f"Login: {data.get('login') or '(unknown)'}")
    if data.get("name"):
        print(f"Name: {data.get('name')}")
    if response.headers.get("X-OAuth-Scopes"):
        print(f"OAuth scopes: {response.headers.get('X-OAuth-Scopes')}")
    if response.headers.get("X-RateLimit-Remaining"):
        print(f"Rate limit remaining: {response.headers.get('X-RateLimit-Remaining')}")

    return 0


def ensure_mcp(path: Path, label: str) -> int:
    resolved_path = resolve_mcp_config_path(path)
    if not resolved_path.is_file():
        print(f"{label} MCP config file not found: {path}", file=sys.stderr)
        return 5
    try:
        config = load_mcp_config(resolved_path)
        cmd = build_mcp_add_command(config)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 6

    try:
        completed = subprocess.run(cmd, check=False)
    except FileNotFoundError:
        print("Could not find `codex` on PATH.", file=sys.stderr)
        return 4
    if completed.returncode != 0:
        return int(completed.returncode)
    return 0


def ensure_jira_mcp(path: Path) -> int:
    return ensure_mcp(path, "Jira")


def ensure_github_mcp(path: Path) -> int:
    return ensure_mcp(path, "GitHub")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_env_file()
    instructions_path = Path(args.instructions_file).expanduser().resolve()
    if not instructions_path.is_file():
        print(f"Instructions file not found: {instructions_path}", file=sys.stderr)
        return 2

    markdown = instructions_path.read_text(encoding="utf-8")
    try:
        template = extract_template(markdown, args.template)
    except KeyError:
        print(
            f"Template {args.template!r} not found in {instructions_path}",
            file=sys.stderr,
        )
        return 3

    prompt = render_template(template, args)
    if args.check_jira_auth:
        mcp_path = Path(args.jira_mcp_file).expanduser().resolve()
        rc = validate_jira_auth(mcp_path)
        if rc != 0:
            return rc

    if args.check_github_auth:
        mcp_path = Path(args.github_mcp_file).expanduser().resolve()
        rc = validate_github_auth(mcp_path)
        if rc != 0:
            return rc

    if args.print_only:
        print(prompt)
        return 0

    if args.add_jira_mcp or args.add_all_mcp:
        mcp_path = Path(args.jira_mcp_file).expanduser().resolve()
        rc = ensure_jira_mcp(mcp_path)
        if rc != 0:
            return rc

    if args.add_github_mcp or args.add_all_mcp:
        mcp_path = Path(args.github_mcp_file).expanduser().resolve()
        rc = ensure_github_mcp(mcp_path)
        if rc != 0:
            return rc

    try:
        completed = subprocess.run(
            [
                "codex",
                "exec",
                "--skip-git-repo-check",
                "--full-auto",
                "--sandbox",
                "workspace-write",
                prompt,
            ],
            check=False,
        )
    except FileNotFoundError:
        print("Could not find `codex` on PATH.", file=sys.stderr)
        return 4
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
