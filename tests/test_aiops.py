import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import aiops
from app.main import app

client = TestClient(app)
CASES = json.loads((Path(__file__).parent / "eval_cases.json").read_text())


# ---------------------------------------------------------------- redaction
def test_redacts_ip_email_and_secrets():
    raw = ("login from 10.1.2.3 by ops@example.com password=hunter2 "
           "key AKIAABCDEFGHIJKLMNOP Authorization: Bearer abc.def.ghi")
    clean, n = aiops.redact(raw)
    for secret in ("10.1.2.3", "ops@example.com", "hunter2", "AKIAABCDEFGHIJKLMNOP", "abc.def.ghi"):
        assert secret not in clean
    assert n == 5


def test_prompt_never_contains_raw_secrets():
    prompt, n = aiops.build_prompt({"alerts": []}, ["db 10.0.3.15 refused, token=xyz123"])
    assert "10.0.3.15" not in prompt and "xyz123" not in prompt
    assert n == 2


# ------------------------------------------------- evaluation: rule baseline
@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_rule_based_eval_cases(case):
    ticket = aiops.rule_based(case["metrics"], case["logs"])
    assert ticket["priority"] in case["expect_priority"]
    text = json.dumps(ticket).lower()
    assert any(k in text for k in case["expect_keywords"]), text


# ------------------------------------------------------ LLM output handling
GOOD = json.dumps({
    "title": "Disk full on web-01", "priority": "P1", "affected_service": "nginx",
    "summary": "Disk at 97%.", "likely_cause": "Logs filled /var", "next_steps": ["Rotate logs"],
})


def test_validate_accepts_good_json():
    assert aiops.validate(GOOD)["priority"] == "P1"


@pytest.mark.parametrize("bad", ["not json", "[]", json.dumps({"title": "x"}),
                                 GOOD.replace('"P1"', '"URGENT"')])
def test_validate_rejects_bad_output(bad):
    assert aiops.validate(bad) is None


def test_validate_redacts_model_echo():
    leaky = GOOD.replace("Disk at 97%.", "Host 10.9.9.9 disk at 97%.")
    assert "10.9.9.9" not in aiops.validate(leaky)["summary"]


def test_analyze_uses_llm_when_valid(monkeypatch):
    monkeypatch.setattr(aiops, "ask_llm", lambda prompt: GOOD)
    out = aiops.analyze(CASES[0]["metrics"], CASES[0]["logs"], use_llm=True)
    assert out["source"] == "llm" and out["ticket"]["title"] == "Disk full on web-01"


def test_analyze_falls_back_on_invalid_llm_output(monkeypatch):
    monkeypatch.setattr(aiops, "ask_llm", lambda prompt: "sorry, I can't help")
    out = aiops.analyze(CASES[0]["metrics"], CASES[0]["logs"], use_llm=True)
    assert out["source"] == "rules" and out["fallback_reason"] == "invalid_llm_output"


def test_analyze_falls_back_on_api_error(monkeypatch):
    def boom(prompt):
        raise TimeoutError("api down")
    monkeypatch.setattr(aiops, "ask_llm", boom)
    out = aiops.analyze(CASES[0]["metrics"], CASES[0]["logs"], use_llm=True)
    assert out["source"] == "rules" and out["fallback_reason"] == "TimeoutError"


# ---------------------------------------------------------------- endpoint
def test_analyze_endpoint_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    case = CASES[0]
    r = client.post("/analyze", json={"metrics": case["metrics"], "logs": case["logs"]})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "rules"
    assert set(aiops.TICKET_FIELDS) <= set(body["ticket"])


def test_analyze_endpoint_uses_live_metrics(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    r = client.post("/analyze", json={"logs": []})
    assert r.status_code == 200 and r.json()["ticket"]["priority"] in aiops.PRIORITIES


# ------------------------------------------------------------- retry logic
class _FakeResp:
    def __init__(self, text):
        self._body = json.dumps({"candidates": [{"content": {"parts": [{"text": text}]}}]}).encode()

    def read(self, *a):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(code):
    import io
    import urllib.error
    return urllib.error.HTTPError("u", code, "err", {}, io.BytesIO(b"{}"))


def test_ask_llm_retries_overload_then_succeeds(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout):
        calls.append(1)
        if len(calls) < 3:
            raise _http_error(503)
        return _FakeResp(GOOD)

    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(aiops.urllib.request, "urlopen", fake_urlopen)
    assert aiops.ask_llm("p", retries=3, backoff=0) == GOOD
    assert len(calls) == 3


def test_ask_llm_does_not_retry_auth_error(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout):
        calls.append(1)
        raise _http_error(401)

    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(aiops.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(aiops.urllib.error.HTTPError):
        aiops.ask_llm("p", retries=3, backoff=0)
    assert len(calls) == 1
