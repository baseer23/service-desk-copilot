"""Generic queries over explicitly fictional enterprise records, owned by MCP."""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path


def canonical_site(value: str, company: str = "B99") -> str:
    value = value.strip()
    lower = value.casefold()
    if any(x in lower for x in ("2nd", "second")):
        return f"{company} 2nd floor".strip()
    if any(x in lower for x in ("1st", "first")):
        return f"{company} 1st floor".strip()
    return value


def terms(text):
    return set(re.findall(r"[\w-]+", text.casefold())) - {
        "the",
        "a",
        "an",
        "to",
        "from",
        "of",
        "at",
        "is",
        "are",
        "and",
        "cannot",
        "can",
        "after",
        "this",
        "for",
    }


class FixtureRepository:
    def __init__(self):
        data = json.loads(Path(__file__).with_name("fixture_data.json").read_text())
        self.records = {}
        for category, rows in data.items():
            if isinstance(rows, list):
                self.records[category] = [self._record(**row) for row in rows]

    @staticmethod
    def _record(**data):
        now = datetime.now(timezone.utc)
        hours = data.pop("hours_ago", None)
        if hours is not None:
            data["changed_at"] = (now - timedelta(hours=hours)).isoformat()
        return {
            **data,
            "source_system": "local_fixture",
            "source": "Fictional service-desk dataset",
            "observed_at": now.isoformat(),
        }

    def _lookup(self, category, name, company=None):
        for row in self.records[category]:
            names = [row.get("name", ""), row.get("id", ""), *row.get("aliases", [])]
            if name.casefold() in {n.casefold() for n in names} and (
                not company or row.get("company", "").casefold() == company.casefold()
            ):
                return dict(row)
        return {}

    def get_user_context(self, user_name, company=None):
        return self._lookup("users", user_name, company)

    def get_company_context(self, company):
        return self._lookup("companies", company)

    def get_site_context(self, company, site):
        row = self._lookup("sites", site, company) or self._lookup(
            "sites", canonical_site(site, company), company
        )
        return {**row, "site": row["name"]} if row else {}

    def get_device_context(self, device_or_user):
        return self._lookup("devices", device_or_user)

    def search_previous_tickets(self, query, user=None, company=None, site=None):
        canonical_user = self.get_user_context(user).get("name", user) if user else None
        found = []
        query_terms = terms(query)
        for row in self.records["tickets"]:
            if company and row.get("company", "").casefold() != company.casefold():
                continue
            if canonical_user and row.get("user", "").casefold() != canonical_user.casefold():
                continue
            if site and canonical_site(site, company or row.get("company", "")) != row.get("site"):
                continue
            record_terms = terms(
                " ".join([row.get("issue", ""), row.get("product", ""), *row.get("tags", [])])
            )
            score = len(query_terms & record_terms)
            if score:
                found.append((score, row))
        return [dict(row) for _, row in sorted(found, key=lambda pair: -pair[0])[:5]]

    @staticmethod
    def _matches(record, company, site, product):
        return (
            record.get("company", "").casefold() == company.casefold()
            and (not site or canonical_site(site, company) == record.get("site"))
            and (not product or bool(terms(product) & terms(record.get("product", ""))))
        )

    def get_active_alerts(self, company, site=None, product=None):
        return [
            dict(row)
            for row in self.records["alerts"]
            if self._matches(row, company, site, product)
        ]

    def get_recent_changes(self, company, site=None, product=None):
        return [
            dict(row)
            for row in self.records["changes"]
            if self._matches(row, company, site, product)
        ]

    def get_ticket(self, ticket_id):
        return self._lookup("tickets", ticket_id)

    def inventory(self):
        return {
            "fixture_" + category: [r["name"] for r in self.records[category]]
            for category in ("companies", "users", "devices", "sites")
        }

    def relationships(self):
        result = []

        def edge(subject, predicate, obj):
            result.append({"subject": subject, "predicate": predicate, "object": obj})

        for user in self.records["users"]:
            edge(user["name"], "WORKS_AT", user["company"])
        for site in self.records["sites"]:
            edge(site["company"], "HAS_SITE", site["name"])
            if site.get("product"):
                edge(site["name"], "USES", site["product"])
        for device in self.records["devices"]:
            if device.get("user"):
                edge(device["user"], "ASSOCIATED_WITH", device["name"])
        for row in self.records["alerts"]:
            edge(row["id"], "AFFECTS", row.get("site") or row["company"])
        for row in self.records["changes"]:
            edge(row["id"], "APPLIES_TO", row["product"])
        for row in self.records["tickets"]:
            edge(row["id"], "INVOLVES", row["product"])
        return result
