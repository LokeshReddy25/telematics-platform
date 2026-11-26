# Results summary (branch `feature/risk-api`)

Date: 2026-11-26. Machine: i5-12500H (12C/16T), 15.71 GiB RAM, Windows 11,
Docker Desktop (WSL2 VM: 16 CPUs, 7.61 GiB). Full detail, exact commands and
raw logs: [results/results.md](results/results.md). The claim-by-claim check
is in [results/resume_claims_check.md](results/resume_claims_check.md).

## Verified numbers (median of 3 runs, with min–max)

| Metric | Value |
|---|---|
| Kafka produce, transport only | 40,042.7 events/s (36,405.1–43,537.5) |
| Kafka consume | 44,898.9 events/s (42,748.2–45,851.1) |
| `kafka_producer.py --speed 0`, incl. generation | 40,960.6 events/s (39,576.1–41,530.0) |
| Event produced → score in InfluxDB, median | 16.155 s (15.483–16.639) |
| Event produced → score in InfluxDB, p95 | 29.454 s (29.432–29.460) |
| Spark micro-batch time (29 batches with input) | median 938 ms (408–1,823) |
| Per demo run | 5 drivers, 15 trips, 461 events (454–492), 37 scored records (34–39) |
| Model, held-out (random 80/20 split of 10,000 synthetic rows, seed 42) | MAE 2.646, R² 0.975 (identical in all 3 runs) |
| `tests/` | 17/17 passed, 33% coverage (same in 3 runs after the `api/`/`deploy/` removal) |
| `risk_api/tests` (the only API in the repo) | 46/46 passed (41 unit + 5 integration), 100% coverage; unit-only 99% (same in 3 runs after the removal) |
| Infra (re-counted after the removal) | 2 compose files; root stack has 10 services. 4 Dockerfiles. 6 k8s manifests (11 objects). 7 Terraform resource blocks (`devops/terraform` only) |

Latency is set by the 30 s Spark trigger, not by Kafka or the model.

## Resume claims

| Claim | Verdict | Main caveat |
|---|---|---|
| Kafka + Spark Structured Streaming pipeline, near-real-time scores | **SUPPORTED** | "Near-real-time" = 30 s micro-batches (median 16.155 s). Demo depended on a `trip_generator.py` fix, now committed (below) |
| Gradient Boosting scoring, InfluxDB, risk levels | **SUPPORTED** | Model trained on synthetic, formula-generated labels. Simulator features don't match training distributions. Categories are per trip window, not per driver |
| Monitoring + deployment with Grafana, Docker, K8s, Terraform, GitHub Actions, CI/CD | **PARTIAL** | Grafana ✔, Docker ✔, K8s ✔ (local cluster only). Terraform: the MSK stack (`devops/terraform`) validated, **never applied**. *Update:* a separate single-EC2 stack (`devops/terraform-ec2`) was applied once on 2026-11-26, verified, and destroyed the same day (see `results/results.md#aws-deployment`). CI ✔, but the **deploy job is an `echo` placeholder (no CD)**. Prometheus is claimed in the old README but **NOT FOUND** |

## What was added on this branch

- `benchmarks/`: measurement scripts (Kafka throughput, e2e latency, Spark
  micro-batch listener, clock offset, feature skew, trip coverage, summarizer)
- `results/`: results.md, resume_claims_check.md, Grafana screenshot, all raw logs
- `risk_api/`: FastAPI + Pydantic read-only API over InfluxDB.
  `GET /health`, `/drivers/{id}/risk`, `/drivers/top-risk?limit=N`, `/trips/{id}`.
  Input validation (422), 404/503, one error format, env-var config with no
  token default, OpenAPI at `/docs`
- `risk_api/tests`: 41 unit tests (InfluxDB mocked) + 5 integration tests (real InfluxDB, throwaway bucket)
- `risk_api/Dockerfile` (non-root, healthcheck), `devops/k8s/risk-api-deployment.yaml`
- `.github/workflows/risk-api-ci.yml`: unit + coverage, integration against an InfluxDB service container, image build
- `docker-compose.yml`: the whole stack from one `docker compose up -d --build`
  (adds model-trainer, kafka-init, spark, simulator, risk-api; includes the Kafka file)
- Removed `deploy/` (ECS Fargate Terraform for the Vehicle API that was deleted on `main`)
  and its `.gitignore` rules; merged `origin/main` (deletion of `api/` and `api-ci.yml`)
- README: CI badges, working Quickstart, Risk API in the diagram and tech
  stack, Results section, "nothing deployed to AWS" statement. Prometheus
  claim removed. All references to the deleted Vehicle API (`api/`) removed;
  README states that `risk_api/` is the only API in the repo.

Pipeline code changed only in the first commit: your two fixes to
`simulator/trip_generator.py` and `spark/stream_processor.py`. `ml/` and
`storage/` are unchanged.

## Still to do by hand

1. ~~Decide on the two uncommitted pipeline changes~~. **Done:** committed at
   your request as the first commit on this branch ("Fix sparse risk
   scoring…"). Before the fix, 7 of 27 trips got a score; after it, 15 of 15
   in each run. `tests/` still passes 17/17 on the committed code (3 runs,
   `results/raw/pytest_pipeline_after_fix_commit_run*.log`).
2. **Open the PR and check CI on GitHub.** The branch is pushed. I couldn't verify any
   workflow on GitHub (`gh` isn't installed). The badges only turn green
   once runs exist, and they only render publicly if the repo is public.
   The new `risk-api-ci.yml` integration job is untested on GitHub Actions
   itself; it passed only against my local InfluxDB.
3. **Capture this Grafana screenshot** for the README/portfolio. Run
   `docker compose up -d --build`, wait about 5 minutes, then open
   http://localhost:3000/d/telematics-driver-risk/?from=now-15m&to=now in a
   window at least 1,900 px wide, so the "Top 5 Riskiest Drivers" score
   column is visible. The **"Live Risk Score per Driver"** time-series panel
   is the one to show. `results/grafana_dashboard.png` is a headless capture
   from this session that you can use meanwhile.
4. **Fix or explain two dashboard panels.** "Risk Category Distribution"
   renders as one `_value` slice, and the "Top 5" table shows `_start`/`_stop`
   columns. These are Flux/transform issues in
   `dashboard/grafana_config/dashboard.json`. I didn't touch them.
5. **Update your resume wording**. Suggestions are at the bottom of
   `results/resume_claims_check.md`. In particular: say "CI" not "CI/CD",
   "Terraform (MSK design validated, not applied; single-EC2 Docker Compose deployment run once and destroyed)", "micro-batch (30 s)", and "synthetic
   training data".
6. ~~RUNBOOK.md outdated~~. **Done:** rewritten for the single
   `docker compose up` flow (and now committed), including how to remove the
   old separately started Kafka/Spark containers once.
7. Optional: `kubectl apply -f devops/k8s/risk-api-deployment.yaml` on the
   local cluster (I only ran a server-side dry run) after
   `docker build -t telematics/risk-api:latest risk_api`.
8. Your old exited container `telematics-spark` is still present. I left it
   alone. `docker rm telematics-spark` removes it if you no longer need it.
9. Review and merge `feature/risk-api` yourself. Nothing has been merged to `main`.
