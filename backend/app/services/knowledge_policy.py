"""Exclude explicitly labeled test/demo material from normal knowledge evidence."""

import re


_LABEL = re.compile(r"^(?:fictional|demo|acceptance|validation)(?:\b|[_-])", re.I)


def is_test_knowledge(metadata):
    return (
        metadata.get("source_system") == "local_fixture"
        or metadata.get("is_fixture") is True
        or bool(_LABEL.match(str(metadata.get("title") or "").strip()))
        or metadata.get("source_system") in {"acceptance_test", "validation_fixture", "test_fixture"}
    )
