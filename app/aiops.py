"""AIOps incident assistant.

Turns host metrics + recent log lines into an ITSM-style incident ticket.

Flow:
  1. redact()        mask IPs, emails, secrets and tokens before anything leaves the host
  2. rule_based()    deterministic triage that always works (no API key needed)
  3. ask_llm()       optional: send the *redacted* evidence to an LLM (Google Gemini)
  4. validate()      reject malformed LLM output and fall back to the rule-based ticket
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

PRIORITIES = ("P1", "P2", "P3", "P4")
TICKET_FIELDS = ("title", "priority", "affected_service", "summary", "likely_cause", "next_steps")

# ---------------------------------------------------------------- 1. redaction
_REDACTIONS = [
    # secrets first, so later patterns don't partially match them
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[AWS_KEY]"),
    (re.compile(r"(?i)bearer\s+[a-z0-9._\-]+"), "Bearer [TOKEN]"),
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key)\s*[=:]\s*\S+"), r"\1=[SECRET]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[EMAIL]"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[IP]"),
]


def redact(text: str) -> tuple[str, int]:
    """Mask sensitive values. Returns (clean_text, number_of_replacements)."""
    total = 0
    for pattern, repl in _REDACTIONS:
        text, n = pattern.subn(repl, text)
        total += n
    return text, total


# ------------------------------------------------------ 2. rule-based triage
_ERROR_WORDS = re.compile(r"\b(error|exception|failed|failure|fatal|critical|oom|killed|timeout|refused)\b", re.I)
_CAUSE_HINTS = [
    (re.compile(r"no space left|disk (?:is )?full", re.I), "Disk is full or nearly full"),
    (re.compile(r"out of memory|oom|killed process", re.I), "Process killed or degraded due to memory pressure"),
    (re.compile(r"connection refused|could not connect|timeout|timed out", re.I), "A dependency is unreachable or timing out"),
    (re.compile(r"permission denied|unauthori[sz]ed|forbidden|403|401", re.I), "Access or permission problem"),
]


def _service_from_logs(lines: list[str]) -> str:
    for line in lines:
        m = re.search(r"\b([a-z][a-z0-9_-]+)(?:\[\d+\])?:", line)
        if m:
            return m.group(1)
    return "infra-health-monitor"


def rule_based(metrics: dict, logs: list[str]) -> dict:
    """Deterministic triage used as the baseline and the fallback."""
    alerts = metrics.get("alerts", [])
    errors = [line for line in logs if _ERROR_WORDS.search(line)]
    text = "\n".join(logs)

    cause = next((hint for pattern, hint in _CAUSE_HINTS if pattern.search(text)), None)
    if not cause and alerts:
        cause = "Resource usage above threshold: " + "; ".join(alerts)

    disk = metrics.get("disk_percent", 0)
    mem = metrics.get("memory_percent", 0)
    if disk >= 95 or mem >= 95 or re.search(r"fatal|no space left|out of memory", text, re.I):
        priority = "P1"
    elif alerts or len(errors) >= 5:
        priority = "P2"
    elif errors or cause:
        priority = "P3"
    else:
        priority = "P4"

    steps = {
        "P1": ["Page the on-call engineer", "Free disk/memory or restart the affected service", "Confirm recovery on /health and /metrics"],
        "P2": ["Investigate the top error in the logs", "Check recent deployments or config changes", "Watch /metrics for the next 15 minutes"],
        "P3": ["Create a ticket and review the errors during business hours"],
        "P4": ["No action needed; keep monitoring"],
    }[priority]

    service = _service_from_logs(errors or logs)
    title = cause or (f"{len(errors)} error(s) in {service} logs" if errors else "System healthy")
    return {
        "title": title,
        "priority": priority,
        "affected_service": service,
        "summary": f"{len(alerts)} metric alert(s) and {len(errors)} error log line(s) in the analysed window.",
        "likely_cause": cause or ("Unknown; see error lines" if errors else "None"),
        "next_steps": steps,
    }


# ------------------------------------------------------------------ 3. LLM
PROMPT_TEMPLATE = """You are an SRE assistant triaging an incident for an IT operations team.
Use ONLY the evidence below. Do not invent facts. If the evidence is insufficient, say so in likely_cause.

Priority guide: P1 = outage or data-loss risk now; P2 = degraded, needs action today;
P3 = minor errors, handle in business hours; P4 = healthy / informational.

Return ONLY a JSON object with exactly these keys:
title (short string), priority (one of P1, P2, P3, P4), affected_service (string),
summary (1-2 sentences), likely_cause (string), next_steps (list of up to 4 short strings).

METRICS:
{metrics}

RECENT LOG LINES:
{logs}
"""


def build_prompt(metrics: dict, logs: list[str]) -> tuple[str, int]:
    """Build the LLM prompt from *redacted* evidence. Returns (prompt, redaction_count)."""
    clean_logs, n_logs = redact("\n".join(logs[-50:]))  # cap the context we send
    clean_metrics, n_metrics = redact(json.dumps(metrics, indent=1))
    return PROMPT_TEMPLATE.format(metrics=clean_metrics, logs=clean_logs or "(none)"), n_logs + n_metrics


RETRYABLE_STATUS = {429, 500, 502, 503, 504}  # rate limit / overloaded / transient


def ask_llm(prompt: str, timeout: float = 60, retries: int = 3, backoff: float = 2.0) -> str:
    """Call Google Gemini and return the raw text reply.

    Retries rate-limit, overload and timeout errors with exponential backoff
    (2s, 4s, 8s). Raises after the last attempt, or immediately on other errors
    such as a bad key (401/403) or unknown model (404).
    """
    key = os.environ["GEMINI_API_KEY"]
    model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "x-goog-api-key": key},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.load(resp)
            return body["candidates"][0]["content"]["parts"][0]["text"]
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRYABLE_STATUS or attempt == retries:
                raise
        except (TimeoutError, urllib.error.URLError):
            if attempt == retries:
                raise
        time.sleep(backoff * 2**attempt)
    raise RuntimeError("unreachable")


# ------------------------------------------------------------- 4. validation
def validate(raw: str) -> dict | None:
    """Parse and check an LLM reply. Returns a clean ticket, or None if it is unusable."""
    try:
        data = json.loads(raw.strip().removeprefix("```json").removesuffix("```"))
    except (json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(data, dict) or any(k not in data for k in TICKET_FIELDS):
        return None
    if data["priority"] not in PRIORITIES or not isinstance(data["next_steps"], list):
        return None
    ticket = {k: data[k] for k in TICKET_FIELDS}
    ticket["next_steps"] = [str(s) for s in ticket["next_steps"]][:4]
    # never echo secrets back, even if the model repeats something
    for k in ("title", "summary", "likely_cause", "affected_service"):
        ticket[k] = redact(str(ticket[k]))[0]
    return ticket


def analyze(metrics: dict, logs: list[str], use_llm: bool | None = None) -> dict:
    """Produce an incident ticket. Uses the LLM when a key is set, else rules."""
    if use_llm is None:
        use_llm = bool(os.getenv("GEMINI_API_KEY"))
    prompt, redactions = build_prompt(metrics, logs)
    baseline = rule_based(metrics, logs)

    if use_llm:
        try:
            ticket = validate(ask_llm(prompt))
        except Exception as exc:  # network error, quota, bad key ...
            return {"ticket": baseline, "source": "rules", "fallback_reason": type(exc).__name__, "redactions": redactions}
        if ticket:
            return {"ticket": ticket, "source": "llm", "redactions": redactions}
        return {"ticket": baseline, "source": "rules", "fallback_reason": "invalid_llm_output", "redactions": redactions}

    return {"ticket": baseline, "source": "rules", "redactions": redactions}
