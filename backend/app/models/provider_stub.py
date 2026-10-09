"""Deterministic structured provider exclusively for isolated automated tests."""

import re


class StubProvider:
    def name(self):
        return "stub"

    async def generate_structured(self, stage, context, schema):
        if stage == "analyze_ticket":
            text = context["ticket"]["summary"]
            match = re.search(r"([A-Z][a-z]+ [A-Z][a-z]+) from ([\w-]+)", text)
            data = dict(
                user_name=match[1] if match else None,
                company=match[2] if match else None,
                product="WatchGuard VPN" if "vpn" in text.lower() else None,
                issue_type="vpn_authentication" if "vpn" in text.lower() else "unknown",
                symptoms=text,
                error_messages=(
                    ["Authentication failed"] if "authentication failed" in text.lower() else []
                ),
                missing_information=(
                    ["current_site", "required_update_installed"] if "vpn" in text.lower() else []
                ),
            )
            return schema.model_validate(data)
        analysis = context["analysis"]
        facts = {
            f["predicate"]: f["value"]
            for f in context.get("remembered_facts", []) + context.get("confirmed_facts", [])
        }
        base = dict(
            evidence_summary="Assessment based on available incident, alert and change evidence.",
            confidence=0,
            enough_information=False,
            hypotheses=[],
            uncertainty=[],
        )
        if (
            analysis.get("user_name")
            and analysis.get("company")
            and not facts.get("current_site")
            and not analysis.get("site")
        ):
            base.update(
                question_for_human=f"Which site or floor is {analysis['user_name']} currently working from?",
                human_field="current_site",
                missing_information=["current_site"],
                decision_justification="The current site is needed to correlate incident evidence.",
            )
        elif context.get("changes") and not facts.get("required_update_installed"):
            base.update(
                question_for_human=f"Has {analysis['user_name']} installed the required VPN client update?",
                human_field="required_update_installed",
                missing_information=["required_update_installed"],
                decision_justification="The configuration change requires a client update.",
            )
        elif context.get("evidence"):
            changes = context.get("changes", [])
            action = (
                changes[0]["required_action"]
                if changes
                else "Review the supplied evidence with the service desk technician."
            )
            if facts.get("required_update_installed") == "Yes":
                action = "The required update is confirmed installed. Refresh the client profile and retry authentication; if it fails, ask the technician to correlate logs with the acknowledged active alert."
            hypotheses = [
                c.get("summary", c.get("message", "")) for c in changes + context.get("alerts", [])
            ]
            base.update(
                enough_information=True,
                confidence=0.7 if changes else 0.35,
                likely_issue=hypotheses[0] if hypotheses else "Issue needs technician review",
                hypotheses=hypotheses,
                inferences=(
                    [
                        "The site alert and recent configuration change correlate with the authentication failure."
                    ]
                    if changes and context.get("alerts")
                    else []
                ),
                recommended_next_action=action,
                uncertainty=["No successful connection has been verified."],
                decision_justification="Recommendation follows the available evidence; resolution is unverified.",
            )
        elif not context.get("web_attempted"):
            base.update(needs_web_research=True, uncertainty=["No local evidence found."])
        else:
            base.update(
                recommended_next_action="Provide diagnostic logs or a relevant knowledge document.",
                uncertainty=["No supporting evidence was found."],
            )
        return schema.model_validate(base)
