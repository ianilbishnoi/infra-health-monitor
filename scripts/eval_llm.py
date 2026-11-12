"""Evaluate the LLM triage against the labelled cases in tests/eval_cases.json.

Usage:
    export GEMINI_API_KEY=...        # never commit this
    python scripts/eval_llm.py       # prints a per-case table and the pass rate

A case passes when the LLM returns a valid ticket whose priority is in the expected
set and whose text mentions at least one expected keyword. The rule-based baseline is
scored on the same cases so the two can be compared.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import aiops  # noqa: E402

CASES = json.loads((Path(__file__).resolve().parents[1] / "tests" / "eval_cases.json").read_text())


def score(ticket: dict | None, case: dict) -> bool:
    if not ticket:
        return False
    text = json.dumps(ticket).lower()
    return ticket["priority"] in case["expect_priority"] and any(k in text for k in case["expect_keywords"])


def main() -> int:
    if not os.getenv("GEMINI_API_KEY"):
        print("GEMINI_API_KEY is not set; skipping LLM evaluation.")
        return 0
    llm_pass = rules_pass = 0
    print(f"{'case':<20}{'rules':<8}{'llm':<8}llm priority / title")
    for case in CASES:
        rules_ok = score(aiops.rule_based(case["metrics"], case["logs"]), case)
        prompt, _ = aiops.build_prompt(case["metrics"], case["logs"])
        try:
            ticket = aiops.validate(aiops.ask_llm(prompt))
        except Exception as exc:  # report, don't crash the whole run
            ticket, note = None, f"error: {type(exc).__name__}"
        else:
            note = f"{ticket['priority']} / {ticket['title']}" if ticket else "invalid output"
        llm_ok = score(ticket, case)
        rules_pass += rules_ok
        llm_pass += llm_ok
        print(f"{case['name']:<20}{'PASS' if rules_ok else 'FAIL':<8}{'PASS' if llm_ok else 'FAIL':<8}{note}")
    n = len(CASES)
    print(f"\nrules: {rules_pass}/{n}   llm: {llm_pass}/{n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
