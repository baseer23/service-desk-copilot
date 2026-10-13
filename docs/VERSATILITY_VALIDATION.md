# DeskMate versatility validation

Historical results below predate the Azure-only browser voice migration. They do
not establish live Azure acceptance. Current results are in [VOICE_REVIEW.md](VOICE_REVIEW.md).

Validation date: 2026-10-09.

Baseline commit: `1b767b18577de45b76fde911aa8e9359ef16b9f2` (`Generalize DeskMate MCP investigation platform`), pushed to GitHub main after the later request to save on GitHub.

**Overall verdict: FAIL / live model validation incomplete.** The deterministic platform suite passes and the real public-network smoke passes. All three final previous hosted provider attempts encounter HTTP 429 TPD limits; they are not counted as successful live scenarios. Earlier live attempts also exposed weak hypotheses and repeated rejected MFA location questions. The revision fix is covered by a regression test but cannot be fully revalidated on previous hosted provider under the exhausted quota.

## Scenario scorecard

These ten rows use canned structured model responses and mocked external network adapters, while executing real LangGraph subgraphs, real LangChain `StructuredTool.ainvoke`, official MCP SDK stdio, repositories, temporary SQLite and temporary Chroma. Model response fixtures live only in tests; no scenario dispatch was added to the production agent.

| # | Scenario | Intent | Tools Used (diagnostic reads/research) | Human Input | Result | Pass |
|---|----------|--------|------------|-------------|--------|------|
| 1 | VPN | service_desk | search_memory, get_user_context, search_previous_tickets, search_knowledge, get_active_alerts, get_recent_changes, get_site_context | Which site is Jake working from right now?; Has Jake installed the required VPN client update? | Install the required approved VPN client update, refresh the profile, retry and retain authentication logs if failure persists. | PASS |
| 2 | MFA | service_desk | search_memory, get_user_context, search_previous_tickets, search_knowledge | None | Verify Sarah's identity, then have an authorized administrator reset the old authentication methods and complete Authenticator re-registration. | PASS |
| 3 | PRINTER | service_desk | get_device_context, search_previous_tickets, search_knowledge | None | Confirm the physical jam is cleared, inspect and resume PRN-ACCT-02's queue under policy, then print a test page. | PASS |
| 4 | MULTI-USER OUTAGE | service_desk | search_knowledge, get_company_context, get_active_alerts, get_recent_changes, search_previous_tickets | Which client company is affected? | Link the five reports to the shared VPN incident and ask network operations to compare gateway authentication logs with the certificate rotation. | PASS |
| 5 | ACCOUNT LOCKOUT | service_desk | search_memory, get_user_context, get_device_context, search_previous_tickets, search_knowledge | None | Review lockout events for WIN-MICHAEL-01, identify the credential consumer, and update only the verified stale saved credential or mapped-drive entry. | PASS |
| 6 | OUTLOOK | service_desk | search_memory, get_user_context, search_previous_tickets, search_knowledge | Do new messages appear in Priya's webmail? | Since webmail receives new messages, inspect Outlook view filters, offline state and synchronization before considering profile repair. | PASS |
| 7 | DEVICE PERFORMANCE | service_desk | search_memory, get_user_context, get_device_context, get_recent_changes, search_previous_tickets, search_knowledge | None | Monitor Windows Modules Installer activity and update progress, retain disk diagnostics and perform an approved restart only after installation completes. | PASS |
| 8 | PERMISSIONS | service_desk | search_memory, get_user_context, search_previous_tickets, search_knowledge, search_graph | None | Ask the Budget folder owner to verify effective folder permissions and approved group membership before granting any access. | PASS |
| 9 | APPLICATION | service_desk | search_memory, get_user_context, get_device_context, search_previous_tickets, search_knowledge | None | Test Autotask in a clean private browser session, then isolate site storage/extensions under policy and retain browser console errors if it persists. | PASS |
| 10 | WEB RESEARCH | knowledge_question | search_knowledge, search_web, fetch_web_page, ingest_text | None | Have an authorized tenant administrator review sign-in logs and the Conditional Access policy result; do not disable the policy based on the error alone. | PASS |

Each operational result has plausible evidence-cited hypotheses, separate inferences and confirmed facts, a grounded next action and preserved causal uncertainty. Maria’s older fictional memory says Fabrikam while current user context says Northwind; the discrepancy and both timestamps are surfaced. The follow-up VPN case reuses human-confirmed site/update facts without another interrupt.

## Coverage

- Unique MCP tools: 19: `fetch_web_page`, `get_active_alerts`, `get_case`, `get_case_history`, `get_company_context`, `get_device_context`, `get_recent_changes`, `get_site_context`, `get_user_context`, `ingest_text`, `list_available_context`, `remember_fact`, `save_case`, `save_case_note`, `search_graph`, `search_knowledge`, `search_memory`, `search_previous_tickets`, `search_web`.
- Different diagnostic tool sets: 8 across ten support/research categories.
- Completed without unnecessary human questions: 10/10; unnecessary questions: 0.
- Completed without any human question: 7/10.
- Human interrupt/resume: 3 cases, four material questions (VPN site/update, outage company, Outlook webmail).
- Public research: 1 scenario; a separate live-network execution also passes.
- Memory: 2 scenarios (VPN follow-up reuse and seeded older Maria record).
- Chroma: 10 scenarios; exact-code retrieval is separately tested in uppercase and lowercase against distractors.
- Neo4j/graph context: one search_graph attempt, explicit unavailable result; 0 connected Aura scenarios. This is not evidence of live Aura behavior.
- Alerts/recent changes: 3 scenarios (VPN, shared outage, device updates).

## Additional protections

- Uploaded/indexed fictional Abdul resume PDF: PASS. `/ask` classifies `knowledge_question`, answers 2022 from the extracted PDF, creates no case, writes no case notes and asks no operational question.
- Unknown Contoso production domain-controller address: PASS under controlled model responses. Empty internal evidence produces insufficient information, confidence 0, no address and no fabricated source.
- Failed web fetch: PASS. Actual MCP tool failure is retained, no public document is indexed/cited as fetched evidence, and the final answer is withheld with confidence 0.

## Live configured previous hosted provider

Model: configured `previous configured model`; temporary runtime: `/var/folders/22/x12ctqgx7hjc76z7v_7ryxn00000gn/T/deskmate-live-previous_hosted_provider-7rv5xhx7`. Final attempts use the configured provider with no stub fallback. Validation-only pacing and durable checkpoint retries were used for TPM limits; TPD limits remain external blockers.

| Scenario | Result | Evidence |
|---|---|---|
| VPN | FAIL / UNAVAILABLE | classify_intent, analyze_ticket; previous hosted provider request failed or returned no message (HTTP 429, model previous configured model) (TPD quota limit); retry after 552 seconds |
| MFA | FAIL / UNAVAILABLE | classify_intent; previous hosted provider request failed or returned no message (HTTP 429, model previous configured model) (TPD quota limit); retry after 86 seconds |
| PRINTER | FAIL / UNAVAILABLE | classify_intent; previous hosted provider request failed or returned no message (HTTP 429, model previous configured model) (TPD quota limit); retry after 211 seconds |

Earlier previous hosted provider runs genuinely executed planning/reasoning/verifying but did not satisfy the quality checks: VPN returned a next action without a hypothesis/KB retrieval, MFA repeated a rejected location premise, and the printer asked a bundled question and asserted uncertain jam causation. These results are retained as failures, not counted as proofs of intelligence. Provider revision instructions now explicitly prioritize application validation and preserve plausible hypotheses; the post-fix live pass is still outstanding.

## Real public web

PASS. The full agent used normal MCP `search_web` and `fetch_web_page` against actual public search services and the Microsoft Entra error-code reference, reviewed useful fetched text, called MCP `ingest_text`, and retrieved the exact AADSTS53003 chunk from real sentence-embedding Chroma with URL and document ID provenance. Only structured model responses were controlled; DNS, HTTP, page extraction, ingestion and retrieval were real.

Observed source: https://learn.microsoft.com/en-us/entra/identity-platform/reference-error-codes

## Validation fixes

- Added generic fictional multi-company/user/device/ticket/alert/change data and data-driven repository queries; every fixture result is labeled `source_system=local_fixture`.
- `/ask` classifies before case creation; the UI and Vite proxy use it. Knowledge requests retain graph execution/activity without creating operational case records.
- Fresh user context is compared with older remembered facts, preserving contradictions.
- Revision no longer repeats a rejected critic premise as guidance; application validation has explicit priority.
- Public HTML extraction preserves table rows, bounded model excerpts retain matching passages, and the existing Retriever preserves exact opaque identifiers before semantic/graph filtering.
- No agent repository calls, vendor workflows or production scenario-specific branches were added. Original user stores and `.env` are unchanged; validation uses temporary stores and disables live Aura writes.

## Checks

- Dedicated verbose validation suite: 14 passed (ten scenarios, resume, unknown IP, failed fetch, coverage matrix).
- Full backend suite: 109 passed, 0 failed, 2 optional Aura skips.
- Frontend unit tests: 12 passed, 0 failed, including empty/invalid responses, connection failure, retry recovery and provider-error case restoration.
- Frontend TypeScript/Vite build: PASS.
- Playwright E2E: 5 passed, 0 failed, including an empty proxy response followed by a successful retry through the real MCP agent.
- Ruff undefined/unused-symbol checks: PASS.

## Reproduce

```bash
.venv/bin/python -m pytest backend/app/tests -q
SCENARIO_REPORT=/private/tmp/deskmate-scenario-report.json .venv/bin/python -m pytest backend/app/tests/test_service_desk_scenarios.py -vv -s
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run test:e2e
.venv/bin/python -m backend.app.tests.run_live_versatility --mode azure_openai --report /private/tmp/deskmate-live-previous_hosted_provider.json --pace-seconds 30
.venv/bin/python -m backend.app.tests.run_live_versatility --mode azure_openai --report /private/tmp/deskmate-live-previous_hosted_provider.json --resume --pace-seconds 30
.venv/bin/python -m backend.app.tests.run_live_versatility --mode web --report /private/tmp/deskmate-live-web.json
```

Runtime data, logs and artifacts are excluded from Git. The scenario suite, fixtures, fixes and this report are included following the user's 2026-10-10 request to save and commit all pending work.

## Evidence artifacts

- `/private/tmp/deskmate-scenario-report.json` and `/private/tmp/deskmate-scenarios.log`
- `/private/tmp/deskmate-live-previous_hosted_provider.json` (earlier quality failures) and `/private/tmp/deskmate-live-previous_hosted_provider-final.json` (post-fix quota blockers)
- `/private/tmp/deskmate-live-web-passed.json` and `/private/tmp/deskmate-live-web-passed.log`
- `/private/tmp/deskmate-backend-full.log`, `/private/tmp/deskmate-frontend-tests.log`, `/private/tmp/deskmate-frontend-build.log`, `/private/tmp/deskmate-e2e.log`
- Latest response-error regression: `/private/tmp/deskmate-response-tests.log`, `/private/tmp/deskmate-response-build.log`, `/private/tmp/deskmate-response-e2e.log`

## Running application

- Frontend: http://127.0.0.1:5174
- Backend: http://127.0.0.1:8000
- Generation remains configured previous hosted provider; new investigations may return its daily quota error until capacity is available. MCP and Chroma health are independent of hosted generation quota.
