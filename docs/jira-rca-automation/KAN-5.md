## Root cause analysis
**Automated RCA (no OpenAI API key)** — heuristic summary from Jira, triage, and discovery.

Similar issues were ranked with **TF-IDF** (`--similarity tfidf`). For embedding-based ranking without OpenAI, install sentence-transformers and use `--similarity local` (uses `LOCAL_EMBEDDING_MODEL`).

**Ticket:** Change text "Hello All" to "Hello World"

This is **not** LLM-generated prose. Review `cursor_prompt.md` in artifacts or set `OPENAI_API_KEY` for full LLM RCA and code-fix PRs.

**Clone ran but no files matched** — tighten `--module-prefix` or add `--code-path` so relevant sources appear here.

### Automated code fixes and pull requests
- **Changing application source from the ticket** requires **`OPENAI_API_KEY`**: the model returns full file contents in `recommended_fixes`.
- **Opening a PR** needs **`--open-pr`** and a repo (**`--repo`** or **`GITHUB_DEFAULT_REPO`**). You can also set **`JIRA_RCA_OPEN_PR=1`** or **`RCA_OPEN_PR=1`** instead of the flag.
- **Without OpenAI**, automation only adds a **markdown doc** under `docs/jira-rca-automation/` (no source rewrites from description alone).


## Impact
Priority **Medium**, status **In Progress**. (Heuristic — verify in Jira.)

## Suspected components
- OurBudget/config
- OurBudget/src
- .vscode/settings.json
- __pycache__/multiple_aquarium_logic.cpython-312.pyc
- __pycache__/test_aquarium.cpython-312.pyc
- aquariumLogic/aquarium.db
- aquariumLogic/multiple_aquarium_logic.py
- aquariumLogic/test_aquarium.py
- aquariumLogic/testing_work.py
- .github/workflows

## Evidence from ticket
- Change text "Hello All" to "Hello World"

## Hypotheses
- Compare symptoms with top similar tickets listed below.
- Review similar resolved tickets and linked PRs for prior fixes.
- Confirm reproduction and gather logs or traces not present in the ticket.

## Recommended fixes
## Verification steps
- Reproduce using the Jira description and environment notes
- Inspect code paths suggested by similar tickets and module hints

## Risk notes
Heuristic mode: only the doc under docs/jira-rca-automation/ is proposed unless you set OPENAI_API_KEY for real code edits. Review before merge.

---
## Similar tickets (ranked)

- **KAN-4** (similarity 0.6114): Change Hello World Text to Hello All

---
## Triage excerpt

## Triage: KAN-5
**Title:** Change text "Hello All" to "Hello World"
**Type:** Bug | **Priority:** Medium | **Status:** In Progress
**Project:** KAN — Roadmap Planning
**Labels:** (none)
**Components:** (none)

### Description (plain)
Change text "Hello All" to "Hello World"

### Attachment manifest
(no attachments)

### Remote links
(none)

### Heuristic hints
(none)

---
## Prompt / code context (excerpt)

# Cursor / LLM task: Root cause analysis and fix proposal

You are assisting with **production engineering triage**. Be precise, cite evidence from the
ticket and code, and separate **facts** from **hypotheses**.

---

## Meta
- Generated at: 2026-04-09 16:22 UTC
- Current Jira: **KAN-5** — Change text "Hello All" to "Hello World"

---

## Current issue (normalized)
- **Type:** Bug
- **Priority / Status:** Medium / In Progress
- **Project:** KAN
- **Labels:** (none)
- **Components:** (none)

### Description (plain text)
Change text "Hello All" to "Hello World"

---

## Triage bundle (attachments manifest + heuristics)
## Triage: KAN-5
**Title:** Change text "Hello All" to "Hello World"
**Type:** Bug | **Priority:** Medium | **Status:** In Progress
**Project:** KAN — Roadmap Planning
**Labels:** (none)
**Components:** (none)

### Description (plain)
Change text "Hello All" to "Hello World"

### Attachment manifest
(no attachments)

### Remote links
(none)

### Heuristic hints
(none)

### Attachment / artifact note
Binary attachments (screenshots, zips) are listed in the triage bundle only. Download manually from Jira if you need vision/OCR or archive inspection.

**Auto-suggestion:** consider `--module-prefix OurBudget/config` (derived from past PRs; verify against your architecture).

---

## Similar historical Jira tickets (TF-IDF ranked)
1. [KAN-4] (similarity=0.611) Change Hello World Text to Hello All [Bug]

---

## Related GitHub pull requests (from past tickets)
- [KAN-4] KAN-4-test — https://github.com/nhcp/Git1/pull/3
- [KAN-4] KAN-4: escripcion — https://github.com/YvnPretty/practice-jira/pull/1
- [KAN-4] Joe/kan 4 — https://github.com/joe-sagayaraj/familybudget/pull/8
- [KAN-4] Kan 4 — https://github.com/ProPrif/proprif-fish/pull/3
- [KAN-4] KAN-4: Настроил GitHub Actions — https://github.com/ChedCM/urfu-project-practicum/pull/1
- [KAN-4] Feature/kan 4 — https://github.com/Pankaj4769/kosUI/pull/35
- [KAN-4] KAN-4 Nav — https://github.com/szymon-tulodziecki/jira_task/pull/7
- [KAN-4] KAN-4: инициализация тестового билда Docusaurus — https://github.com/ChedCM/urfu-project-practicum/pull/2
- [KAN-4] Kan 4 add 100 manual tests — https://github.com/WhyNotStudio-noCopyrightName/WhyNotExperience/pull/37
- [KAN-4] KAN-4 : Scanning Shape feat — https://github.com/DanCPE/sky-skills-pilot/pull/1
- [KAN-4] feat: KAN-2, KAN-3, KAN-4 - Models, API REST e Docker para sistema de reservas GOL — https://github.com/domiciano-brq/poc-gol-airline/pull/2
- [KAN-4] KAN-4: Add Spotify Top 100 analytics pipeline — https://github.com/mail2ghuman/spark-scala-maven/pull/5
- [KAN-4] Players — https://github.com/Bryanrobu/blackjack-Trainer-Project/pull/4
- [KAN-4] docs: Pipeline Architecture Overhaul — 8 specs + 8 plans + 4 reviews — https://github.com/vipulbhatia29/stock-signal-platform/pull/205
- [KAN-4] Kan 4 — https://github.com/Shikha307/JUMBLE/pull/17
- [KAN-4] Kan 4 — https://github.com/Shikha307/JUMBLE/pull/14
- [KAN-4] Kan 4 — https://github.com/Shikha307/JUMBLE/pull/12
- [KAN-4] Kan 4 — https://github.com/Shikha307/JUMBLE/pull/10
- [KAN-4] KAN-1315 (+12): Phase 59 — Surface Cleanup, Five-Field Workspace, Bulk Actions, Automation Trust, Launch Gate, Issues Action Fidelity — https://github.com/narasimhan-me/EngineO.ai/pull/94
- [KAN-4] docs: Hydrate project-plan with JIRA ticket refs + stale cleanup — https://github.com/vipulbhatia29/stock-signal-platform/pull/184
- [KAN-4] Admin create employee en parent menu navigatie — https://github.com/romanized/Ziekenhuis-Casus-Project-B/pull/5
- [KAN-4] [KAN-373] Spec C — Convergence UX (Sprints 10-13) — https://github.com/vipulbhatia29/stock-signal-platform/pull/181
- [KAN-4] Add suukantsanra (four kans abortive draw) rule — https://github.com/h1g0/riichi_mahjong_rs/pull/84
- [KAN-4] chore(main): release 1.0.0 — https://github.com/kube-rca/kuberca/pull/6
- [KAN-4] chore(main): release kuberca 1.0.0 — https://github.com/kube-rca/kuberca/pull/5
- [KAN-4] KAN-4: Add CommonPaper legal document templates dataset — https://github.com/sonukushwaha403/prelegal/pull/2
- [KAN-4] 20 us 20 onderhoud registreren en afronden medewerkerbeheerder — https://github.com/Quidney/Klusloods/pull/51
- [KAN-4] [KAN-420] Spec D PR1 — Langfuse task-tracking flags + trace_task consumer tests — https://github.com/vipulbhatia29/stock-signal-platform/pull/210
- [KAN-4] KAN-4 & KAN-7 & KAN-8 : Graph Fixes — https://github.com/VISITORS-2-0/chart-stacks-pro/pull/3
- [KAN-4] KAN-4 :: Pause menu — https://github.com/ConnorJWheatley/Godot-Stellar-Raider/pull/4

---

## Repository code context (may be partial / truncated)
(no code context collected)

---

## What to produce
1. **Root cause analysis** with clear chain from symptom → code/system behavior.
2. **Evidence vs assumptions** — label each claim.
3. **Recommended code changes** with file paths and reasoning.
4. **Test / verification plan** (including how to confirm in staging).
5. If information is missing, **list questions** for the author instead of guessing.

---

## Optional machine-readable output
If you can also emit JSON for automation, follow this schema:

Return a single JSON object with these keys:
- "root_cause_analysis": string (structured narrative for developers)
- "impact": string
- "suspected_components": array of strings
- "evidence_from_ticket": array of strings (bullet-level facts tied to the Jira text)
- "hypotheses": array of strings (ranked guesses if root cause not provable)
- "recommended_fixes": array of objects {"path": "repo-relative path", "rationale": "why", "new_content": "FULL new file content as UTF-8 text"}
- "verification_steps": array of strings (tests, logs, feature flags)
- "risk_notes": string (rollbacks, data migrations)
- "pr_title": string (concise, references Jira key)
- "branch_name": string (e.g. fix/PROJ-123-short-slug)

Rules for recommended_fixes:
- Only include files you are confident about; prefer small targeted edits.
- "new_content" must be the complete file after the fix (not a diff).
- Use paths relative to repository root exactly as shown in the code context.



---
## Metadata

- Pipeline: jira-rca-assistant heuristic RCA
- Issue: `KAN-5`
