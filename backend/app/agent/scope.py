"""Scope decisions come from the canonical structured intent classifier."""

SCOPE_MESSAGE = (
    "DeskMate is configured for IT support and internal knowledge investigations. "
    "Please ask a service-desk or uploaded-document question."
)
SCOPE_INSTRUCTIONS = """Classify scope as well as workflow. Set in_scope=true for service-desk
incidents, technical product/support questions, internal knowledge/policy and uploaded-document
questions (including resumes). Set in_scope=false for clearly unrelated general news, immigration,
sports, restaurant recommendations or political news unless tied to a specific supplied document
or IT support problem. Do not use a person's identity alone as evidence of a support incident.
Conversation contact confirmations and follow-ups to an existing IT issue are in scope.
Return RequestIntent JSON; never research before this classification."""
