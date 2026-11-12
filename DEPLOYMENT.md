# Deployment: AWS EC2 (ap-south-1)

Deployed on 12 Nov 2026.

## What was deployed

| Item | Value |
|---|---|
| Cloud / region | AWS EC2, Asia Pacific (Mumbai) `ap-south-1` |
| Instance | `t3.micro` (free-tier eligible), 2 vCPU, 1 GiB RAM |
| OS | Amazon Linux 2023 (kernel 6.18) |
| Storage | 8 GiB gp3 root volume (deleted on termination) |
| Security group | Inbound TCP 80 (HTTP) only. No SSH port open, no key pair |
| Runtime | Docker container `monitor`, host port 80 → container port 8000 |
| Cost guard | AWS Budgets "Zero-Spend" alert (emails when spend > $0.01) |

## How it was deployed

The instance was launched from the EC2 console. Its **user data** script bootstraps the server on first boot:

```bash
#!/bin/bash
exec > >(tee /var/log/user-data.log | logger -t user-data -s 2>/dev/console) 2>&1
dnf install -y docker git
systemctl enable --now docker
git clone https://github.com/ianilbishnoi/infra-health-monitor.git /opt/app
cd /opt/app && docker build -t infra-health-monitor . \
  && docker run -d --name monitor --restart unless-stopped -p 80:8000 infra-health-monitor
sleep 10; curl -s localhost/health
echo USERDATA_DONE
```

It installs Docker and git, clones this repo, builds the image from the `Dockerfile`, and runs the container with a restart policy. The container also has a Docker `HEALTHCHECK` that polls `/health`.

## Verification (live responses from the public IP)

`GET /health`
```json
{"status":"ok","uptime_seconds":28.7}
```

`GET /metrics`
```json
{"cpu_percent":0.0,"memory_percent":48.6,"memory_used_mb":288.0,"memory_total_mb":913.1,
 "disk_percent":26.7,"disk_free_gb":5.81,
 "load_avg":{"1m":0.796,"5m":0.384,"15m":0.147},"alerts":[]}
```

`GET /info`
```json
{"hostname":"60c21a7f6dc8","os":"Linux-6.18.56-126.188.amzn2023.x86_64-x86_64-with-glibc2.41",
 "python":"3.12.16","cpu_count":2}
```

Swagger UI at `/docs` loaded with all three endpoints.

## Design notes

- **No SSH exposed:** debugging uses the EC2 *Get system log* console output, since the user data writes its log to the serial console.
- **Free-tier sizing:** the instance type and volume stay inside free-tier eligibility, and the zero-spend budget catches any accidental charge.
- **Immutable-ish rebuild:** to redeploy, terminate and relaunch with the same user data. The server always builds from `main`.

## Cleanup

Terminate the instance from the EC2 console (Instance state → Terminate). The 8 GiB volume is deleted automatically. No Elastic IP was allocated.
