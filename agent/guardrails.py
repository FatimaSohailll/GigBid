"""Deterministic safety checks that no model output can switch off."""

import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from agent.handoffs import RiskFlag

_defences_enabled: ContextVar[bool] = ContextVar("gigbid_injection_defences", default=True)

_INJECTION_PATTERNS = (
    r"ignore (?:all |your |the )?(?:previous|prior|above) (?:instructions|rules)",
    r"disregard (?:all |your |the )?(?:previous|prior|above)? ?(?:instructions|rules)",
    r"system (?:note|prompt|message)\b",
    r"\bif you are an? (?:ai|llm|language model|bot)\b",
    r"\b(?:ai assistants?|language models?|automated tools?)\b.{0,40}\b(?:must|should|reading)\b",
    r"reveal your (?:system )?prompt",
    r"\[admin override\]",
    r"<!--\s*assistant",
    r"skip the risk checks?",
)
_RISK_PATTERNS: dict[RiskFlag, tuple[str, ...]] = {
    RiskFlag.OFF_PLATFORM: (
        r"\b(?:telegram|whatsapp|signal|discord|skype)\b",
        r"outside (?:this|the) (?:site|platform)",
        r"avoid the fees?",
    ),
    RiskFlag.FREE_WORK: (
        r"\bfor free\b",
        r"\bunpaid\b(?! milestone)",
        r"free (?:test|sample|trial)",
    ),
    RiskFlag.UPFRONT_PAYMENT: (
        r"\bdeposit\b.{0,40}\b(?:gift card|bank transfer|wire|refundable)\b",
        r"refundable .{0,20}deposit",
        r"\bgift cards?\b",
        r"wire the difference",
        r"\bcheque\b.{0,60}\bmore than\b",
    ),
    RiskFlag.CREDENTIAL_REQUEST: (
        r"\b(?:login|password|credentials?)\b.{0,40}\b(?:marketplace )?account\b",
        r"\b(?:login|password)\b.{0,30}\bfor your\b",
        r"using your profile",
    ),
}
_SCAM_FLAGS = {
    RiskFlag.OFF_PLATFORM,
    RiskFlag.FREE_WORK,
    RiskFlag.UPFRONT_PAYMENT,
    RiskFlag.CREDENTIAL_REQUEST,
}
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_PHONE = re.compile(r"(?<![\w(])(?!\d{4}-\d{2}-\d{2})\+?\(?\d[\d ()-]{7,}\d(?!\w)")
_URL = re.compile(r"\bhttps?://\S+|\bwww\.\S+", re.IGNORECASE)
_HANDLE = re.compile(r"\b(?:telegram|whatsapp|skype|signal|discord)\b[^.!?\n]*", re.IGNORECASE)
_GUARANTEE = re.compile(
    r"\b(?:i |we )?(?:guarantee[ds]?|promise[ds]?|100% (?:success|accura\w+|satisfaction)|"
    r"risk[- ]free)\b",
    re.IGNORECASE,
)
_OFF_PLATFORM_PAYMENT = re.compile(
    r"\b(?:paypal|bank transfer|wire transfer|western union|crypto(?:currency)?|bitcoin)\b",
    re.IGNORECASE,
)
_CREDENTIALS = re.compile(
    r"\b(?:send|share|give)\b.{0,40}\b(?:password|login|credentials?|api key)\b", re.IGNORECASE
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class RiskSignals:
    flags: set[RiskFlag]
    injected_text: list[str]


@dataclass
class ComplianceScan:
    text: str
    findings: list[tuple[str, str]] = field(default_factory=list)
    blocked: bool = False


@contextmanager
def defences_off() -> Iterator[None]:
    """Run without injection defences, only to measure attack success before and after.

    A context switch rather than a setting, so no config value can turn defences off in production.
    Outgoing Compliance checks stay on.
    """
    token = _defences_enabled.set(False)
    try:
        yield
    finally:
        _defences_enabled.reset(token)


def mask_pii(value: Any) -> Any:
    """Mask emails, card numbers and phone numbers in any JSON-like value."""
    if isinstance(value, str):
        masked = _EMAIL.sub("[email]", value)
        masked = _CARD.sub("[payment]", masked)
        return _PHONE.sub("[phone]", masked)
    if isinstance(value, list):
        return [mask_pii(item) for item in value]
    if isinstance(value, dict):
        return {key: mask_pii(item) for key, item in value.items()}
    return value


def delimit_untrusted(label: str, text: str) -> str:
    """Wrap outside text in tags that it cannot close itself."""
    if not _defences_enabled.get():
        return text
    safe = text.replace("</", "<\\/")
    return f"<untrusted_{label}>\n{safe}\n</untrusted_{label}>"


def scan_job_post(text: str) -> RiskSignals:
    lowered = " ".join(text.lower().split())
    flags = {
        flag
        for flag, patterns in _RISK_PATTERNS.items()
        if any(re.search(pattern, lowered) for pattern in patterns)
    }
    injected = []
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text) if _defences_enabled.get() else []
    for sentence in sentences:
        if any(re.search(pattern, sentence, re.IGNORECASE) for pattern in _INJECTION_PATTERNS):
            injected.append(sentence.strip())
    if injected:
        flags.add(RiskFlag.INJECTION)
    if flags & _SCAM_FLAGS:
        flags.add(RiskFlag.SCAM)
    return RiskSignals(flags, injected)


def scan_outgoing_text(
    text: str,
    configured: Sequence[tuple[str, re.Pattern[str], bool]] = (),
) -> ComplianceScan:
    """Redact contact details, remove guarantees, and block payment or credential requests.

    configured holds rules defined only in config, as (name, pattern, blocks): a match blocks the
    text, or is redacted when the rule does not block.
    """
    scan = ComplianceScan(text)

    def redact(pattern: re.Pattern[str], rule: str, replacement: str) -> None:
        for match in pattern.finditer(scan.text):
            scan.findings.append((rule, match.group(0)))
        scan.text = pattern.sub(replacement, scan.text)

    redact(_EMAIL, "contact", "[removed]")
    redact(_URL, "contact", "[removed]")
    redact(_PHONE, "contact", "[removed]")
    redact(_HANDLE, "contact", "[removed]")

    kept = []
    for sentence in _SENTENCE.split(scan.text):
        if _GUARANTEE.search(sentence):
            scan.findings.append(("guarantee", sentence))
        else:
            kept.append(sentence)
    scan.text = " ".join(kept)

    for rule, pattern in (("payment", _OFF_PLATFORM_PAYMENT), ("credentials", _CREDENTIALS)):
        for match in pattern.finditer(scan.text):
            scan.findings.append((rule, match.group(0)))
            scan.blocked = True

    for rule, pattern, blocks in configured:
        for match in pattern.finditer(scan.text):
            scan.findings.append((rule, match.group(0)))
            scan.blocked = scan.blocked or blocks
        if not blocks:
            scan.text = pattern.sub("[removed]", scan.text)
    return scan
