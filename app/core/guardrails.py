"""
UniDocVerse Guardrail Engine — 6-layer protection, model-agnostic.

Layer 1  Input         — injection, PII, length, domain scope
Layer 2  Agent Loop    — iteration cap, timeout, tool budget
Layer 3  HITL          — risk tiers, always-HITL tools, approval timeout
Layer 4  MCP Tools     — role allowlist, rate limits, blocked combos
Layer 5  Output        — PII redaction, length cap, toxicity
Layer 6  Audit         — structured log every action, session isolation
"""
import json
import logging
import re
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ── Config path ───────────────────────────────────────────────────────────────
_CONFIG_PATH = Path(__file__).parent / "guardrail_config.json"
_AUDIT_LOG_PATH = Path(__file__).parent.parent.parent / "logs" / "guardrail_audit.jsonl"


def _load_config() -> Dict:
    if _CONFIG_PATH.exists():
        try:
            return json.loads(_CONFIG_PATH.read_text())
        except Exception:
            pass
    return _default_config()


def save_config(data: Dict):
    _CONFIG_PATH.write_text(json.dumps(data, indent=2))


def _default_config() -> Dict:
    return {
        "enabled": True,
        "input": {
            "enabled": True,
            "max_chars": 8000,
            "injection_check": True,
            "pii_check": True,
            "domain_scope_check": True,
        },
        "agent_loop": {
            "enabled": True,
            "max_iterations": 10,
            "timeout_seconds": 60,
            "max_tool_calls": 20,
        },
        "hitl": {
            "enabled": True,
            "approval_timeout_minutes": 30,
            "always_hitl_tools": [
                "send_renewal_reminder", "bind_quote", "move_clm_stage",
                "create_client", "add_client_note", "request_approval",
                "delete_document",
            ],
            "dollar_threshold": 10000,
        },
        "tools": {
            "enabled": True,
            "role_allowlists": {
                "viewer": ["get_document", "list_documents", "search_documents",
                           "get_client", "list_clients", "list_policies",
                           "get_policy", "ask_document", "get_stats",
                           "list_risk_scores", "get_document_risk"],
                "agent":  None,   # None = all tools allowed
                "admin":  None,
            },
            "rate_limits": {
                "send_renewal_reminder": {"max": 10,  "window_seconds": 3600},
                "create_client":         {"max": 50,  "window_seconds": 3600},
                "add_client_note":       {"max": 100, "window_seconds": 3600},
                "bind_quote":            {"max": 5,   "window_seconds": 3600},
                "move_clm_stage":        {"max": 20,  "window_seconds": 3600},
                "delete_document":       {"max": 10,  "window_seconds": 3600},
            },
            "blocked_combos": [
                ["delete_document", "send_renewal_reminder"],
                ["delete_document", "bind_quote"],
            ],
        },
        "output": {
            "enabled": True,
            "max_chars": 12000,
            "pii_redact": True,
            "toxicity_check": True,
        },
        "audit": {
            "enabled": True,
            "retention_days": 90,
        },
    }


# ── Violation ─────────────────────────────────────────────────────────────────

class GuardrailViolation(Exception):
    def __init__(self, layer: str, rule: str, message: str, severity: str = "block"):
        self.layer = layer
        self.rule = rule
        self.message = message
        self.severity = severity   # "block" | "warn" | "redact"
        super().__init__(message)


class RiskTier(Enum):
    LOW      = "low"
    MEDIUM   = "medium"
    HIGH     = "high"
    CRITICAL = "critical"


# ── Layer 1: Input ────────────────────────────────────────────────────────────

_INJECTION_PATTERNS = [
    r"ignore (previous|all|prior|your) instructions",
    r"disregard (your|all|previous|prior)",
    r"you are now (a|an|the)",
    r"pretend (you are|to be)",
    r"act as (if|though|a|an)",
    r"forget (everything|all|your instructions)",
    r"new (instructions|prompt|system prompt)",
    r"(override|bypass|disable) (your|the) (instructions|guardrails|safety|rules|filters)",
    r"reveal (your|the) (system prompt|instructions|prompt)",
    r"DAN (mode|prompt|jailbreak)",
    r"jailbreak",
    r"<\|system\|>",
    r"\[INST\].*\[/INST\]",
    r"</?(system|user|assistant)>",
]
_INJECTION_RE = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]

_PII_PATTERNS = {
    "SSN":             re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "EIN":             re.compile(r"\b\d{2}-\d{7}\b"),
    "credit_card":     re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    "phone":           re.compile(r"\b(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    "email":           re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    "dob":             re.compile(r"\b(0?[1-9]|1[0-2])[-/](0?[1-9]|[12]\d|3[01])[-/](19|20)\d{2}\b"),
    "passport_us":     re.compile(r"\b[A-Z]{1,2}\d{6,9}\b"),
    "uscis_receipt":   re.compile(r"\b(EAC|WAC|SRC|LIN|NBC|IOE|MSC|TSC)\d{10,13}\b"),
    "alien_number":    re.compile(r"\bA[-\s]?\d{8,9}\b", re.IGNORECASE),
    "bank_account":    re.compile(r"\b\d{8,17}\b"),
    "iban":            re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{4,30}\b"),
    "ip_address":      re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    "drivers_license": re.compile(r"\b[A-Z]\d{7}\b"),
}

_DOMAIN_KEYWORDS = {
    "document", "file", "pdf", "upload", "analyze", "search", "query",
    "insurance", "policy", "claim", "client", "renewal", "quote", "coi",
    "hr", "contract", "onboarding", "candidate",
    "finance", "invoice", "bank", "reconciliation", "transaction",
    "legal", "nda", "compliance", "regulatory",
    "medical", "patient", "eob", "prior auth",
    "real estate", "lease", "appraisal", "title",
    "supply chain", "purchase order", "vendor", "shipment",
    "summary", "extract", "report", "dashboard", "analytics",
    "list", "show", "get", "find", "help", "what", "how", "create", "add",
}

_OFF_TOPIC_PATTERNS = [
    r"\b(malware|exploit|hack|crack|phish|ransomware|ddos|botnet)\b",
    r"\b(write me (a|an)? (virus|worm|trojan|keylogger|rootkit))\b",
    r"\b(how to (make|build|create) (a |)(bomb|weapon|poison|drug))\b",
    r"\b(child (porn|abuse|exploit))\b",
    r"\b(generate (fake|forged) (id|passport|license|document))\b",
]
_OFF_TOPIC_RE = [re.compile(p, re.IGNORECASE) for p in _OFF_TOPIC_PATTERNS]


def check_input(text: str, session_id: str = "") -> Dict:
    """
    Run all Layer 1 checks on user input.
    Returns {"ok": True} or raises GuardrailViolation.
    """
    cfg = _load_config().get("input", {})
    if not cfg.get("enabled", True):
        return {"ok": True}

    # Length
    if len(text) > cfg.get("max_chars", 8000):
        raise GuardrailViolation(
            "input", "max_length",
            f"Input too long ({len(text)} chars). Maximum is {cfg['max_chars']}.",
        )

    # Prompt injection
    if cfg.get("injection_check", True):
        for pattern in _INJECTION_RE:
            if pattern.search(text):
                _audit("input.injection_blocked", session_id, {"snippet": text[:120]})
                raise GuardrailViolation(
                    "input", "prompt_injection",
                    "Prompt injection attempt detected and blocked.",
                )

    # Off-topic / harmful content
    if cfg.get("domain_scope_check", True):
        for pattern in _OFF_TOPIC_RE:
            if pattern.search(text):
                _audit("input.off_topic_blocked", session_id, {"snippet": text[:120]})
                raise GuardrailViolation(
                    "input", "off_topic",
                    "This request is outside UniDocVerse's scope and has been blocked.",
                )

    # PII warning (don't block, but log)
    if cfg.get("pii_check", True):
        for pii_type, pattern in _PII_PATTERNS.items():
            if pattern.search(text):
                _audit("input.pii_detected", session_id, {"pii_type": pii_type})
                logger.warning("PII detected in input (%s) — session %s", pii_type, session_id)
                # Warn but don't block — user might legitimately query their own data

    _audit("input.passed", session_id, {"length": len(text)})
    return {"ok": True}


# ── Layer 2: Agent Loop ───────────────────────────────────────────────────────

class AgentLoopGuard:
    """
    Attach one instance per agent run. Call .tick() before each iteration,
    .tool_called() before each tool invocation.
    """

    def __init__(self, session_id: str = ""):
        cfg = _load_config().get("agent_loop", {})
        self.session_id       = session_id or str(uuid.uuid4())
        self.max_iterations   = cfg.get("max_iterations", 10)
        self.timeout_seconds  = cfg.get("timeout_seconds", 60)
        self.max_tool_calls   = cfg.get("max_tool_calls", 20)
        self._iteration       = 0
        self._tool_calls      = 0
        self._start_time      = time.monotonic()
        self._seen_tool_calls: List[str] = []
        self.enabled          = cfg.get("enabled", True)

    def tick(self, iteration: int = None):
        if not self.enabled:
            return
        self._iteration = iteration if iteration is not None else self._iteration + 1
        elapsed = time.monotonic() - self._start_time

        if elapsed > self.timeout_seconds:
            _audit("loop.timeout", self.session_id,
                   {"elapsed": elapsed, "iterations": self._iteration})
            raise GuardrailViolation(
                "agent_loop", "timeout",
                f"Agent run exceeded {self.timeout_seconds}s timeout after "
                f"{self._iteration} iterations.",
            )

        if self._iteration > self.max_iterations:
            _audit("loop.max_iterations", self.session_id,
                   {"iterations": self._iteration})
            raise GuardrailViolation(
                "agent_loop", "max_iterations",
                f"Agent stopped after {self.max_iterations} iterations to prevent "
                f"infinite loops.",
            )

    def tool_called(self, tool_name: str, args: Dict = None):
        if not self.enabled:
            return
        self._tool_calls += 1
        self._seen_tool_calls.append(tool_name)

        if self._tool_calls > self.max_tool_calls:
            _audit("loop.tool_budget_exceeded", self.session_id,
                   {"tool_calls": self._tool_calls, "last_tool": tool_name})
            raise GuardrailViolation(
                "agent_loop", "tool_budget",
                f"Agent exceeded tool budget ({self.max_tool_calls} calls).",
            )

        # Recursion / cycle: same tool called 5+ times in a row
        recent = self._seen_tool_calls[-5:]
        if len(recent) == 5 and len(set(recent)) == 1:
            _audit("loop.tool_cycle", self.session_id, {"tool": tool_name})
            raise GuardrailViolation(
                "agent_loop", "tool_cycle",
                f"Tool '{tool_name}' called 5 consecutive times — possible loop.",
            )

    def summary(self) -> Dict:
        return {
            "iterations": self._iteration,
            "tool_calls": self._tool_calls,
            "elapsed_seconds": round(time.monotonic() - self._start_time, 2),
        }


# ── Layer 3: HITL ────────────────────────────────────────────────────────────

def tool_risk_tier(tool_name: str) -> RiskTier:
    """Classify a tool by risk regardless of the global HITL toggle."""
    cfg = _load_config()
    always_hitl: List[str] = cfg.get("hitl", {}).get("always_hitl_tools", [])
    if tool_name in always_hitl:
        return RiskTier.CRITICAL

    _high = {"bind_quote", "move_clm_stage", "send_renewal_reminder",
             "delete_document", "add_client_note"}
    _medium = {"create_client", "update_policy", "create_quote"}
    if tool_name in _high:
        return RiskTier.HIGH
    if tool_name in _medium:
        return RiskTier.MEDIUM
    return RiskTier.LOW


def requires_hitl(tool_name: str, hitl_globally_enabled: bool) -> bool:
    """
    CRITICAL tools always require HITL.
    HIGH + MEDIUM tools require HITL when the global toggle is on.
    """
    tier = tool_risk_tier(tool_name)
    if tier == RiskTier.CRITICAL:
        return True
    if tier in (RiskTier.HIGH, RiskTier.MEDIUM) and hitl_globally_enabled:
        return True
    return False


def check_approval_timeout(created_at: datetime, session_id: str = "") -> bool:
    """Return True if an approval request has timed out and should be auto-rejected."""
    cfg = _load_config().get("hitl", {})
    timeout_min = cfg.get("approval_timeout_minutes", 30)
    expired = datetime.utcnow() > created_at + timedelta(minutes=timeout_min)
    if expired:
        _audit("hitl.approval_timeout", session_id,
               {"created_at": created_at.isoformat(), "timeout_min": timeout_min})
    return expired


def check_dollar_threshold(amount: float, tool_name: str, session_id: str = "") -> bool:
    """Return True if a dollar amount exceeds the threshold and must be escalated."""
    threshold = _load_config().get("hitl", {}).get("dollar_threshold", 10000)
    if amount > threshold:
        _audit("hitl.dollar_threshold_exceeded", session_id,
               {"amount": amount, "threshold": threshold, "tool": tool_name})
        return True
    return False


# ── Layer 4: MCP Tools ────────────────────────────────────────────────────────

# In-memory sliding-window rate limiter
_rate_windows: Dict[str, List[float]] = defaultdict(list)
_rate_lock = Lock()

# Per-session tool call tracker for combo detection
_session_tools: Dict[str, List[str]] = defaultdict(list)


def check_tool_allowed(tool_name: str, role: str = "agent", session_id: str = "") -> bool:
    """
    Check if a role is allowed to call a tool.
    Raises GuardrailViolation if not.
    """
    cfg = _load_config().get("tools", {})
    if not cfg.get("enabled", True):
        return True

    allowlists: Dict = cfg.get("role_allowlists", {})
    allowed = allowlists.get(role)
    if allowed is None:
        return True   # None = unrestricted
    if tool_name not in allowed:
        _audit("tool.role_blocked", session_id, {"tool": tool_name, "role": role})
        raise GuardrailViolation(
            "tools", "role_not_allowed",
            f"Role '{role}' is not permitted to call tool '{tool_name}'.",
        )
    return True


def check_tool_rate_limit(tool_name: str, session_id: str = "") -> bool:
    """Sliding-window rate limiter. Raises GuardrailViolation if exceeded."""
    cfg = _load_config().get("tools", {})
    if not cfg.get("enabled", True):
        return True

    limits: Dict = cfg.get("rate_limits", {})
    if tool_name not in limits:
        return True

    limit = limits[tool_name]
    max_calls     = limit.get("max", 100)
    window_secs   = limit.get("window_seconds", 3600)
    key           = f"{tool_name}:{session_id}"
    now           = time.monotonic()

    with _rate_lock:
        calls = _rate_windows[key]
        # Prune calls outside the window
        _rate_windows[key] = [t for t in calls if now - t < window_secs]
        if len(_rate_windows[key]) >= max_calls:
            _audit("tool.rate_limit_exceeded", session_id,
                   {"tool": tool_name, "calls": len(_rate_windows[key]), "max": max_calls})
            raise GuardrailViolation(
                "tools", "rate_limit",
                f"Tool '{tool_name}' rate limit exceeded "
                f"({max_calls} calls per {window_secs}s).",
            )
        _rate_windows[key].append(now)
    return True


def check_tool_combo(tool_name: str, session_id: str) -> bool:
    """Detect blocked tool combinations within a session."""
    cfg = _load_config().get("tools", {})
    if not cfg.get("enabled", True):
        return True

    blocked_combos: List[List[str]] = cfg.get("blocked_combos", [])
    history: Set[str] = set(_session_tools[session_id])
    history.add(tool_name)

    for combo in blocked_combos:
        if all(t in history for t in combo):
            _audit("tool.blocked_combo", session_id,
                   {"tool": tool_name, "combo": combo})
            raise GuardrailViolation(
                "tools", "blocked_combo",
                f"Tool combination {combo} is not permitted in a single session.",
            )

    _session_tools[session_id].append(tool_name)
    return True


def run_tool_guardrails(tool_name: str, role: str = "agent", session_id: str = "") -> bool:
    """Run all Layer 4 checks in order. Raises on first violation."""
    check_tool_allowed(tool_name, role, session_id)
    check_tool_rate_limit(tool_name, session_id)
    check_tool_combo(tool_name, session_id)
    _audit("tool.allowed", session_id, {"tool": tool_name, "role": role})
    return True


# ── Layer 5: Output ───────────────────────────────────────────────────────────

_PII_REDACT_PATTERNS = {
    # ── Govt / Tax IDs ────────────────────────────────────────────────────────
    "SSN_ITIN":        (re.compile(r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b"),                            "[SSN/ITIN REDACTED]"),
    "EIN":             (re.compile(r"\b\d{2}-\d{7}\b"),                                           "[EIN REDACTED]"),
    # ── Financial ─────────────────────────────────────────────────────────────
    "credit_card":     (re.compile(r"\b(?:\d[ -]?){13,16}\b"),                                    "[CARD# REDACTED]"),
    "bank_routing":    (re.compile(r"\b\d{9}\b"),                                                  "[ROUTING# REDACTED]"),
    "iban":            (re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),                          "[IBAN REDACTED]"),
    # ── Contact ───────────────────────────────────────────────────────────────
    "phone":           (re.compile(r"\b(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),    "[PHONE REDACTED]"),
    "email":           (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),     "[EMAIL REDACTED]"),
    "ip_address":      (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),                               "[IP REDACTED]"),
    # ── Dates that are likely DOB (MM/DD/YYYY, MM-DD-YYYY) ───────────────────
    "dob":             (re.compile(r"\b(0?[1-9]|1[0-2])[-/](0?[1-9]|[12]\d|3[01])[-/](19|20)\d{2}\b"), "[DOB REDACTED]"),
    # ── Immigration / Visa ────────────────────────────────────────────────────
    "uscis_receipt":   (re.compile(r"\b(EAC|WAC|SRC|LIN|NBC|IOE|MSC|TSC|ZAR|YSC)\d{10,13}\b",
                                   re.IGNORECASE),                                                  "[USCIS# REDACTED]"),
    "alien_number":    (re.compile(r"\bA[-\s]?\d{8,9}\b", re.IGNORECASE),                         "[A# REDACTED]"),
    "h1b_i94":         (re.compile(r"\b[I]-?\s*9\s*4\s*#?\s*:?\s*[\dA-Z]{10,11}\b",
                                   re.IGNORECASE),                                                  "[I-94# REDACTED]"),
    "visa_number":     (re.compile(r"\bVISA\s*#?\s*:?\s*[A-Z0-9]{8,12}\b", re.IGNORECASE),        "[VISA# REDACTED]"),
    # ── Vehicle ───────────────────────────────────────────────────────────────
    "vin":             (re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b"),                                    "[VIN REDACTED]"),
    # ── Insurance / Policy ────────────────────────────────────────────────────
    "policy_number":   (re.compile(r"\b\d{3,6}-\d{2}-\d{2}-\d{2,}\b"),                            "[POLICY# REDACTED]"),
    # ── Passports / Licenses ─────────────────────────────────────────────────
    "passport_us":     (re.compile(r"\b[A-Z]{1,2}\d{6,9}\b"),                                     "[PASSPORT# REDACTED]"),
    "drivers_license": (re.compile(r"\b[A-Z]\d{7}\b"),                                            "[DL# REDACTED]"),
    # ── Street addresses in free text (number + street name + type) ─────────
    "street_address":  (re.compile(
                            r'\b\d{1,6}\s+[A-Za-z][A-Za-z\s]{2,30}'
                            r'(?:St|Ave|Rd|Blvd|Dr|Ln|Way|Ct|Pl|Cir|Pkwy|Hwy|Terr?|Trl?|Loop)\b',
                            re.IGNORECASE
                        ),                                                                           "[ADDRESS REDACTED]"),
    # ── Catch-all: any standalone number 5+ digits (wages, account #, etc.) ──
    # Applied LAST so more-specific patterns above take priority.
    "id_number_5plus": (re.compile(r"\b\d{5,}\b"),                                                 "[ID# REDACTED]"),
    # ── Medical record / NPI ─────────────────────────────────────────────────
    "npi":             (re.compile(r"\bNPI\s*#?\s*:?\s*\d{10}\b", re.IGNORECASE),                  "[NPI REDACTED]"),
    "mrn":             (re.compile(r"\bMRN\s*#?\s*:?\s*[A-Z0-9]{5,12}\b", re.IGNORECASE),         "[MRN REDACTED]"),
    # ── W2 / Payroll box numbers ──────────────────────────────────────────────
    "ein_w2":          (re.compile(r"\b\d{2}-\d{7}\b"),                                            "[EIN REDACTED]"),
    # ── Supply-chain / BoL tracking ──────────────────────────────────────────
    "tracking":        (re.compile(r"\b(1Z[A-Z0-9]{16}|9[24]\d{20}|\d{12,22})\b"),                "[TRACKING# REDACTED]"),
    # ── Catch-all: hyphenated / slash-separated identifiers like XX-XXXXXX-XX ─
    "hyphenated_id":   (re.compile(r"\b[A-Z0-9]{2,}-[A-Z0-9]{2,}-[A-Z0-9]{2,}(?:-[A-Z0-9]{1,})?\b",
                                   re.IGNORECASE),                                                  "[REF# REDACTED]"),
    # ── All-caps proper names (as they appear in official documents) ──────────
    # Catches: "VIJAY K BOMMA", "JOHN M DOE", "MARY JANE WATSON" etc.
    # Pattern: 2+ uppercase sequences optionally with a single-letter middle initial
    "allcaps_name":    (re.compile(
                            r'\b[A-Z]{2,}(?:\s+[A-Z]\s+[A-Z]{2,}|\s+[A-Z]{2,}){1,4}\b'
                        ),                                                                           "[NAME REDACTED]"),
}

_TOXIC_PATTERNS = [
    re.compile(r"\b(kill|murder|rape|torture)\s+(yourself|himself|herself|themselves)\b", re.I),
    re.compile(r"\b(bomb|attack|shoot)\s+(the|a|an)?\s*(school|church|mosque|synagogue|building)\b", re.I),
]


def sanitize_output(text: str, session_id: str = "") -> str:
    """
    Run Layer 5 output guardrails:
    1. Truncate to max_chars
    2. Redact PII
    3. Block toxic content
    Returns sanitized string.
    """
    cfg = _load_config().get("output", {})
    if not cfg.get("enabled", True):
        return text

    # Length cap
    max_chars = cfg.get("max_chars", 12000)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[Response truncated by output guardrail]"
        _audit("output.truncated", session_id, {"original_len": len(text)})

    # PII redaction
    if cfg.get("pii_redact", True):
        for pii_type, (pattern, replacement) in _PII_REDACT_PATTERNS.items():
            before = len(pattern.findall(text))
            text = pattern.sub(replacement, text)
            if before:
                _audit("output.pii_redacted", session_id, {"pii_type": pii_type, "count": before})

    # Toxicity
    if cfg.get("toxicity_check", True):
        for pattern in _TOXIC_PATTERNS:
            if pattern.search(text):
                _audit("output.toxic_blocked", session_id, {})
                raise GuardrailViolation(
                    "output", "toxic_content",
                    "Output contained harmful content and was blocked.",
                    severity="block",
                )

    return text


# ── Layer 6: Audit ────────────────────────────────────────────────────────────

def _audit(event: str, session_id: str, data: Dict = None):
    """Append a structured audit event to the JSONL log."""
    cfg = _load_config().get("audit", {})
    if not cfg.get("enabled", True):
        return

    entry = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "event": event,
        "session_id": session_id or "unknown",
        "data": data or {},
    }
    try:
        _AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_AUDIT_LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.warning("Audit log write failed: %s", e)


def read_audit_log(
    session_id: str = None,
    event_prefix: str = None,
    since_hours: int = 24,
    limit: int = 200,
) -> List[Dict]:
    """Read recent audit events, optionally filtered."""
    if not _AUDIT_LOG_PATH.exists():
        return []
    since = datetime.utcnow() - timedelta(hours=since_hours)
    results = []
    try:
        with open(_AUDIT_LOG_PATH) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    ts = datetime.fromisoformat(entry["ts"].rstrip("Z"))
                    if ts < since:
                        continue
                    if session_id and entry.get("session_id") != session_id:
                        continue
                    if event_prefix and not entry.get("event", "").startswith(event_prefix):
                        continue
                    results.append(entry)
                except Exception:
                    continue
    except Exception:
        pass
    return results[-limit:]


def purge_old_audit_logs():
    """Delete audit entries older than retention_days."""
    cfg = _load_config().get("audit", {})
    retention = cfg.get("retention_days", 90)
    if not _AUDIT_LOG_PATH.exists():
        return
    cutoff = datetime.utcnow() - timedelta(days=retention)
    kept = []
    try:
        with open(_AUDIT_LOG_PATH) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    ts = datetime.fromisoformat(entry["ts"].rstrip("Z"))
                    if ts >= cutoff:
                        kept.append(line)
                except Exception:
                    kept.append(line)
        with open(_AUDIT_LOG_PATH, "w") as f:
            f.writelines(kept)
        logger.info("Audit log purged entries older than %d days", retention)
    except Exception as e:
        logger.warning("Audit log purge failed: %s", e)


# ── MCP Tool Result Sanitizer ─────────────────────────────────────────────────

# Fields that should never be sent to an external LLM (raw OCR text is huge and
# contains dense PII; internal DB fields are not useful to the model).
# Internal/bulk fields — never sent to any LLM client.
_MCP_STRIP_FIELDS = {
    "raw_text", "raw_extracted", "ocr_raw", "full_text", "extracted_text",
    "embedding", "has_embedding",
    "password", "token", "secret", "api_key", "hashed_password",
    # Raw spreadsheet/table data — too large and contains unredacted rows.
    # Analytics endpoints provide safe aggregated summaries instead.
    "tables", "rows", "raw_data", "sheet_data",
    # Internal storage paths expose server filesystem layout.
    "file_path", "storage_path", "upload_path",
    # Chunk internals — raw_chunk holds pre-redaction text; chunk_metadata
    # is internal provenance only, never useful to an LLM client.
    "raw_chunk", "chunk_metadata",
}

# PII-bearing field names — stripped in full regardless of value type.
# Principle: ANY field whose name suggests it holds an identifier, name,
# contact detail, or free-form text is stripped here. Regex redaction
# (_redact_string) then catches PII that leaks through permitted fields.
# Coverage: Insurance · HR/Payroll · Immigration · Medical · Legal ·
#           Real-Estate · Finance · Supply-Chain · General
_MCP_STRIP_PII_FIELDS = {
    # ── People / entity names (all domains) ──────────────────────────────────
    "name", "full_name", "fullname", "display_name", "legal_name",
    "first_name", "firstname", "last_name", "lastname",
    "middle_name", "middlename", "suffix", "prefix", "salutation",
    "b_name", "m_name",                             # short-form name fields
    # Roles — Insurance
    "customer_name", "customername", "client_name", "clientname",
    "named_insured", "named_insured_name", "insured", "insured_name",
    "co_insured", "policyholder_name", "insurer_name",
    "claimant_name", "claimant", "adjuster_name",
    "beneficiary_name", "guarantor_name", "guarantor",
    "agent_name", "rep_name", "broker_name", "carrier_name",
    "salesperson", "salesperson_name",
    # Roles — Medical
    "patient_name", "patientname", "patient",
    "physician", "ordering_physician", "physician_name", "doctor_name",
    "provider_name", "provider", "payer_name",
    # Roles — HR / Employment
    "employee_name", "employeename", "employee",
    "employer_name", "employer_sponsor", "manager_name",
    "candidate_name", "candidate", "reviewer_name", "auditor_name",
    # Roles — Legal / Real-Estate / Finance
    "owner_name", "grantor", "grantee", "lessee", "lessor",
    "trustee", "trustor", "testator_name", "executor_name", "principal_name",
    "attorney_name", "paralegal_name",
    "buyer", "buyer_name", "seller",
    "borrower", "borrower_name", "co_borrower",
    "lender_name", "mortgagee", "appraiser_name", "inspector_name",
    "inventor_name", "graduate_name",
    # Roles — Finance / Supply-Chain
    "vendor_name", "supplier_name",
    "sender_name", "receiver_name", "recipient_name",
    "merchant_name", "card_name", "cardholder_name",
    "top_merchant_1_name", "top_merchant_2_name", "top_merchant_3_name",
    "top_merchant_4_name", "top_merchant_5_name", "top_merchant_name",
    "payer", "payee",
    # Roles — Immigration
    "petitioner", "sponsor", "beneficiary", "applicant", "co_applicant",
    "employer", "employer_contact",
    # Other name variants
    "contact_name", "org_name", "entity_name",
    "business_name", "company_name", "dba_name",
    "representative_name", "person_name",
    # ── Contact details ───────────────────────────────────────────────────────
    "phone", "phone_number", "mobile", "mobile_number", "cell", "cell_number",
    "fax", "fax_number", "telephone", "home_phone", "work_phone",
    "email", "email_address", "work_email", "personal_email",
    "vendor_email", "vendor_phone",
    # ── Addresses (all domains) ───────────────────────────────────────────────
    "address", "addr", "address1", "address2", "address3",
    "address_line1", "address_line2", "address_line_1", "address_line_2",
    "street", "street_address", "street1", "street2",
    "city", "state", "zip", "zip_code", "zipcode", "postal_code", "postalcode",
    "country", "county", "district", "province",
    "mailing_address", "billing_address", "shipping_address", "home_address",
    "work_address", "property_address", "client_address",
    "employee_address", "employer_address", "merchant_address",
    "vendor_address", "location", "residence",
    # ── Government / Tax / Immigration IDs ───────────────────────────────────
    "ssn", "social_security", "social_security_number",
    "social_security_wages", "employee_ssn", "employee_ssn_masked",
    "itin", "sin",
    "tin", "tax_id", "tax_id_number", "taxpayer_id",
    "ein", "employer_ein", "employer_state_id",
    "employer_identification_number", "vendor_tax_id",
    "vat_number", "vat_id", "gst_number",
    "dob", "date_of_birth", "birth_date", "birthdate", "birthday", "patient_dob",
    "passport_number", "passport_no", "passport_id", "passport",
    "document_number", "id_number", "national_id", "immigration_id",
    "alien_number", "alien_reg_number",
    "uscis_receipt", "uscis_number", "receipt_number", "immigration_receipt",
    "visa_number", "visa_class", "visa", "visa_type",
    "i94_number", "i94", "h1b", "h1b_number", "h1b_case",
    "green_card_number", "lpr_number",
    "drivers_license", "dl_number", "license_number", "license_no",
    "registration_number", "certificate_number",
    "application_number", "declaration_number",
    # ── Financial account identifiers ─────────────────────────────────────────
    "bank_account", "account_number", "account_no", "acct_number",
    "account_name", "account_holder", "account_information",
    "routing_number", "routing_no", "aba_number",
    "iban", "bic", "swift", "swift_code", "swift_bic",
    "card_number", "credit_card", "debit_card", "card_no",
    "sender_account", "receiver_account", "beneficiary_account",
    "wire_id",
    # ── Balances / amounts that are personal financial data ───────────────────
    "balance", "current_balance", "new_balance", "previous_balance",
    "statement_balance", "escrow_balance", "rewards_balance",
    "suspense_balance", "principal_balance",
    "salary", "base_salary", "salary_range",
    "wages", "total_wages", "w2_wages", "w2_income",
    "box1_wages", "box3_ss_wages", "wages_tips_compensation",
    "medicare_wages", "medicare_wages_tips", "state_wages",
    "federal_income_tax_withheld", "state_income_tax",
    "income", "total_income", "average_income", "annualized_gross",
    "loan_amount", "loan_number", "loan_id",
    # ── Vehicle / Insurance ───────────────────────────────────────────────────
    "vin", "vehicle_vin", "vin_number", "vehicle_id", "vehicle_identification",
    "license_plate", "plate_number", "tag_number", "vehicle_number",
    "policy_number", "policy_no", "policy_num", "policy_id", "policy_ref",
    "claim_number", "claim_no", "claim_ref", "claim_id", "claimant_id",
    "quote_number", "quote_ref", "quote_id",
    "group_number", "control_number",
    # ── Medical ───────────────────────────────────────────────────────────────
    "patient_id", "patient_number", "patient_no", "mrn", "medical_record",
    "npi", "npi_number", "provider_id", "provider_npi", "dea_number",
    "member_id", "member_number", "member_no",
    "patient_demographics", "patient_coverage", "patient_responsibility",
    "patient_treatment",
    # ── HR / Employment ───────────────────────────────────────────────────────
    "employee_id", "employee_number", "employee_no", "emp_id",
    "candidate_id", "student_id",
    # ── Legal ─────────────────────────────────────────────────────────────────
    "matter_number", "matter_ref", "matter_id",
    "case_number", "case_ref",
    "contract_number", "contract_ref", "contract_id",
    "patent_number", "rfp_number",
    "client_number", "client_ref",
    # ── Real-Estate / Finance ─────────────────────────────────────────────────
    "property_id", "parcel_id", "deed_id", "appraisal_number",
    "mortgage_number", "account_id",
    # ── Supply-Chain ─────────────────────────────────────────────────────────
    "order_id", "order_number", "order_no", "order_ref",
    "invoice_number", "invoice_ref", "invoice_no", "invoice_id",
    "bol_number", "shipment_id", "tracking_number",
    "po_number", "reference_number",
    "vendor_id", "supplier_id",
    # ── Other document / system identifiers ──────────────────────────────────
    "beneficiary_id", "beneficiary_number",
    "receipt_id", "job_id", "batch_id", "run_id",
    "credit_memo_number", "delivery_receipt",
    # ── Free-text / narrative fields (contain PII verbatim) ──────────────────
    "notes", "note", "free_text", "comments", "comment", "remarks",
    "narrative", "description", "details", "memo", "summary_text",
    "extracted_fields", "raw_fields", "fields",
    "text", "body", "content", "message_body",
    "additional_info", "other_info", "misc",
    "adjuster_report", "adjuster_contact",
    "account_summary", "account_information",
}


def _redact_string(value: str, session_id: str = "") -> str:
    """Apply all PII redaction patterns to a string value.

    All-caps name detection runs FIRST so it operates on the original text
    before other patterns inject uppercase 'REDACTED' tokens that would
    themselves match the name regex.
    """
    # Pass 1: all-caps proper names (must run before other replacements)
    _name_entry = _PII_REDACT_PATTERNS.get("allcaps_name")
    if _name_entry:
        _np, _nr = _name_entry
        _nc = len(_np.findall(value))
        if _nc:
            value = _np.sub(_nr, value)
            _audit("mcp.field_pii_redacted", session_id, {"pii_type": "allcaps_name", "count": _nc})

    # Pass 2: all other patterns
    for pii_type, (pattern, replacement) in _PII_REDACT_PATTERNS.items():
        if pii_type == "allcaps_name":
            continue  # already applied above
        count = len(pattern.findall(value))
        if count:
            value = pattern.sub(replacement, value)
            _audit("mcp.field_pii_redacted", session_id, {"pii_type": pii_type, "count": count})
    return value


# Filename fields — keep the extension (useful for type context) but strip the
# basename which may contain patient/client names or ID numbers.
_MCP_FILENAME_FIELDS = {"filename", "file_name", "original_filename"}


def sanitize_mcp_result(result: Any, session_id: str = "", _depth: int = 0) -> Any:
    """
    Recursively clean a tool result before it leaves the server toward an LLM client.

    Pass order:
      1. Strip bulk/internal fields (raw_text, embedding, tables, file_path …)
      2. Strip PII-bearing named fields (names, addresses, IDs, free-text …)
      3. Regex-redact structured PII (SSN, email, phone …) from all remaining strings
      4. Truncate any single string > 4000 chars
      5. Audit every strip/redaction

    Safe to call on dict, list, str, or any scalar.
    """
    if _depth > 10:
        return result

    if isinstance(result, dict):
        cleaned: Dict = {}
        for k, v in result.items():
            key_lower = k.lower()
            if key_lower in _MCP_STRIP_FIELDS:
                _audit("mcp.field_stripped", session_id, {"field": k})
                continue
            if key_lower in _MCP_STRIP_PII_FIELDS:
                _audit("mcp.pii_field_stripped", session_id, {"field": k})
                continue
            # Filename fields: keep only the extension, drop the basename which
            # may contain patient/client names or identifier numbers.
            if key_lower in _MCP_FILENAME_FIELDS and isinstance(v, str):
                import os as _os
                ext = _os.path.splitext(v)[1] or ""
                cleaned[k] = f"[redacted]{ext}"
                _audit("mcp.filename_redacted", session_id, {"ext": ext})
                continue
            cleaned[k] = sanitize_mcp_result(v, session_id, _depth + 1)
        return cleaned

    if isinstance(result, list):
        return [sanitize_mcp_result(item, session_id, _depth + 1) for item in result]

    if isinstance(result, str):
        if len(result) > 4000:
            result = result[:4000] + " … [truncated by MCP guardrail]"
            _audit("mcp.string_truncated", session_id, {})
        return _redact_string(result, session_id)

    return result


def guard_mcp_tool_args(tool_name: str, args: Dict, session_id: str = "") -> Dict:
    """
    Layer 1 check on MCP tool arguments — catches injection attempts embedded
    in tool call parameters (e.g. a client name field containing prompt injection).
    Returns args unchanged or raises GuardrailViolation.
    """
    for k, v in args.items():
        if isinstance(v, str) and v:
            try:
                check_input(v, session_id)
            except GuardrailViolation as e:
                _audit("mcp.arg_injection_blocked", session_id,
                       {"tool": tool_name, "arg": k, "rule": e.rule})
                raise


    return args


# ── Convenience: full input+output pipeline ───────────────────────────────────

def guard_input(text: str, session_id: str = "") -> str:
    """Call before sending user text to any LLM. Returns text unchanged or raises."""
    _audit("input.received", session_id, {"length": len(text)})
    check_input(text, session_id)
    return text


def guard_output(text: str, session_id: str = "") -> str:
    """Call on any LLM/agent output before returning to the client."""
    result = sanitize_output(text, session_id)
    _audit("output.delivered", session_id, {"length": len(result)})
    return result
