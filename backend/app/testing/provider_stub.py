"""Deterministic test fixtures; live reasoning uses Azure."""

import re
from typing import Any


class StubProvider:
    def name(self) -> str:
        return "stub"

    async def generate_structured(self, stage: str, context: dict[str, Any], schema: Any) -> Any:
        text = context.get("request") or context.get("ticket", {}).get("summary", "")
        lower = text.lower()
        if stage == "classify_intent":
            unrelated = any(
                term in lower
                for term in (
                    "immigration",
                    "sports score",
                    "restaurant recommendation",
                    "political news",
                    "election news",
                )
            )
            knowledge = any(
                t in lower
                for t in (
                    "graduate",
                    "resume",
                    "skills",
                    "summarize",
                    "guidance",
                    "document",
                    "policy",
                )
            )
            return schema.model_validate(
                {
                    "workflow": "knowledge" if knowledge or unrelated else "service_desk",
                    "in_scope": not unrelated,
                    "goal": text,
                    "justification": "Explicit test intent fixture",
                }
            )
        if stage == "conversation_interpret":
            return schema.model_validate(
                {
                    "intent": "general",
                    "operations": [],
                    "needs_clarification": True,
                    "clarification": "Which exact characters should I change?",
                }
            )
        if stage == "general_chat":
            return schema.model_validate(
                {"answer": "I can help troubleshoot that. What error do you see?"}
            )
        if stage == "analyze_ticket":
            match = re.search(r"([A-Z][a-z]+ [A-Z][a-z]+) from ([\w-]+)", text)
            user = (
                match[1]
                if match
                else "Sarah"
                if "sarah" in lower
                else "Jake"
                if "jake" in lower
                else None
            )
            company = match[2] if match else "B99" if "b99" in lower else None
            device = "PRN-ACCT-02" if "prn-acct-02" in lower else None
            return schema.model_validate(
                {
                    "user_name": user,
                    "company": company,
                    "device": device,
                    "product": (
                        "VPN"
                        if "vpn" in lower
                        else "Microsoft 365"
                        if "mfa" in lower
                        else "Printer"
                        if device
                        else None
                    ),
                    "issue_type": (
                        "authentication"
                        if "vpn" in lower or "mfa" in lower
                        else "printing"
                        if device
                        else "unknown"
                    ),
                    "symptoms": text,
                    "affected_scope": "multiple users" if "five" in lower else "individual",
                    "entities": [x for x in (user, company, device) if x],
                }
            )
        evidence = context.get("evidence", []) or []
        analysis = context.get("analysis", {}) or {}
        facts = {
            f["predicate"]: f["value"] for f in reversed(context.get("remembered_facts") or [])
        }
        facts.update({f["predicate"]: f["value"] for f in context.get("confirmed_facts") or []})

        def call(name: str, **args: Any) -> dict[str, Any]:
            return {
                "name": name,
                "arguments": args,
                "reason": "Gather evidence needed for this test decision",
            }

        observed_company = next(
            (
                e["content"].get("company")
                for e in evidence
                if e["tool"] == "get_user_context" and e["content"].get("company")
            ),
            None,
        )
        company = analysis.get("company") or facts.get("company") or observed_company
        if stage == "plan":
            if context["workflow"] == "knowledge":
                calls = [call("search_knowledge", question=text)]
            elif analysis.get("device"):
                calls = [
                    call("get_device_context", device_or_user=analysis["device"]),
                    call("search_knowledge", question=text),
                ]
            elif analysis.get("product") == "Microsoft 365":
                calls = [
                    call("get_user_context", user_name=analysis["user_name"]),
                    call("search_knowledge", question=text),
                ]
            elif company:
                site = facts.get("current_site")
                if site:
                    calls = [
                        call("get_site_context", company=company, site=site),
                        call("get_active_alerts", company=company, site=site),
                        call("get_recent_changes", company=company, site=site),
                        call("search_previous_tickets", query=text, company=company, site=site),
                    ]
                else:
                    calls = [
                        call("get_active_alerts", company=company),
                        call("get_recent_changes", company=company),
                        call("search_previous_tickets", query=text, company=company),
                        call("get_company_context", company=company),
                        call("search_knowledge", question=text),
                    ]
                    if analysis.get("user_name"):
                        calls.append(
                            call(
                                "get_user_context", user_name=analysis["user_name"], company=company
                            )
                        )
            elif analysis.get("user_name"):
                calls = [
                    call("get_user_context", user_name=analysis["user_name"]),
                    call("search_knowledge", question=text),
                ]
            else:
                calls = [call("search_knowledge", question=text)]
            return schema.model_validate(
                {
                    "goal": text,
                    "tool_calls": calls,
                    "parallel_groups": [list(range(len(calls)))],
                    "stop_condition": (
                        "Enough evidence to form a candidate answer or material question"
                    ),
                }
            )
        if stage == "evaluate_sources":
            return schema.model_validate(
                {
                    "selected_urls": [r["url"] for r in context["results"][:2]],
                    "directly_relevant": True,
                    "evidence_gap": text,
                    "justification": "Explicit test source selection",
                }
            )
        if stage == "verify":
            decision = context["decision"]
            ids = [
                e["id"]
                for e in evidence
                if e["source_type"] != "tool_error"
                and e["tool"] not in {"search_web", "list_available_context", "user_request"}
            ]
            if decision.get("question_for_human") and not ids:
                ids = [e["id"] for e in evidence if e["tool"] == "user_request"]
            return schema.model_validate(
                {
                    "supported_claims": (
                        [
                            decision.get("answer")
                            or decision.get("recommended_next_action")
                            or "No supported claim"
                        ]
                        if ids
                        else []
                    ),
                    "unsupported_claims": [] if ids else ["No supporting evidence"],
                    "evidence_ids": ids,
                    "confidence": 0.75 if ids else 0,
                    "should_continue_research": False,
                    "justification": (
                        "Independent test verification checked the supplied evidence identifiers"
                    ),
                }
            )
        base = {
            "evidence_summary": "Assessing observed evidence; causation remains unconfirmed.",
            "enough_information": False,
            "confidence": 0,
            "uncertainty": ["No confirmed root cause."],
        }
        documents = [
            e
            for e in evidence
            if e["tool"] == "search_knowledge" and e["source_type"] != "tool_error"
        ]
        if context["workflow"] == "knowledge":
            source = "\n".join(e["content"].get("text", "") for e in documents)
            if source:
                year = re.search(
                    r"(?:graduat\w*|education)[^\n.]*?\b((?:19|20)\d{2})\b", source, re.I
                )
                answer = (
                    f"Abdul graduated in {year[1]}."
                    if "graduate" in lower and year
                    else source.strip()
                )
                base.update(
                    answer=answer,
                    enough_information=bool(year) if "graduate" in lower else True,
                    confidence=0.75,
                    recommended_next_action="Review the cited document for context.",
                )
            elif not context.get("research_rounds"):
                base.update(
                    needs_web_research=True,
                    research_queries=[text],
                    preferred_domains=["learn.microsoft.com"] if "microsoft" in lower else [],
                )
        elif analysis.get("affected_scope") == "multiple users" and not company:
            base.update(
                question_for_human="Which company is affected?",
                human_field="company",
                question_reason="Company identifies the shared alerts and changes to investigate.",
            )
        elif (
            analysis.get("product") == "VPN"
            and observed_company
            and not any(e["tool"] == "get_active_alerts" for e in evidence)
        ):
            base.update(
                requested_tools=[
                    call("get_active_alerts", company=observed_company),
                    call("get_recent_changes", company=observed_company),
                ]
            )
        elif (
            analysis.get("product") == "VPN"
            and analysis.get("user_name")
            and any(e["tool"] == "get_active_alerts" and e["content"].get("site") for e in evidence)
        ):
            ids = [
                e["id"]
                for e in evidence
                if e["tool"]
                in {"get_active_alerts", "get_recent_changes", "search_previous_tickets"}
            ]
            base["hypotheses"] = [
                {
                    "claim": (
                        "Recent WatchGuard configuration change is a plausible cause, supported by"
                        " the site-specific alert and previous incident, but it is not yet"
                        " confirmed."
                    ),
                    "evidence_ids": ids,
                    "confidence": 0.6,
                }
            ]
            if not facts.get("current_site"):
                base.update(
                    question_for_human=(
                        f"Which site or floor is {analysis['user_name']} currently working from?"
                    ),
                    human_field="current_site",
                    question_reason="The active alert affects only the second floor.",
                    question_subject=analysis["user_name"],
                )
            elif not facts.get("required_update_installed"):
                base.update(
                    question_for_human=(
                        f"Has {analysis['user_name']} installed the required VPN client update?"
                    ),
                    human_field="required_update_installed",
                    question_reason="The site configuration change requires a client update.",
                    question_subject=analysis["user_name"],
                )
            else:
                base.update(
                    enough_information=True,
                    confidence=0.75,
                    likely_issue=(
                        "A stale client profile is plausible; root cause remains unconfirmed."
                    ),
                    recommended_next_action=(
                        "The required update is confirmed installed. Refresh the client profile and"
                        " retry VPN authentication; inspect logs if failure continues."
                    ),
                    troubleshooting_steps=[
                        "Refresh the client profile",
                        "Retry authentication",
                        "Collect diagnostic logs if it fails",
                    ],
                )
        elif analysis.get("device") and any(e["tool"] == "get_device_context" for e in evidence):
            base.update(
                enough_information=True,
                confidence=0.75,
                likely_issue="The printer queue is paused.",
                recommended_next_action=(
                    "Verify the physical jam is cleared, resume the paused queue, and print a test"
                    " page."
                ),
            )
        elif analysis.get("product") == "Microsoft 365":
            base.update(
                enough_information=True,
                confidence=0.6,
                recommended_next_action=(
                    "Check the Microsoft Entra sign-in failure code and registered authentication"
                    " method; verify Authenticator enrollment before changing policy."
                ),
                likely_issue="MFA enrollment or policy failure remains unconfirmed.",
            )
        elif analysis.get("affected_scope") == "multiple users" and evidence:
            base.update(
                enough_information=True,
                confidence=0.6,
                recommended_next_action=(
                    "Correlate the shared service alert with the recent VPN change; check service"
                    " health before individual devices."
                ),
            )
        elif documents or any(e["tool"] == "fetch_web_page" for e in evidence):
            base.update(
                enough_information=True,
                confidence=0.6,
                recommended_next_action=(
                    "Review diagnostic logs and verify the documented supported configuration."
                ),
            )
        elif not context.get("research_rounds"):
            base.update(needs_web_research=True, research_queries=[text])
        return schema.model_validate(base)
