from fastapi.testclient import TestClient

from app.main import app, build_alerts

client = TestClient(app)


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_metrics_shape():
    r = client.get("/metrics")
    assert r.status_code == 200
    body = r.json()
    for key in ("cpu_percent", "memory_percent", "disk_percent", "load_avg", "alerts"):
        assert key in body
    assert 0 <= body["memory_percent"] <= 100


def test_info_has_hostname():
    assert client.get("/info").json()["hostname"]


def test_alerts_trigger_above_threshold():
    alerts = build_alerts(cpu=95, mem=10, disk=10)
    assert len(alerts) == 1 and alerts[0].startswith("cpu")


def test_alerts_empty_when_healthy():
    assert build_alerts(cpu=10, mem=10, disk=10) == []


def test_threshold_env_override(monkeypatch):
    monkeypatch.setenv("CPU_ALERT_PERCENT", "5")
    assert build_alerts(cpu=10, mem=1, disk=1)
