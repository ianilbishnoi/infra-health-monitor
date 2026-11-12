# Infra Health Monitor

A small FastAPI service that reports host health (CPU, memory, disk, load), raises threshold alerts,
and turns metrics + logs into an ITSM-style incident ticket with an **AIOps assistant** (LLM with a
rule-based fallback). It is containerized with Docker, provisioned on AWS with **Terraform**, runs on
**Kubernetes**, and is tested and shipped by **GitHub Actions**.

**Stack:** Python, FastAPI, psutil, Docker, Terraform, AWS EC2, Kubernetes (kind), GitHub Actions,
GitHub Container Registry, Google Gemini API

```
logs + metrics ──► redact secrets/IPs/emails ──► rule-based triage (always)
                                         └────► LLM triage (if GEMINI_API_KEY) ──► validate JSON ──► ticket
                                                                    invalid / error ──► fall back to rules
```

## Endpoints
| Path | Purpose |
|---|---|
| `GET /health` | Liveness/readiness probe (Docker HEALTHCHECK and Kubernetes probes) |
| `GET /metrics` | CPU, memory, disk, load average, and `alerts` when a threshold is exceeded |
| `GET /info` | Hostname, OS, Python version, CPU count |
| `POST /analyze` | AIOps: returns an incident ticket (title, priority P1-P4, affected service, summary, likely cause, next steps) |
| `GET /docs` | Auto-generated API docs |

Alert thresholds are set with env vars: `CPU_ALERT_PERCENT` (85), `MEM_ALERT_PERCENT` (85), `DISK_ALERT_PERCENT` (90).

### `/analyze` example
```bash
curl -X POST localhost:8000/analyze -H 'Content-Type: application/json' -d '{
  "logs": ["nginx[812]: write() failed (28: No space left on device)"],
  "metrics": {"disk_percent": 97, "memory_percent": 40, "alerts": ["disk usage 97.0% exceeds 90%"]}
}'
```
```json
{"ticket": {"title": "Disk is full or nearly full", "priority": "P1", "affected_service": "nginx", "...": "..."},
 "source": "rules", "redactions": 0}
```
If `metrics` is omitted, the live `/metrics` values are used.

### How the AIOps assistant works (`app/aiops.py`)
1. **Redaction (responsible AI):** IPs, emails, AWS keys, bearer tokens and `password=`/`token=` values are masked
   before anything is sent to the LLM, and again on the way back.
2. **Rule-based triage:** deterministic priority and likely cause from alerts and log patterns. It always runs,
   so the service works with no API key and never depends on the LLM being up.
3. **LLM triage (optional):** if `GEMINI_API_KEY` is set, the redacted evidence is sent to Google Gemini with a
   prompt that asks for strict JSON and forbids inventing facts (`GEMINI_MODEL`, default `gemini-3.8-flash`).
4. **Validation and fallback:** the reply must parse as JSON, contain every ticket field and use P1-P4;
   otherwise, or on any API error, the rule-based ticket is returned with a `fallback_reason`.

### Evaluation
`tests/eval_cases.json` holds labelled incidents (disk full, OOM kill, DB unreachable, auth error, healthy) with
the expected priority and keywords.
- `pytest` scores the rule-based triage on every case (runs in CI).
- `python scripts/eval_llm.py` scores the LLM on the same cases and prints both pass rates.
  It runs in CI only when the repo has a `GEMINI_API_KEY` secret.

## Run locally
```bash
pip install -r requirements-dev.txt
pytest -q
uvicorn app.main:app --reload          # http://localhost:8000/docs
export GEMINI_API_KEY=...              # optional, enables LLM triage (never commit it)

# or with Docker
docker compose up --build
```

## CI/CD (GitHub Actions)
`.github/workflows/ci.yml` runs on every push and PR:

| Job | What it checks |
|---|---|
| `test` | pytest (API, alerts, redaction, LLM fallback, eval cases); LLM eval if the secret exists |
| `terraform` | `terraform fmt -check`, `init`, `validate` |
| `kubernetes` | builds the image, deploys `k8s/monitor.yaml` to a kind cluster, waits for rollout, smoke-tests `/health` and `/analyze` through the Service |
| `docker` | on `main`, after all checks pass: pushes the image to `ghcr.io/<user>/infra-health-monitor` |

## Deploy to AWS with Terraform (`infra/terraform`)
Creates: one free-tier `t3.micro` (Amazon Linux 2023, 8 GiB gp3, IMDSv2), a security group with **HTTP only**
(no SSH), and optionally a zero-spend budget alert. The instance bootstraps itself from user data: installs
Docker and git, clones this repo, builds the image and runs it with a restart policy.

```bash
cd infra/terraform
aws configure                       # or any AWS credentials
terraform init
terraform plan  -var alert_email=you@example.com
terraform apply -var alert_email=you@example.com
# wait ~3 min, then open the "url" output + /docs

terraform destroy                   # removes everything; do this when finished
```
Optional: `-var gemini_api_key=...` enables LLM triage on the server. Note that the key is then stored in the
instance user data and in your local Terraform state, so use a dedicated key and keep `terraform.tfstate`
private (it is git-ignored).

A manual, console-based deployment of the same setup is recorded in [DEPLOYMENT.md](DEPLOYMENT.md).

## Run on Kubernetes (`k8s/monitor.yaml`)
Deployment (2 replicas, CPU/memory requests and limits, readiness and liveness probes on `/health`) plus a
ClusterIP Service.
```bash
kind create cluster
docker build -t infra-health-monitor:ci .
kind load docker-image infra-health-monitor:ci
kubectl apply -f k8s/monitor.yaml
kubectl rollout status deployment/infra-monitor
kubectl port-forward service/infra-monitor 8080:80     # http://localhost:8080/docs
# optional LLM key:
kubectl create secret generic gemini --from-literal=api-key=...
```

## Troubleshooting
- Browser times out but the instance is running: the bootstrap is still building (check EC2 → Actions →
  Monitor and troubleshoot → Get system log, look for `USERDATA_DONE`), or the security group lacks port 80.
- `/analyze` returns `"source": "rules"` with a `fallback_reason`: the LLM key is missing or invalid, the quota
  was hit, or the model returned malformed JSON. The ticket is still valid.
- Kubernetes pod not ready: `kubectl describe pod -l app=infra-monitor` and `kubectl logs -l app=infra-monitor`.
