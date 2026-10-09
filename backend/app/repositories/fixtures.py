"""Coherent B99 examples, never represented as live enterprise integrations."""

from datetime import datetime, timedelta, timezone


def canonical_site(value: str, company: str = "B99") -> str:
    value = value.strip()
    lower = value.lower()
    if any(x in lower for x in ("2nd", "second")):
        return f"{company} 2nd floor".strip()
    if any(x in lower for x in ("1st", "first")):
        return f"{company} 1st floor".strip()
    return value


class FixtureRepository:
    def __init__(self):
        now = datetime.now(timezone.utc)
        earlier = (now - timedelta(hours=2)).isoformat()
        self.company = self._record(
            name="B99", sites=["B99 1st floor", "B99 2nd floor"], product="WatchGuard VPN"
        )
        self.user = self._record(
            name="Jake Peralta", company="B99", current_site=None, required_update_installed=None
        )
        self.device = self._record(
            name="Jake's laptop",
            user="Jake Peralta",
            company="B99",
            product="WatchGuard VPN",
            required_update_installed=None,
        )
        self.incident = self._record(
            id="B99-previous-vpn",
            user="Jake Peralta",
            company="B99",
            site="B99 2nd floor",
            product="WatchGuard VPN",
            issue="VPN authentication failed",
            outcome="Client profile refreshed after the VPN configuration change; verify required client update before retrying.",
        )
        self.alert = self._record(
            id="B99-alert-vpn",
            company="B99",
            site="B99 2nd floor",
            product="WatchGuard VPN",
            status="active",
            message="Elevated VPN authentication failures",
            acknowledged_by="Amy Santiago",
            technician_event="Alert acknowledged by a technician",
            acknowledged_at=(now - timedelta(hours=1)).isoformat(),
        )
        self.change = self._record(
            id="B99-change-vpn",
            company="B99",
            site="B99 2nd floor",
            product="WatchGuard VPN",
            changed_at=earlier,
            summary="WatchGuard VPN configuration updated earlier today",
            required_action="Install the required VPN client update and refresh the client profile",
        )

    @staticmethod
    def _record(**data):
        return {
            **data,
            "source_system": "local_fixture",
            "source": "B99 example dataset",
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }

    def get_user_context(self, user_name, company=None):
        if user_name.casefold() == "sarah":
            return self._record(
                name="Sarah",
                company="B99",
                product="Microsoft 365",
                mfa_method="Authenticator",
                current_site=None,
            )
        return (
            dict(self.user)
            if user_name.casefold() in {"jake", "jake peralta"}
            and (not company or company.casefold() == "b99")
            else {}
        )

    def get_company_context(self, company):
        return dict(self.company) if company.casefold() == "b99" else {}

    def get_site_context(self, company, site):
        site = canonical_site(site, company)
        return (
            self._record(name=site, company="B99", product="WatchGuard VPN")
            if company.casefold() == "b99" and site in self.company["sites"]
            else {}
        )

    def get_device_context(self, device_or_user):
        if device_or_user.upper() == "PRN-ACCT-02":
            return self._record(
                name="PRN-ACCT-02",
                company="B99",
                department="Accounting",
                product="Network printer",
                queue_status="paused",
                last_test_page="pending",
                diagnostic="Queue paused after a paper-jam event; physical jam clearance is unconfirmed",
            )
        return dict(self.device) if "jake" in device_or_user.lower() else {}

    def search_previous_tickets(self, query, user=None, company=None, site=None):
        if company and company.casefold() != "b99":
            return []
        if user and user.casefold() != "jake peralta":
            return []
        if site and canonical_site(site) != self.incident["site"]:
            return []
        return (
            [dict(self.incident)]
            if any(t in query.lower() for t in ("vpn", "auth", "jake"))
            else []
        )

    def _matches(self, record, company, site, product):
        return (
            company.casefold() == "b99"
            and (not site or canonical_site(site) == record["site"])
            and (not product or "vpn" in product.lower() or "watchguard" in product.lower())
        )

    def get_active_alerts(self, company, site=None, product=None):
        return [dict(self.alert)] if self._matches(self.alert, company, site, product) else []

    def get_recent_changes(self, company, site=None, product=None):
        return [dict(self.change)] if self._matches(self.change, company, site, product) else []

    def get_ticket(self, ticket_id):
        return dict(self.incident) if ticket_id == self.incident["id"] else {}

    def relationships(self):
        return [
            {"subject": "Jake Peralta", "predicate": "WORKS_AT", "object": "B99"},
            {"subject": "B99", "predicate": "HAS_SITE", "object": "B99 2nd floor"},
            {"subject": "B99 2nd floor", "predicate": "USES", "object": "WatchGuard VPN"},
            {"subject": self.alert["id"], "predicate": "AFFECTS", "object": "B99 2nd floor"},
            {"subject": self.change["id"], "predicate": "APPLIES_TO", "object": "WatchGuard VPN"},
            {"subject": self.incident["id"], "predicate": "INVOLVES", "object": "WatchGuard VPN"},
        ]
