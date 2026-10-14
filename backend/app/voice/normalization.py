"""Conservative contact parsing and explicit, deterministic human confirmations."""

import re
from typing import Literal

import phonenumbers


def normalize_name(raw: str) -> str:
    value = " ".join(raw.strip(" .,!").split())
    if not 2 <= len(value) <= 100 or not any(c.isalpha() for c in value):
        raise ValueError("Please provide a readable name")
    if not all(c.isalpha() or c in " '-.\u2019" for c in value):
        raise ValueError("Please correct the name using text")
    return value


def normalize_email(raw: str) -> str:
    value = raw.strip().lower().rstrip(".,!?")
    for word, symbol in [
        ("at", "@"),
        ("dot", "."),
        ("underscore", "_"),
        ("dash", "-"),
        ("hyphen", "-"),
    ]:
        value = re.sub(r"(?<!\S)" + word + r"(?!\S)", symbol, value)
    value = re.sub(r"\s+", "", value)
    if len(value) > 254 or value.count("@") != 1:
        raise ValueError("Please provide the full email address, including its domain")
    local, domain = value.split("@")
    if (
        not re.fullmatch(r"[a-z0-9][a-z0-9._+\-]{0,63}", local)
        or ".." in local
        or local.endswith(".")
        or not re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+", domain
        )
        or not re.fullmatch(r"[a-z]{2,63}", domain.rsplit(".", 1)[-1])
        or any(len(label) > 63 for label in domain.split("."))
    ):
        raise ValueError("Please repeat or type the complete email address")
    return value


def normalize_phone(raw: str, region: str | None = None) -> str:
    words = dict(
        zip(
            ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"),
            "0123456789",
            strict=True,
        )
    )
    value = raw.lower().strip().replace("plus", "+")
    for word, digit in words.items():
        value = re.sub(r"\b" + word + r"\b", digit, value)
    if not re.fullmatch(r"[+0-9\s().-]+", value):
        raise ValueError("Please correct the callback number using digits")
    if not value.lstrip().startswith("+") and not region:
        raise ValueError("Which country is the callback number in?")
    try:
        number = phonenumbers.parse(value, region)
        if not phonenumbers.is_valid_number(number):
            raise ValueError("Please correct the callback number")
        return str(phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164))
    except phonenumbers.NumberParseException:
        raise ValueError("Please correct the callback number and country") from None


def confirmation(raw: str) -> bool | None:
    value = re.sub(r"\s+", " ", raw.replace("\u2019", "'").replace(",", " "))
    value = value.strip().lower().strip(".!?")
    if value in {
        "yes",
        "yes please",
        "correct",
        "that's correct",
        "that is correct",
        "confirm",
        "confirmed",
        "yes that is correct",
        "yes that's correct",
        "yes that is right",
        "yes that's right",
        "that's right",
        "that is right",
    }:
        return True
    if value in {
        "no", "incorrect", "not correct", "that's wrong", "that is wrong",
        "no that's wrong", "no that is wrong", "no that's not correct", "no that is not correct",
    }:
        return False
    return None


def action_result(raw: str) -> Literal["worked", "failed"] | None:
    value = raw.lower().strip().strip(".!?")
    if value in {
        "yes",
        "yes it worked",
        "that worked",
        "it worked",
        "it works now",
        "it's working now",
        "that fixed it",
        "the problem is resolved",
    }:
        return "worked"
    if value in {
        "no",
        "it didn't work",
        "it did not work",
        "still broken",
        "still failing",
        "that failed",
        "no it still fails",
    }:
        return "failed"
    return None


def spoken_email(value: str) -> str:
    symbols = {"@": "at", ".": "dot", "_": "underscore", "-": "dash", "+": "plus"}
    return " ".join(symbols.get(c, c) for c in value)


def grouped_phone(value: str) -> str:
    return "plus " + " ".join(value.lstrip("+"))
