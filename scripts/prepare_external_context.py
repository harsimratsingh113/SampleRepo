#!/usr/bin/env python3
"""
Prepare external context files for the Jira-triggered Codex workflow.

The Jira payload can reference files before those files exist in the runner workspace. This
script creates parent directories and safe placeholder files for payload-declared context files.
Use --default-files only when a workflow intentionally wants the standard Snyk/New Relic/
Confluence placeholders.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_CONTEXT_FILES = (
    ("Snyk vulnerabilities", "snyk.md"),
    ("New Relic logs", "newrelic.md"),
    ("Confluence runbook", "confluence.md"),
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create expected Codex external context files.")
    parser.add_argument(
        "--payload-file",
        default=os.getenv("GITHUB_EVENT_PATH"),
        help="GitHub event payload path. Defaults to GITHUB_EVENT_PATH.",
    )
    parser.add_argument(
        "--context-dir",
        default=os.getenv("CODEX_CONTEXT_DIR", "codex-context"),
        help="Directory for generated context files.",
    )
    parser.add_argument(
        "--default-files",
        action="store_true",
        help="Always create default snyk/newrelic/confluence placeholders.",
    )
    return parser.parse_args(argv)


def load_payload(path: str | None) -> dict[str, Any]:
    if not path or not Path(path).is_file():
        return {}
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON payload in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        return {}
    client_payload = payload.get("client_payload")
    if isinstance(client_payload, dict):
        merged = dict(payload)
        merged.update(client_payload)
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


def as_context_items(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
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
    return []


def context_specs(payload: dict[str, Any], context_dir: Path, include_defaults: bool) -> list[tuple[str, Path]]:
    items = as_context_items(
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

    specs: list[tuple[str, Path]] = []
    for item in items:
        if isinstance(item, dict):
            label = str(
                item.get("label")
                or item.get("name")
                or item.get("source")
                or item.get("type")
                or "External context"
            )
            raw_path = item.get("path") or item.get("file")
            if raw_path:
                specs.append((label, Path(str(raw_path))))
        elif isinstance(item, str):
            if "|" in item:
                label, raw_path = item.split("|", 1)
                specs.append((label.strip(), Path(raw_path.strip())))
            else:
                path = Path(item)
                specs.append((path.name, path))

    if include_defaults:
        for label, filename in DEFAULT_CONTEXT_FILES:
            specs.append((label, context_dir / filename))

    deduped: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for label, path in specs:
        resolved = path.expanduser()
        if not resolved.is_absolute():
            resolved = Path.cwd() / resolved
        resolved = resolved.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append((label, resolved))
    return deduped


def placeholder(label: str, path: Path) -> str:
    generated_at = datetime.now(timezone.utc).isoformat()
    return f"""# {label}

Generated: {generated_at}
Path: `{path}`

No external context was populated for this source before the Codex RCA step.

How to populate this file:
- Add a workflow step before `Run Jira-triggered Codex automation`.
- Fetch only Jira-issue-relevant evidence from the external source.
- Sanitize tokens, cookies, raw PII, customer secrets, and unrelated production payloads.
- Write the sanitized markdown or JSON summary to this path.
"""


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = load_payload(args.payload_file)
    context_dir = Path(args.context_dir).expanduser()
    if not context_dir.is_absolute():
        context_dir = Path.cwd() / context_dir
    context_dir.mkdir(parents=True, exist_ok=True)

    for label, path in context_specs(payload, context_dir.resolve(), args.default_files):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 0:
            print(f"External context exists: {label}: {path}")
            continue
        path.write_text(placeholder(label, path), encoding="utf-8")
        print(f"Created external context placeholder: {label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
