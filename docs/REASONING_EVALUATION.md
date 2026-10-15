# Reasoning evaluation — 2026-10-14

These ten scenarios ran through actual LangGraph nodes and conditional edges, LangChain StructuredTool validation, MCP SDK stdio, temporary SQLite persistence and temporary persistent Chroma. Structured model responses were explicit deterministic fixtures. External LLM inference was not executed by this suite, so this demonstrates orchestration and evidence safeguards rather than Groq or Azure model quality. Test knowledge remained in isolated pytest directories.

| Scenario | Outcome | Status | Evidence items | Reasoning iterations | Duration |
|---|---|---|---:|---:|---:|
| Microsoft 365 MFA authentication failure | Passed | `assessment_ready` | 4 | 1 | 0.228 s |
| VPN authentication failure | Passed | `assessment_ready` | 9 | 3 | 0.459 s |
| Network printer unavailable | Passed | `assessment_ready` | 4 | 1 | 0.166 s |
| Active Directory account lockout | Passed | `assessment_ready` | 5 | 1 | 0.197 s |
| Outlook connectivity failure | Passed | `assessment_ready` | 5 | 2 | 0.339 s |
| Multiple users reporting internet outages | Passed | `assessment_ready` | 2 | 1 | 0.231 s |
| Application access denied | Passed | `assessment_ready` | 4 | 1 | 0.241 s |
| Corporate Wi-Fi connection failure | Passed | `assessment_ready` | 2 | 1 | 0.162 s |
| Contradictory knowledge-base troubleshooting instructions | Passed | `assessment_ready` | 4 | 2 | 0.172 s |
| Incident with incomplete information and no matching knowledge | Passed | `needs_input` | 0 | 1 | 0.152 s |

Every scenario executed classification, ticket analysis, plan generation, evidence collection, reasoning and verification. Diagnostic calls went through dynamically discovered MCP schemas and LangChain tools. Recommendations reference retrieved evidence IDs; no case was declared resolved. The incomplete incident returned a real LangGraph human-input interrupt with zero confidence and no invented diagnosis.

The contradictory-KB scenario supplied two different stored articles. Its verifier rejected an unsupported certificate-validation recommendation, triggered another reasoning pass and retained the contradiction in the final result. The resulting recommendation asks the policy owner to reconcile the sources while preserving certificate validation.

Corporate Wi-Fi explicitly considered graph relationships. No live Neo4j availability is claimed by this suite; vector-only fallback is tested separately.

Per-scenario model token usage and estimated inference cost are **unavailable**, because the model is a fixture. No paid inference requests ran. The detailed runtime report records actual stages, MCP calls, evidence references, rejected claims, durations and null token/cost fields.

Reproduce with:

```bash
REASONING_MATRIX_REPORT=/private/tmp/deskmate-reasoning-evaluation.json \
  .venv/bin/python -m pytest backend/app/tests/test_architecture_reasoning_matrix.py -q
```

The runtime report contains isolated test identifiers and is created with private file permissions. Live provider, real microphone STT and actual Azure resource checks require separate verification and must not be inferred from these results.


## Live Groq verification

Real Groq inference also ran for all ten scenarios, using `openai/gpt-oss-120b`, actual LangGraph/MCP, fresh empty Chroma/SQLite, disabled fixtures, no private Aura and no external service-desk connections. Every final scenario requested necessary information. All ten passed the **limited missing-evidence/provenance checks**; this does not establish diagnostic accuracy against connected business systems.

| Scenario | Final state | Diagnostic evidence | Verifier unsupported claims | Tokens | Seconds |
|---|---|---:|---:|---:|---:|
| Microsoft 365 MFA authentication failure | `needs_input` | 0 | 0 | 11,651 | 48.289 |
| VPN authentication failure | `needs_input` | 0 | 0 | 10,348 | 90.367 |
| Network printer unavailable | `needs_input` | 0 | 0 | 11,197 | 72.543 |
| Active Directory account lockout | `needs_input` | 0 | 0 | 11,264 | 88.053 |
| Outlook connectivity failure | `needs_input` | 0 | 0 | 8,317 | 70.569 |
| Multiple users reporting internet outages | `needs_input` | 0 | 0 | 9,199 | 67.881 |
| Application access denied | `needs_input` | 0 | 0 | 10,213 | 90.476 |
| Corporate Wi-Fi connection failure | `needs_input` | 0 | 0 | 8,112 | 56.795 |
| Contradictory knowledge-base troubleshooting instructions | `needs_input` | 0 | 0 | 6,022 | 10.709 |
| Incomplete information with no matching knowledge | `needs_input` | 0 | 0 | 8,269 | 74.940 |

Each final run executed real planning, MCP discovery, retrieval, reasoning and verification, followed by a LangGraph human-input interrupt. Source absence was explicit. Unconnected organization tools returned unavailable, not business records. An evaluator metric initially counted these unavailable wrappers as fixture evidence; final metrics were recomputed from the exact preserved case states without further inference. Their contents were only availability notices. The initial reports remain privately preserved.

The first conflicting-KB run incorrectly used general chat and skipped retrieval. Classification instructions were corrected to route internal-KB comparisons through retrieval and verification, then that scenario was rerun live successfully. No source articles were injected into this live test; the deterministic test above separately verifies actual contradictory retrieved articles.

The final ten runs used **94,592 reported tokens** and **670.622 seconds** in aggregate. Provider capacity waits account for much of the duration. Groq pricing is unconfigured, so the API-cost estimate is unknown, not assumed zero. Initial failed requests and the initial incorrect routing run are excluded from these final-run totals and retained in private reports.

A tiny Azure OpenAI structured-text connectivity probe passed with Entra authentication. The full Azure investigation matrix was **not run**, because the unchanged CAD155 allowance has only a few cents remaining. No ledger reset or allowance increase was performed. Aura remains disabled at the user's request because the saved hostname no longer resolves. No physical microphone acceptance is claimed.
