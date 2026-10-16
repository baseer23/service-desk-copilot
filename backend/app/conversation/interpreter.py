"""Entity-span parsing: transcript -> intent -> spans -> normalized operations."""

from __future__ import annotations
import re
from backend.app.conversation.models import EntityOperation, InterpretedTurn


def normalize_local(span):
    return re.sub(r"[^a-z0-9._+-]", "", span.casefold())


def spelled(span):
    repeats = {"double": 2, "triple": 3, "two": 2, "three": 3}
    out = []
    repeat = 1
    for token in re.findall(r"[a-z0-9]+", span.casefold().replace("-", " ")):
        if token in repeats:
            repeat = repeats[token]
        elif token not in {"letter", "letters", "and", "then"}:
            out.append(token * repeat)
            repeat = 1
    return "".join(out)


PREFIX = re.compile(
    r"^(?:no[,.]?\s*)?(?:my\s+)?(?:full\s+)?(?:e-?mail(?:\s+address)?(?:\s+is)?|address(?:\s+is)?|it\s+is)\s+",
    re.I,
)


def deterministic_turn(transcript, current_email=None, active_entity=None):
    text = transcript.strip()
    lower = text.casefold().strip(" .")
    if re.fullmatch(
        r"(?:yes|yes[,]? that is correct|yes[,]? that's correct|that is correct|correct|that's right)",
        lower,
    ):
        confirmation_entity = (
            "contact.email.local_part" if active_entity == "contact.email" else active_entity
        ) or "contact.email.local_part"
        return InterpretedTurn(
            intent="confirm_entity",
            operations=[EntityOperation(entity=confirmation_entity, operation="CONFIRM")],
        )
    if re.search(r"\b(?:undo|go back to the previous version)\b", lower):
        return InterpretedTurn(
            intent="undo",
            operations=[EntityOperation(entity="contact.email.local_part", operation="UNDO")],
        )
    ticket = re.search(r"\bticket\s+(?:number|reference)\s+is\s+([a-z0-9-]+)", text, re.I)
    if ticket:
        return InterpretedTurn(
            intent="set_entity",
            operations=[
                EntityOperation(
                    entity="ticket.reference", operation="SET", new_value=ticket.group(1).upper()
                )
            ],
        )
    # Explicit local part with no domain must never trigger domain resolution.
    local_only = re.search(
        r"(?:no[,.]?\s*)?(?:it is )?(?:just )?(.+?)\s+before\s+the\s+at\s+symbol", text, re.I
    )
    if local_only:
        return InterpretedTurn(
            intent="set_entity",
            operations=[
                EntityOperation(
                    entity="contact.email.local_part",
                    operation="SET",
                    new_value=spelled(local_only.group(1)),
                )
            ],
        )
    # Complete email: isolate only the entity span on each side of the address separator.
    separator = re.search(r"\s+(?:at|@)\s+", text, re.I)
    if separator:
        left = PREFIX.sub("", text[: separator.start()].strip())
        right = text[separator.end() :].strip(" .,!?")
        local = spelled(left)
        domain = right
        if local and domain:
            return InterpretedTurn(
                intent="set_entity",
                operations=[
                    EntityOperation(
                        entity="contact.email.local_part", operation="SET", new_value=local
                    ),
                    EntityOperation(
                        entity="contact.email.domain", operation="SET", new_value=domain
                    ),
                ],
            )
    replace = re.search(r"\b([a-z0-9]+)\s+(?:is actually|should be|is)\s+(.+?)[.]?$", text, re.I)
    if replace and current_email:
        return InterpretedTurn(
            intent="correct_entity",
            operations=[
                EntityOperation(
                    entity="contact.email.local_part",
                    operation="REPLACE",
                    old_value=normalize_local(replace.group(1)),
                    new_value=spelled(replace.group(2)),
                )
            ],
        )
    return None
