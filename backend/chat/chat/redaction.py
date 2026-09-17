# backend/chat/chat/redaction.py
from __future__ import annotations

import re

# Card: 13-19 digits, optionally space- or dash-grouped. Anchored on length so a
# product figure like "50 000" (5 digits) can never match.
CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
PHONE = re.compile(r"(?:\+994|0)[ -]?\d{2}[ -]?\d{3}[ -]?\d{2}[ -]?\d{2}\b")
FIN = re.compile(
    r"\b(?=[A-Z0-9]{7}\b)(?=[A-Z0-9]{0,6}\d)(?=[A-Z0-9]{0,6}[A-Z])[A-Z0-9]{7}\b",
    re.IGNORECASE,
)


def redact(text: str) -> str:
    """Applied at log-write, before anything is persisted."""
    text = CARD.sub("[redacted:card]", text)
    text = PHONE.sub("[redacted:phone]", text)
    return FIN.sub("[redacted:id]", text)
