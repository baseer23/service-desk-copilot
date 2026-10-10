"""Fictional tickets and canned model responses, confined to validation.

These are test data, not production routing rules. The deterministic suite proves
execution/provenance/guards; the separate Groq run evaluates model selection.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    number: int
    name: str
    ticket: str
    analysis: dict
    calls: list
    kb: str
    hypothesis: str
    action: str
    action_terms: tuple
    questions: tuple = ()
    workflow: str = "service_desk"
    research_query: str = ""


def call(name, **arguments):
    return {
        "name": name,
        "arguments": arguments,
        "reason": "Resolve the ticket's diagnostic evidence gap",
    }


def kb(query):
    return call("search_knowledge", question=query, mode="VECTOR", top_k=6)


def previous(query, **filters):
    return call("search_previous_tickets", query=query, **filters)


SCENARIOS = [
    Scenario(
        1,
        "VPN",
        "Jake Peralta from B99 cannot connect to the corporate VPN. Authentication failed.",
        {
            "user_name": "Jake Peralta",
            "company": "B99",
            "product": "VPN",
            "issue_type": "network authentication",
            "symptoms": "VPN authentication failed",
            "entities": ["Jake Peralta", "B99"],
        },
        [
            call("get_user_context", user_name="Jake Peralta"),
            previous("VPN authentication failed", user="Jake Peralta"),
            kb("VPN client configuration update"),
            call("get_active_alerts", company="B99", product="VPN"),
            call("get_recent_changes", company="B99", product="VPN"),
        ],
        "Fictional internal VPN KB: correlate current site, active alert and configuration change. Verify required client update and refresh the profile under the approved procedure before retrying. Similar historical incidents do not prove current causation.",
        "A client profile/update mismatch is a plausible cause, not yet confirmed; the alert and earlier change establish correlation only.",
        "Install the required approved VPN client update, refresh the profile, retry and retain authentication logs if failure persists.",
        ("update", "profile"),
        (
            (
                "current_site",
                "Which site is Jake working from right now?",
                "Jake Peralta",
                "A site-specific VPN alert makes current location material to applicability.",
                "2nd floor",
            ),
            (
                "required_update_installed",
                "Has Jake installed the required VPN client update?",
                "Jake Peralta",
                "The applicable change requires a client update; installation state determines the next action.",
                "No",
            ),
        ),
    ),
    Scenario(
        2,
        "MFA",
        "Sarah Ahmed replaced her phone and can no longer complete Microsoft 365 MFA.",
        {
            "user_name": "Sarah Ahmed",
            "product": "Microsoft 365",
            "issue_type": "identity MFA",
            "symptoms": "New phone cannot complete MFA",
            "entities": ["Sarah Ahmed", "Microsoft 365"],
        },
        [
            call("get_user_context", user_name="Sarah Ahmed"),
            previous("MFA replacement phone", user="Sarah Ahmed"),
            kb("Microsoft 365 Authenticator replacement phone MFA"),
        ],
        "Fictional internal identity KB: a replacement phone must register an authentication method. Verify identity using the approved process before an authorized administrator resets old authentication methods and performs Authenticator re-registration. Do not bypass MFA.",
        "The old-phone method may explain the failure; this is a plausible cause, not yet confirmed.",
        "Verify Sarah's identity, then have an authorized administrator reset the old authentication methods and complete Authenticator re-registration.",
        ("identity", "registration"),
    ),
    Scenario(
        3,
        "PRINTER",
        "Accounting cannot print invoices to PRN-ACCT-02. Other printers are working.",
        {
            "device": "PRN-ACCT-02",
            "product": "Network printer",
            "issue_type": "printing",
            "affected_scope": "One printer",
            "symptoms": "Invoice printing fails; other printers work",
            "entities": ["PRN-ACCT-02"],
        },
        [
            call("get_device_context", device_or_user="PRN-ACCT-02"),
            previous("PRN-ACCT-02 printer invoices"),
            kb("Paused printer queue paper jam test page"),
        ],
        "Fictional internal printing KB: check a paused queue and physical paper jam before resuming the affected printer queue. Print a test page. If other printers work, investigate this queue/device before treating it as a broad network outage.",
        "A paused queue or remaining paper jam is plausible, not yet confirmed; working other printers argue against a broad outage.",
        "Confirm the physical jam is cleared, inspect and resume PRN-ACCT-02's queue under policy, then print a test page.",
        ("queue", "test page"),
    ),
    Scenario(
        4,
        "MULTI-USER OUTAGE",
        "Five users at the same client suddenly cannot connect to VPN.",
        {
            "product": "VPN",
            "issue_type": "shared outage",
            "affected_scope": "Five users at one company",
            "symptoms": "Sudden shared VPN failure",
            "entities": ["VPN"],
        },
        [
            kb("Shared VPN gateway incident"),
            call("get_company_context", company="$company"),
            call("get_active_alerts", company="$company", product="VPN"),
            call("get_recent_changes", company="$company", product="VPN"),
            previous("VPN outage multiple users", company="$company"),
        ],
        "Fictional internal network KB: for simultaneous multi-user failure establish the affected company, then inspect shared service alerts and recent gateway changes. Escalate shared diagnostics to network operations before reinstalling individual clients. A nearby change does not prove causation.",
        "A shared gateway incident is plausible, not yet confirmed; certificate rotation is a correlated change requiring log verification.",
        "Link the five reports to the shared VPN incident and ask network operations to compare gateway authentication logs with the certificate rotation.",
        ("shared", "logs"),
        (
            (
                "company",
                "Which client company is affected?",
                "Affected client",
                "Company identity is required to scope alerts and recent changes; the ticket does not name it.",
                "Contoso",
            ),
        ),
    ),
    Scenario(
        5,
        "ACCOUNT LOCKOUT",
        "Michael's Windows account keeps locking again immediately after his password is reset.",
        {
            "user_name": "Michael",
            "product": "Windows",
            "issue_type": "account lockout",
            "symptoms": "Immediate relock after password reset",
            "entities": ["Michael", "Windows"],
        },
        [
            call("get_user_context", user_name="Michael"),
            call("get_device_context", device_or_user="Michael"),
            previous("Windows account lockout password", user="Michael"),
            kb("Repeated lockout saved credentials mapped drive service scheduled task"),
        ],
        "Fictional internal Windows KB: inspect domain lockout events to identify the source device. Saved credentials, mapped drives, mobile clients, services and scheduled tasks can retry an old password. Verify the actual source before replacing credentials; a historical incident is not proof.",
        "A saved credential, mapped drive, service or scheduled task may retry the old password; plausible, not yet confirmed.",
        "Review lockout events for WIN-MICHAEL-01, identify the credential consumer, and update only the verified stale saved credential or mapped-drive entry.",
        ("lockout", "credential"),
    ),
    Scenario(
        6,
        "OUTLOOK",
        "Priya can send email from Outlook but incoming messages have stopped appearing since this morning.",
        {
            "user_name": "Priya",
            "product": "Microsoft 365 Outlook",
            "issue_type": "email synchronization",
            "symptoms": "Outlook sends; new incoming mail not shown",
            "entities": ["Priya", "Outlook"],
        },
        [
            call("get_user_context", user_name="Priya"),
            previous("Outlook incoming email", user="Priya"),
            kb("Outlook incoming missing webmail synchronization"),
        ],
        "Fictional internal mail KB: check whether new messages appear in webmail to separate mailbox delivery from local Outlook view/synchronization issues. If webmail receives them, inspect Outlook filters, offline state and synchronization before rebuilding the profile; otherwise check mailbox rules and service health.",
        "If webmail receives new mail, a local Outlook view/synchronization problem is plausible, not yet confirmed.",
        "Since webmail receives new messages, inspect Outlook view filters, offline state and synchronization before considering profile repair.",
        ("outlook", "synchronization"),
        (
            (
                "webmail_receives_mail",
                "Do new messages appear in Priya's webmail?",
                "Priya",
                "This separates server delivery from the local Outlook synchronization path.",
                "Yes",
            ),
        ),
    ),
    Scenario(
        7,
        "DEVICE PERFORMANCE",
        "David's laptop became extremely slow after this morning's updates and Task Manager shows disk usage near 100%.",
        {
            "user_name": "David",
            "product": "Windows 11",
            "issue_type": "endpoint performance",
            "symptoms": "Slow after updates with disk near 100%",
            "entities": ["David", "Windows"],
        },
        [
            call("get_user_context", user_name="David"),
            call("get_device_context", device_or_user="David"),
            call("get_recent_changes", company="Fabrikam", product="Windows"),
            previous("slow disk updates", user="David"),
            kb("High disk usage Windows update progress"),
        ],
        "Fictional internal endpoint KB: inspect the process consuming disk and update progress. Keep the device powered, retain diagnostics and wait for installation completion before a policy-approved restart. Do not terminate the installer or assert update causation from timing alone.",
        "Ongoing installer disk activity could explain the slowdown; plausible, not yet confirmed, and timing alone does not prove a defective update.",
        "Monitor Windows Modules Installer activity and update progress, retain disk diagnostics and perform an approved restart only after installation completes.",
        ("progress", "restart"),
    ),
    Scenario(
        8,
        "PERMISSIONS",
        "Maria can open the Finance SharePoint site but gets Access Denied when opening the Budget folder.",
        {
            "user_name": "Maria",
            "product": "SharePoint",
            "issue_type": "folder authorization",
            "symptoms": "Site accessible; Budget folder denied",
            "entities": ["Maria", "Finance", "SharePoint"],
        },
        [
            call("get_user_context", user_name="Maria"),
            previous("SharePoint Budget folder access", user="Maria"),
            kb("SharePoint unique folder permissions membership"),
            call("search_graph", entities=["Maria", "SharePoint"]),
        ],
        "Fictional internal SharePoint KB: site access does not guarantee folder access when unique permissions exist. The folder owner must verify effective permissions and approved group membership before any access grant. Resetting a password does not fix authorization.",
        "Unique Budget folder permissions or missing group membership are plausible causes, not yet confirmed; successful site access supports an authorization investigation.",
        "Ask the Budget folder owner to verify effective folder permissions and approved group membership before granting any access.",
        ("permissions", "membership"),
    ),
    Scenario(
        9,
        "APPLICATION",
        "Autotask opens for everyone else, but Kevin gets a blank page after signing in.",
        {
            "user_name": "Kevin",
            "product": "Autotask",
            "issue_type": "application browser",
            "affected_scope": "One user",
            "symptoms": "Blank page for Kevin only",
            "entities": ["Kevin", "Autotask"],
        },
        [
            call("get_user_context", user_name="Kevin"),
            call("get_device_context", device_or_user="Kevin"),
            previous("Autotask blank browser", user="Kevin"),
            kb("Autotask blank page browser session extension"),
        ],
        "Fictional internal SaaS KB: when one user sees a blank page and others work, compare a clean private browser session before changing account settings. Isolate site storage and extensions using an approved procedure and retain browser console errors. Do not infer a global outage.",
        "A browser session/cache or extension conflict is plausible, not yet confirmed; others working makes a global outage less likely.",
        "Test Autotask in a clean private browser session, then isolate site storage/extensions under policy and retain browser console errors if it persists.",
        ("browser", "session"),
    ),
    Scenario(
        10,
        "WEB RESEARCH",
        "A technician sees Microsoft Entra error AADSTS53003 in a customer tenant. What does the error mean and what should be checked?",
        {
            "product": "Microsoft Entra",
            "issue_type": "unknown public error",
            "symptoms": "AADSTS53003 meaning is absent locally",
        },
        [kb("Microsoft Entra AADSTS53003")],
        "Fictional internal tenant overview: this dataset deliberately has no definition of the newly reported error. No tenant policy or sign-in logs are available.",
        "A Conditional Access policy block is a plausible explanation of the reported code, not yet confirmed for this tenant without sign-in logs.",
        "Have an authorized tenant administrator review sign-in logs and the Conditional Access policy result; do not disable the policy based on the error alone.",
        ("sign-in", "policy"),
        workflow="knowledge",
        research_query="Microsoft identity platform authentication authorization error codes AADSTS53003",
    ),
]

MICROSOFT_URL = "https://learn.microsoft.com/en-us/entra/identity-platform/reference-error-codes"
PUBLIC_HTML = """<html><title>Microsoft identity platform error codes</title><main>
<h1>Microsoft identity platform error codes</h1>
<p>AADSTS53003 - BlockedByConditionalAccess. Access has been blocked by Conditional Access policies. The access policy does not allow token issuance.</p>
<p>Review the sign-in logs and the Conditional Access evaluation in the affected tenant with an authorized administrator. This public error definition alone does not identify a particular tenant policy or prove the customer root cause.</p>
</main></html>"""
