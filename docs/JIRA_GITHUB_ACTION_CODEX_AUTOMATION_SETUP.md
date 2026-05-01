# Jira To GitHub Action Codex Automation Setup

This flow lets Jira Automation trigger a GitHub Actions workflow. The workflow runs on a
GitHub runner, calls `scripts/jira_automation_codex_runner.py`, and the wrapper fetches Jira
context before launching `codex exec`.

```text
Jira label added
  -> Jira Automation send web request
  -> GitHub repository_dispatch or workflow_dispatch
  -> GitHub Actions workflow
  -> self-hosted runner
  -> scripts/jira_automation_codex_runner.py
  -> scripts/codex_rca_pr_runner.py
  -> Jira REST fetch
  -> codex exec
  -> dry-run summary or PR
```

## GitHub Repository Setup

Copy these files into the target repo that Codex should inspect and edit:

```text
scripts/jira_automation_codex_runner.py
scripts/codex_rca_pr_runner.py
scripts/codex_template_runner.py
scripts/prepare_external_context.py
config/mcp_servers/jira.example.json
config/mcp_servers/github.example.json
```

Copy the workflow template into the target repo:

```bash
mkdir -p .github/workflows
cp templates/github-actions/jira-codex-automation.yml \
  .github/workflows/jira-codex-automation.yml
```

Commit and push the workflow file to the default branch. GitHub will not trigger a
`workflow_dispatch` or `repository_dispatch` workflow unless the workflow file exists on the
default branch.

Create these GitHub repository secrets:

```text
JIRA_BASE_URL
JIRA_EMAIL
JIRA_API_TOKEN
```

For testing, the workflow defaults to ChatGPT-managed Codex CLI auth on the self-hosted
runner. That means you do not need `OPENAI_API_KEY` or `CODEX_API_KEY` if the runner user is
already logged in with Codex CLI.

On the self-hosted runner machine, log in as the same OS user that runs the GitHub Actions
runner service, then run:

```bash
codex login
codex login status
```

Expected status:

```text
Logged in using ChatGPT
```

If your runner runs as a service user, do the login as that service user. A successful login
under your personal shell user does not help if the GitHub Actions runner service runs as a
different user with a different home directory.

For production-style API key auth later, add one of these repository secrets:

```text
CODEX_API_KEY
OPENAI_API_KEY
```

Then set this GitHub repository variable:

```text
CODEX_AUTH_MODE=api-key
```

The Jira-to-GitHub dispatch token is not a GitHub Actions secret. Store that token in Jira
Automation or Jira's secret/connection mechanism.

For self-hosted runner mode, register a runner with labels matching the workflow:

```text
self-hosted
macOS
X64
codex-jira-runner
```

The runner must be able to reach:

```text
GitHub over HTTPS
OpenAI/Codex service over HTTPS
Jira over HTTPS or VPN/private network
```

## Recommended Jira Trigger: repository_dispatch

Use this when Jira sends a normal JSON payload to GitHub.

GitHub endpoint:

```text
POST https://api.github.com/repos/<owner>/<repo>/dispatches
```

GitHub token for Jira:

```text
Fine-grained PAT or GitHub App installation token
Repository: selected target repo
Permission: Contents write
```

Jira Automation rule from UI:

```text
Rule name: Codex: dispatch GitHub Action
Trigger: Field value changed
Field: Labels
Change type: Value added
For: Edit issue
```

Add conditions:

```text
JQL condition: project = KAN
JQL condition: labels not in (codex-disabled)

Advanced compare condition:
First value: {{fieldChange.toString}}
Condition: matches regular expression
Second value: .*codex-(rca|rca-only|dry-run|open-pr|push-pr|pr).*
```

Mode labels:

```text
codex-rca       -> RCA report artifact plus Jira RCA comment; no edits, no builds/tests, no branch, no PR
codex-rca-only  -> RCA report artifact only; no Jira comment, no edits, no builds/tests, no branch, no PR
codex-dry-run   -> RCA plus implementation dry-run; no push/PR
codex-open-pr   -> RCA plus implementation and PR
```

Action: Send web request

```text
Web request URL: https://api.github.com/repos/<owner>/<repo>/dispatches
HTTP method: POST
Web request body: Custom data
Delay execution: unchecked
Wait for response: checked
```

Headers:

```text
Authorization: Bearer <jira-stored-github-token>
Accept: application/vnd.github+json
Content-Type: application/json
X-GitHub-Api-Version: 2022-11-28
```

Label-driven request body:

Use this when one Jira rule should support `codex-rca`, `codex-dry-run`, and
`codex-open-pr`. Notice that `mode` is omitted so the runner can infer the mode from the
label that was just added. This body intentionally avoids `{{issue.labels.asJsonStringArray}}`
because some Jira Automation validations render that smart value in a GitHub-incompatible way.

```json
{
  "event_type": "jira-codex",
  "client_payload": {
    "issue": {
      "key": "{{issue.key}}"
    },
    "labels": ["{{fieldChange.toString}}"],
    "repo": "<owner>/<repo>",
    "repo_path": ".",
    "depth": "standard"
  }
}
```

Multi-repo RCA scan request body:

Use `repos` when one Jira issue needs a single consolidated RCA report that scans multiple
repositories. The workflow still produces one report artifact and, for `codex-rca`, one Jira
comment. Evidence should be grouped by repository inside the same report.

```json
{
  "event_type": "jira-codex",
  "client_payload": {
    "issue": {
      "key": "{{issue.key}}"
    },
    "labels": ["{{fieldChange.toString}}"],
    "repos": [
      {
        "repo": "harsimratsingh113/SampleRepo"
      },
      {
        "repo": "harsimratsingh113/another-service"
      }
    ],
    "depth": "standard"
  }
}
```

The first repo is treated as the primary repo. Additional repos are cloned into the runner
workspace and passed to the same Codex run as read-only context. For private repos outside the
current GitHub repository, add this GitHub Actions secret with read access to those repos:

```text
MULTI_REPO_GITHUB_TOKEN
```

Multi-repo scanning is intended for `codex-rca`, `codex-rca-only`, and `codex-dry-run`. For
`codex-open-pr`, use a single repo so the automation does not accidentally create or coordinate
changes across multiple repositories.

## Additional Data Source Context

The workflow owns the standard external evidence bundle. Jira does not need to decide whether
Snyk, New Relic, or Confluence context should be included.

The recommended pattern is:

```text
external tool API/CLI
  -> workflow step writes sanitized markdown/json into codex-context/
  -> workflow passes the standard files to Codex
  -> Codex scans repos + standard external context in one run
  -> one consolidated RCA report for the Jira issue
```

The Jira Automation payload can stay minimal:

```json
{
  "event_type": "jira-codex",
  "client_payload": {
    "issue": {
      "key": "{{issue.key}}"
    },
    "labels": ["{{fieldChange.toString}}"],
    "repos": [
      {
        "repo": "harsimratsingh113/SampleRepo"
      }
    ],
    "depth": "standard"
  }
}
```

The workflow always prepares and passes these standard files:

```text
codex-context/snyk.md
codex-context/newrelic.md
codex-context/confluence.md
```

`scripts/prepare_external_context.py --default-files` creates those files as safe placeholders
when source-specific data has not been populated yet. If you want real Snyk/New Relic/Confluence
evidence, add collection steps before the Codex step that overwrite the placeholders with
sanitized content.

Example source-specific collection placement:

```yaml
- name: Prepare external context files
  run: |
    mkdir -p "$CODEX_CONTEXT_DIR"
    python3 scripts/prepare_external_context.py \
      --payload-file "$GITHUB_EVENT_PATH" \
      --context-dir "$CODEX_CONTEXT_DIR" \
      --default-files

- name: Collect Snyk context
  if: ${{ secrets.SNYK_TOKEN != '' }}
  run: |
    {
      echo "# Snyk vulnerabilities"
      echo
      echo "Populate this step with your approved Snyk CLI/API query."
    } > codex-context/snyk.md

- name: Collect New Relic context
  if: ${{ secrets.NEW_RELIC_API_KEY != '' }}
  run: |
    {
      echo "# New Relic logs"
      echo
      echo "Populate this step with your approved NRQL/log query."
    } > codex-context/newrelic.md

- name: Collect Confluence context
  if: ${{ secrets.CONFLUENCE_API_TOKEN != '' }}
  run: |
    {
      echo "# Confluence runbook"
      echo
      echo "Populate this step with your approved Confluence page export."
    } > codex-context/confluence.md

- name: Run Jira-triggered Codex automation
  run: |
    python3 scripts/jira_automation_codex_runner.py \
      --payload-file "$GITHUB_EVENT_PATH" \
      --report-file "$CODEX_REPORT_FILE" \
      --context-file "Snyk vulnerabilities|$CODEX_CONTEXT_DIR/snyk.md" \
      --context-file "New Relic logs|$CODEX_CONTEXT_DIR/newrelic.md" \
      --context-file "Confluence runbook|$CODEX_CONTEXT_DIR/confluence.md" \
      --post-rca-comment-for-rca-only
```

Recommended GitHub secrets/variables:

```text
SNYK_TOKEN
NEW_RELIC_API_KEY
NEW_RELIC_ACCOUNT_ID
CONFLUENCE_BASE_URL
CONFLUENCE_EMAIL
CONFLUENCE_API_TOKEN
```

Keep these exports small and sanitized:

```text
Include: issue-relevant vulnerabilities, service/entity names, timestamps, error summaries,
runbook snippets, known incidents, linked pages, and relevant log samples.

Exclude: tokens, cookies, raw PII, customer secrets, full production payloads, and unrelated logs.
```

You can still send extra one-off files or small inline context directly in the payload. Treat this
as an exception path for evidence that is not part of the standard bundle.

```json
{
  "event_type": "jira-codex",
  "client_payload": {
    "issue": {
      "key": "{{issue.key}}"
    },
    "labels": ["codex-rca"],
    "repo": "harsimratsingh113/SampleRepo",
    "external_context": {
      "newrelic-summary": "Error rate spiked on checkout-api between 10:02 and 10:17 UTC.",
      "snyk-summary": "No critical vulnerabilities found in the touched module."
    },
    "depth": "standard"
  }
}
```

Inline context is written to `codex-context/*.md` by the runner and included in the same RCA
report. Use files instead of inline context for anything large or sensitive.

Explicit dry-run request body:

Use this only when the rule should always run dry-run regardless of labels.

```json
{
  "event_type": "jira-codex",
  "client_payload": {
    "issue": {
      "key": "{{issue.key}}"
    },
    "labels": ["codex-dry-run"],
    "repo": "<owner>/<repo>",
    "repo_path": ".",
    "mode": "dry-run",
    "depth": "standard"
  }
}
```

Explicit RCA-only request body:

```json
{
  "event_type": "jira-codex",
  "client_payload": {
    "issue": {
      "key": "{{issue.key}}"
    },
    "labels": ["codex-rca"],
    "repo": "<owner>/<repo>",
    "repo_path": ".",
    "mode": "rca-only",
    "depth": "standard"
  }
}
```

The script extracts `client_payload.issue.key`, so no one manually passes the Jira key to the
CLI. Jira injects the active issue key into the payload.

## Alternative Trigger: workflow_dispatch

Use this if you want to call a named workflow file directly.

GitHub endpoint:

```text
POST https://api.github.com/repos/<owner>/<repo>/actions/workflows/jira-codex-automation.yml/dispatches
```

GitHub token for Jira:

```text
Fine-grained PAT or GitHub App installation token
Repository: selected target repo
Permission: Actions write
```

Request body:

```json
{
  "ref": "main",
  "inputs": {
    "issue_key": "{{issue.key}}",
    "repo": "<owner>/<repo>",
    "mode": "dry-run",
    "depth": "standard"
  }
}
```

For PR mode, set:

```json
"mode": "push-pr"
```

For RCA report only, set:

```json
"mode": "rca-only"
```

## GitHub Workflow Template

The workflow template is available at:

```text
templates/github-actions/jira-codex-automation.yml
```

The important step is:

```bash
python3 scripts/jira_automation_codex_runner.py \
  --payload-file "$GITHUB_EVENT_PATH" \
  --report-file "$CODEX_REPORT_FILE" \
  --post-rca-comment-for-rca-only
```

`GITHUB_EVENT_PATH` contains either `client_payload` from `repository_dispatch` or `inputs`
from `workflow_dispatch`. The automation runner reads that payload and delegates to the Codex
RCA/PR runner.

The workflow uploads the final Codex message as a GitHub Actions artifact:

```text
codex-rca-report-<run-id>
```

The report file is written to:

```text
codex-output/codex-rca-report.md
```

The workflow never attaches the markdown file to Jira. The report file is attached only to the
GitHub Actions runner job as an artifact.

For the `codex-rca` label, the workflow also posts the RCA report text as a Jira comment. For
the `codex-rca-only` label, it does not post a Jira comment and only uploads the runner artifact.
Both labels run Codex in RCA-only mode with a read-only sandbox and explicitly skip build, test,
run, install, restore, and formatter commands. The report includes recommended validation instead
of executed validation results.

For Jira Cloud, keep:

```text
JIRA_COMMENT_API_VERSION=3
```

For Jira Server/Data Center, set this GitHub repository variable:

```text
JIRA_COMMENT_API_VERSION=2
```

## Validation

From Jira Automation, click **Validate** on the web request action. Expected results:

```text
repository_dispatch: 204 No Content
workflow_dispatch: 204 No Content, or 200 if the GitHub API returns run details
```

Then check GitHub:

```text
Repository -> Actions -> Jira Codex Automation
```

If the run starts but fails immediately, check:

```text
Missing GitHub secrets
Workflow file not on default branch
Self-hosted runner labels do not match
Jira token lacks Contents write or Actions write
Jira smart value JSON is invalid
```
