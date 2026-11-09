"""Deterministic safety checks that no model output can switch off."""

import re
from dataclasses import dataclass, field

from agent.handoffs import RiskFlag

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


def delimit_untrusted(label: str, text: str) -> str:
    """Wrap outside text in tags that it cannot close itself."""
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
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        if any(re.search(pattern, sentence, re.IGNORECASE) for pattern in _INJECTION_PATTERNS):
            injected.append(sentence.strip())
    if injected:
        flags.add(RiskFlag.INJECTION)
    if flags & _SCAM_FLAGS:
        flags.add(RiskFlag.SCAM)
    return RiskSignals(flags, injected)


def scan_outgoing_text(text: str) -> ComplianceScan:
    """Redact contact details, remove guarantees, and block payment or credential requests."""
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
    return scan
