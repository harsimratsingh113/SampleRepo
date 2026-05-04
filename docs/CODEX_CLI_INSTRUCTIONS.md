# Codex CLI Instructions

This file defines reusable prompt templates for Codex CLI execution. The goal is to avoid
rewriting the same RCA request every time and instead use a small set of standard prompt
shapes with a few ticket-specific substitutions.

## Quick Start

Use the helper runner when you want to specify only a template name and Jira key:

```bash
python3 scripts/codex_template_runner.py RCA_AND_COMMENT KAN-5
```

If you also want the runner to register the Jira MCP server first from a dedicated config file:

```bash
python3 scripts/codex_template_runner.py RCA_AND_COMMENT KAN-5 --add-jira-mcp
```

Register Jira and GitHub MCP before launching Codex:

```bash
python3 scripts/codex_template_runner.py RCA_AND_COMMENT KAN-5 --add-all-mcp
```

Register only GitHub MCP:

```bash
python3 scripts/codex_template_runner.py RCA_AND_PR_PREP KAN-5 --add-github-mcp
```

Validate Jira credentials from the configured MCP file before launching Codex:

```bash
python3 scripts/codex_template_runner.py RCA_ONLY KAN-5 --check-jira-auth --print
```

Validate GitHub credentials from the configured MCP file before launching Codex:

```bash
python3 scripts/codex_template_runner.py RCA_ONLY KAN-5 --check-github-auth --print
```

Print the rendered prompt without launching Codex:

```bash
python3 scripts/codex_template_runner.py RCA_AND_COMMENT KAN-5 --print
```

Override template variables when needed:

```bash
python3 scripts/codex_template_runner.py RCA_AND_COMMENT KAN-5 \
  --repo-scope "src/api" \
  --depth deep \
  --comment-policy "structured markdown with short sections and explicit risks"
```

The Jira MCP config file lives at:

```text
config/mcp_servers/jira.local.json
```

The runner accepts `JIRA_BASE_URL`, `JIRA_EMAIL`, and `JIRA_API_TOKEN`, then adds
the `ATLASSIAN_SITE_NAME`, `ATLASSIAN_USER_EMAIL`, and `ATLASSIAN_API_TOKEN` aliases
required by `@aashari/mcp-server-atlassian-jira` when it registers the MCP server.

An example file is also provided at:

```text
config/mcp_servers/jira.example.json
```

The GitHub MCP config file should live at:

```text
config/mcp_servers/github.local.json
```

An example remote MCP config is provided at:

```text
config/mcp_servers/github.example.json
```

Set `GITHUB_TOKEN` in `.env`; the GitHub MCP example uses GitHub's remote MCP
endpoint with `GITHUB_TOKEN` as the bearer token environment variable.

Use the Codex-only RCA-to-PR runner when you want the wrapper to fetch Jira context,
then ask Codex CLI to gather GitHub/repo context, implement code changes, and either stop at
a dry run or push/open a GitHub PR:

```bash
python3 scripts/codex_rca_pr_runner.py KAN-5 --repo org/service-a \
  --rca-only \
  --report-file rca-report.md

python3 scripts/codex_rca_pr_runner.py KAN-5 --repo org/service-a --dry-run

python3 scripts/codex_rca_pr_runner.py KAN-5 --repo org/service-a \
  --push-pr \
  --post-rca-comment \
  --post-pr-link-comment
```

The RCA-to-PR runner checks Jira and GitHub auth automatically before launching Codex
unless `--skip-jira-auth-check` or `--skip-github-auth-check` is passed. By default, it
fetches the Jira issue over REST first and injects a Jira context snapshot into the
`codex exec` prompt, so the inner Codex run does not need to call Jira MCP for reads.
Use `--skip-jira-fetch` only when you intentionally want to run without pre-fetched Jira
context. Use `--add-jira-mcp` only when you explicitly want the inner Codex run to have
Jira MCP access.
`--repo` can be `owner/repo`, a GitHub URL, or a short repo name when
`GITHUB_DEFAULT_OWNER` is set.

Use the Jira automation entrypoint when a workflow or webhook already has a Jira payload
and you do not want to pass the Jira key manually:

```bash
python3 scripts/jira_automation_codex_runner.py \
  --payload-file "$GITHUB_EVENT_PATH" \
  --report-file "$CODEX_REPORT_FILE"
```

It reads `issue.key`, `issue_key`, `jira_key`, or GitHub workflow `inputs.issue_key`.
It also supports an embedded JSON string in `inputs.jira_payload`. If no payload file is
provided, it can read JSON from stdin or use `JIRA_ISSUE_KEY` / `ISSUE_KEY`.

Example payload:

```json
{
  "issue": {
    "key": "KAN-5",
    "fields": {
      "labels": ["codex-dry-run"]
    }
  },
  "repo": "harsimratsingh113/SampleRepo",
  "repo_path": ".",
  "mode": "dry-run"
}
```

For one Jira issue that needs one consolidated RCA across multiple repositories, send `repos`
instead of `repo`:

```json
{
  "issue": {
    "key": "KAN-5"
  },
  "labels": ["codex-rca"],
  "repos": [
    {"repo": "harsimratsingh113/SampleRepo"},
    {"repo": "harsimratsingh113/another-service"}
  ]
}
```

The runner clones additional repos, passes them to the same `codex exec` run as context, and
produces one report file for the Jira issue. Private additional repos require
`MULTI_REPO_GITHUB_TOKEN` with read access.

The GitHub Actions workflow can optionally enrich RCA with external evidence. Jira does not need
to send `context_files`; GitHub repo variables decide whether the parallel collectors run:

```text
ENABLE_SNYK_CONTEXT=true
ENABLE_NEW_RELIC_CONTEXT=true
ENABLE_CONFLUENCE_CONTEXT=true
```

When enabled, the collector workflows upload markdown artifacts such as:

```text
codex-context/snyk.md
codex-context/newrelic.md
codex-context/confluence.md
```

The Codex job downloads available artifacts and passes only existing non-empty files with
`--context-file`. If the flags are missing or set to `false`, no external markdown is generated
and Codex runs exactly as before.

For New Relic infrastructure logs, set `ENABLE_NEW_RELIC_CONTEXT=true`, configure
`NEW_RELIC_API_KEY` and `NEW_RELIC_ACCOUNT_ID`, then target logs with variables such as
`NEW_RELIC_INFRA_HOSTS`, `NEW_RELIC_INFRA_ENTITY_NAMES`, `NEW_RELIC_LOG_SEARCH`,
`NEW_RELIC_SINCE`, and `NEW_RELIC_LOG_LIMIT`. Use `NEW_RELIC_NRQL` when you want to provide the
exact NRQL query yourself.

Minimal Jira payload remains unchanged:

```json
{
  "issue": {
    "key": "KAN-5"
  },
  "labels": ["codex-rca"],
  "repo": "harsimratsingh113/SampleRepo"
}
```

Additional one-off context can still be supplied with `context_files` when needed:

```json
{
  "issue": {
    "key": "KAN-5"
  },
  "labels": ["codex-rca"],
  "repo": "harsimratsingh113/SampleRepo",
  "context_files": [
    {"label": "incident timeline", "path": "codex-context/incident-timeline.md"}
  ]
}
```

Small inline `external_context` is also supported, but files are preferred for anything large or
sensitive.

Mode defaults to `dry-run`. `codex-rca` switches the automation entrypoint to `rca-only`
and posts the generated RCA report as a Jira comment. `codex-rca-only` and
`codex-rca-report` switch it to `rca-only` but keep the report as a GitHub Actions artifact
only. RCA-only mode uses a read-only Codex sandbox and does not run build, test, app, install,
restore, or formatter commands. Labels such as `codex-open-pr` or `codex-push-pr` switch it to
`push-pr`. The workflow resolves mode in this order: explicit CLI override, Codex labels,
payload `mode`, then `CODEX_MODE`. This lets an old Jira body with `"mode": "dry-run"` still
be safely overridden by the `codex-rca` label.

High-level design diagram:

```text
docs/CODEX_CLI_MCP_HLD.drawio
```

Jira Automation to GitHub Actions setup:

```text
docs/JIRA_GITHUB_ACTION_CODEX_AUTOMATION_SETUP.md
templates/github-actions/jira-codex-automation.yml
```

## Base Rules

Use these rules as the stable base for all RCA-oriented Codex CLI runs:

- Work from the Jira ticket provided in the prompt.
- Analyze the issue against the local codebase and available artifacts.
- Produce an implementation-focused RCA rather than a generic summary.
- Separate facts, assumptions, risks, and unknowns clearly.
- Prefer concise bullet points over long prose.
- Keep the output structured and consistent across runs.
- If asked to post back to Jira, format the response as clean markdown suitable for an issue
comment.
- If evidence is missing, say so explicitly rather than guessing.

## Template Variables

Use these placeholders inside the templates below:

- `<JIRA_KEY>`: Jira issue key, for example `KAN-5`
- `<REPO_SCOPE>`: Optional area of focus inside the repo, for example `billing`, `src/api`, or
  `entire repository`
- `<DEPTH>`: Analysis depth, for example `concise`, `standard`, or `deep`
- `<OUTPUT_ACTION>`: What Codex should do with the result, for example `return RCA in chat` or
  `post RCA as Jira comment`
- `<COMMENT_POLICY>`: Comment style guidance, for example `structured markdown with sections`

## Template: RCA_ONLY

Use this when you want Codex CLI to fetch the Jira issue, inspect the codebase, and return the
RCA in the terminal only.

```text
Fetch Jira issue <JIRA_KEY>.

Perform root cause analysis against the <REPO_SCOPE>.

Use this RCA structure:
1. Problem
2. Context
3. Assumptions
4. Proposed approach
5. Edge cases
6. Dependencies
7. Risks
8. Test strategy
9. Open questions

Requirements:
- Keep the analysis <DEPTH>
- Be implementation-focused
- Avoid generic filler
- Separate facts from assumptions
- Call out risks and unknowns explicitly
- Prefer bullet points over long paragraphs

Output action:
- <OUTPUT_ACTION>
```

## Template: RCA_AND_COMMENT

Use this when you want Codex CLI to perform RCA and then post the findings back to Jira in a
standardized comment format.

```text
Fetch Jira issue <JIRA_KEY>.

Perform root cause analysis against the <REPO_SCOPE>.

Use this RCA structure:
1. Problem
2. Context
3. Assumptions
4. Proposed approach
5. Edge cases
6. Dependencies
7. Risks
8. Test strategy
9. Open questions

Requirements:
- Keep the analysis <DEPTH>
- Be implementation-focused
- Avoid generic filler
- Separate facts from assumptions
- Call out risks and unknowns explicitly
- Prefer bullet points over long paragraphs

Then post the RCA findings back to the Jira ticket.

Comment policy:
- <COMMENT_POLICY>
- Keep the comment readable for engineers and reviewers
- Use short markdown sections
- Keep recommendations concrete
- State unknowns explicitly
```

## Template: RCA_DEEP_ANALYSIS

Use this when you want a more thorough RCA pass before deciding on next steps.

```text
Fetch Jira issue <JIRA_KEY>.

Perform a deep root cause analysis against the <REPO_SCOPE>.

Use this RCA structure:
1. Problem
2. Context
3. Assumptions
4. Proposed approach
5. Edge cases
6. Dependencies
7. Risks
8. Test strategy
9. Open questions

Additional expectations:
- Trace likely failure paths through the implementation
- Identify missing evidence and investigation gaps
- Distinguish confirmed findings from hypotheses
- Highlight possible regressions and validation needs
- Prefer concrete technical observations over process commentary

Output action:
- <OUTPUT_ACTION>
```

## Template: RCA_LIGHTWEIGHT

Use this for smaller tickets where you want a faster, narrower RCA pass.

```text
Fetch Jira issue <JIRA_KEY>.

Perform a lightweight RCA against the <REPO_SCOPE>.

Return only:
1. Problem
2. Most likely root cause
3. Key risks
4. Validation steps
5. Open questions

Requirements:
- Keep it brief
- Stay implementation-focused
- Do not add generic recommendations
- Call out uncertainty explicitly

Output action:
- <OUTPUT_ACTION>
```

## Template: RCA_AND_PR_PREP

Use this when you want RCA plus implementation-oriented next steps for follow-on coding work.

```text
Fetch Jira issue <JIRA_KEY>.

Perform root cause analysis against the <REPO_SCOPE>.

Use this RCA structure:
1. Problem
2. Context
3. Assumptions
4. Proposed approach
5. Edge cases
6. Dependencies
7. Risks
8. Test strategy
9. Open questions

Also include:
- likely files or modules involved
- recommended implementation direction
- validation steps before opening a PR

Requirements:
- Keep the analysis <DEPTH>
- Be implementation-focused
- Avoid generic filler
- Separate facts from assumptions
- Prefer concrete change guidance

Output action:
- <OUTPUT_ACTION>
```

## Suggested Defaults

If you do not want to decide each variable every time, use these defaults:

- `<REPO_SCOPE>` = `entire repository`
- `<DEPTH>` = `standard`
- `<OUTPUT_ACTION>` = `return RCA in chat`
- `<COMMENT_POLICY>` = `structured markdown with sections for problem, context, risks, test strategy, and open questions`

## Example Usage

Example: RCA only

```bash
codex "
Fetch Jira issue KAN-5.

Perform root cause analysis against the entire repository.

Use this RCA structure:
1. Problem
2. Context
3. Assumptions
4. Proposed approach
5. Edge cases
6. Dependencies
7. Risks
8. Test strategy
9. Open questions

Requirements:
- Keep the analysis standard
- Be implementation-focused
- Avoid generic filler
- Separate facts from assumptions
- Call out risks and unknowns explicitly
- Prefer bullet points over long paragraphs

Output action:
- return RCA in chat
"
```

Example: RCA and Jira comment

```bash
codex "
Fetch Jira issue KAN-5.

Perform root cause analysis against the entire repository.

Use this RCA structure:
1. Problem
2. Context
3. Assumptions
4. Proposed approach
5. Edge cases
6. Dependencies
7. Risks
8. Test strategy
9. Open questions

Requirements:
- Keep the analysis standard
- Be implementation-focused
- Avoid generic filler
- Separate facts from assumptions
- Call out risks and unknowns explicitly
- Prefer bullet points over long paragraphs

Then post the RCA findings back to the Jira ticket.

Comment policy:
- structured markdown with sections
- Keep the comment readable for engineers and reviewers
- Use short markdown sections
- Keep recommendations concrete
- State unknowns explicitly
"
```

## Operating Model

Use this file with a simple three-part pattern:

1. Start from the relevant named template.
2. Replace only the template variables.
3. Run the resulting prompt in Codex CLI.

This keeps Codex CLI execution prompt-based, but makes the workflow repeatable and consistent.

If you want to avoid manual substitution entirely, use:

```bash
python scripts/codex_template_runner.py <TEMPLATE_NAME> <JIRA_KEY>
```

For implementation plus PR flow, use:

```bash
python scripts/codex_rca_pr_runner.py <JIRA_KEY> --repo <OWNER/REPO> --dry-run
python scripts/codex_rca_pr_runner.py <JIRA_KEY> --repo <OWNER/REPO> --push-pr
```
