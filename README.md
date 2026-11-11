# Infra Health Monitor

A small FastAPI service that reports host health (CPU, memory, disk, load) with threshold-based alerts.
Containerized with Docker, tested and built by GitHub Actions CI, and deployed to an AWS EC2 instance.

**Stack:** Python, FastAPI, psutil, Docker, GitHub Actions, GitHub Container Registry, AWS EC2 (Linux)

## Endpoints
| Path | Purpose |
|---|---|
| `GET /health` | Liveness probe (also used by the Docker HEALTHCHECK) |
| `GET /metrics` | CPU, memory, disk, load average, and `alerts` when a threshold is exceeded |
| `GET /info` | Hostname, OS, Python version, CPU count |
| `GET /docs` | Auto-generated API docs |

Alert thresholds are set with env vars: `CPU_ALERT_PERCENT` (85), `MEM_ALERT_PERCENT` (85), `DISK_ALERT_PERCENT` (90).

## Run locally
```bash
pip install -r requirements-dev.txt
pytest -q
uvicorn app.main:app --reload          # http://localhost:8000/docs

# or with Docker
docker compose up --build
```

## CI/CD (GitHub Actions)
`.github/workflows/ci.yml` runs pytest on every push and PR. On `main`, it also builds the Docker image
and pushes it to `ghcr.io/<your-github-user>/infra-health-monitor` (tags: `latest` and the commit SHA).

## Deploy to AWS EC2

### 1. Account safety first
- Create a Billing budget alert before launching anything (AWS Budgets, e.g. $5).
- Pick an instance type labelled "Free tier eligible" in the launch wizard (t3.micro or t2.micro, depending on region).

### 2. Launch the instance
- AMI: Amazon Linux 2023. Storage: default 8 GB.
- Key pair: create one and keep the `.pem` file.
- Security group inbound rules:
  - SSH (22): source **My IP** only
  - HTTP (80): source Anywhere (0.0.0.0/0)

### 3. Install Docker on the instance
```bash
ssh -i your-key.pem ec2-user@<public-ip>
sudo dnf install -y docker
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user
exit        # log in again so the group change applies
```

### 4. Run the container
Make the GHCR package public (GitHub profile -> Packages -> infra-health-monitor -> Package settings -> Change visibility), or run `docker login ghcr.io` with a personal access token that has `read:packages`.
```bash
ssh -i your-key.pem ec2-user@<public-ip>
docker run -d --name monitor --restart unless-stopped -p 80:8000 \
  ghcr.io/<your-github-user>/infra-health-monitor:latest
curl localhost/health
```
Open `http://<public-ip>/docs` in a browser.

### 5. Update after a code change
```bash
docker pull ghcr.io/<your-github-user>/infra-health-monitor:latest
docker rm -f monitor
docker run -d --name monitor --restart unless-stopped -p 80:8000 \
  ghcr.io/<your-github-user>/infra-health-monitor:latest
```

### 6. Tear down when finished (avoid charges)
Terminate the instance in the EC2 console. Release any Elastic IP you allocated.

## Troubleshooting
- `curl localhost/health` works but the browser times out: the security group is missing the HTTP (80) rule.
- `permission denied` on docker: log out and back in after `usermod`.
- `pull access denied`: the GHCR package is private; make it public or `docker login ghcr.io`.
- Container exits: `docker logs monitor`.
