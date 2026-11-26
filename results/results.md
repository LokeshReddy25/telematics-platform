# Measured results: Connected Vehicle Insurance Telematics Platform

**Date:** 2026-11-26 (all runs between 11:13 and 11:38 IST)
**Code measured:** `main` @ `1b5d95c`, **plus two working-tree changes that
were uncommitted at measurement time** (since committed as the first commit
on `feature/risk-api`, with `tests/` re-run there: 17/17 passed in 3 runs,
`raw/pytest_pipeline_after_fix_commit_run*.log`): `simulator/trip_generator.py` (trips start within 0–20 s of
"now" instead of up to 24 h ago) and `spark/stream_processor.py` (adds
`spark.jars.packages`). Both are described in RUNBOOK.md. The pipeline code
was not modified for these measurements. Every measurement script is in
`benchmarks/`.

**Repo changes after measurement:** `api/` (the separate Vehicle API) and
`api-ci.yml` were deleted on `main` and merged into this branch. `deploy/`
(the ECS Fargate Terraform for that API) was then removed. Test counts,
coverage and infrastructure counts below were **re-run on the resulting
code** (logs marked `post_api_removal`). The performance numbers (Kafka,
latency, Spark, model) never involved `api/` or `deploy/`, so they are
unaffected. The old `api/tests` logs are kept in `raw/removed_api/`.

## Machine

Source: `results/raw/machine_specs.log`

| | |
|---|---|
| CPU | 12th Gen Intel Core i5-12500H, 12 cores / 16 logical processors |
| RAM | 16,869,548,032 bytes (15.71 GiB) |
| GPU | NVIDIA GeForce RTX 3050 Ti Laptop GPU + Intel Iris Xe (not used by anything in this project) |
| OS | Windows 11 Home 10.0.26300 |
| Docker | Docker Desktop 29.6.1, WSL2 VM with 16 CPUs and 8,172,236,800 bytes (7.61 GiB) |
| Python / Java | 3.11.9 / Temurin 17.0.21 (host). Spark itself ran in the `apache/spark:3.5.8` image (Java 11) |

Other load during the runs: the project's Kubernetes deployment (4 pods in
namespace `telematics`, its own Kafka/InfluxDB/Spark/simulator) was also
running on the same Docker Desktop VM (`results/raw/k8s_local_state.log`).
It does not share topics or buckets with the measured stack, but it did
compete for CPU.

## How the pipeline was run

Kafka/Zookeeper/Kafka UI (`kafka/docker-compose.yml`) and
InfluxDB/Grafana (`docker-compose.yml`) were started with Docker Compose. The
Spark job ran in a container built from `devops/Dockerfile.spark` (tag
`telematics-spark:bench`), started through `benchmarks/spark_with_progress.py`.
That wrapper calls `stream_processor.py`'s own functions with the same
trigger, output mode and checkpoint setting, and adds a listener that logs
micro-batch timings. Exact commands: `results/raw/stack_startup.log`,
`results/raw/spark_image_build.log`, `results/raw/spark_container_cmd.log`.

Startup issue: on the first `up`, Kafka exited with `NodeExistsException`
because a stale `/brokers/ids/1` node was left in Zookeeper by the previous,
unclean Docker shutdown. The second `up` succeeded with no data deleted
(`results/raw/kafka_start_failure.log`).

## Results

Repeated measurements are shown as **median (min–max)** over 3 runs, computed
by `benchmarks/summarize.py` → `results/raw/summary.json`.

| Metric | Value | Exact command | Raw log |
|---|---|---|---|
| Kafka produce throughput (transport only, events pre-serialised, linger.ms=50, 3 partitions, ~52k events/run) | **40,042.7 events/s** (36,405.1–43,537.5) | `python benchmarks/kafka_throughput.py --bootstrap localhost:29092 --drivers 200 --trips 8` | `raw/kafka_throughput_run{1,2,3}.log` |
| Kafka consume throughput (same events, single consumer, `assign()` from offset 0) | **44,898.9 events/s** (42,748.2–45,851.1) | same as above | same |
| Project producer at full speed, including event generation (`kafka_producer.py`; "Connected" → "Done" log timestamps) | **40,960.6 events/s** (39,576.1–41,530.0) | `cd simulator && python kafka_producer.py --bootstrap localhost:29092 --drivers 200 --trips 8 --speed 0` | `raw/kafka_producer_speed0_run{1,2,3}.log` |
| End-to-end latency, event acked by Kafka → risk score written to InfluxDB, **median** (per event, ~450–490 events/run) | **16.155 s** (15.483–16.639) | `bash benchmarks/run_e2e.sh` (runs `clock_offset.py` then `e2e_latency.py --drivers 5 --trips 3 --speed 0.3 --wait 240 --clock-offset-ms <measured> --progress-file results/raw/spark_progress.jsonl`) | `raw/e2e_latency_run{1,2,3}.log` |
| End-to-end latency, **95th percentile** (nearest-rank) | **29.454 s** (29.432–29.460) | same | same |
| End-to-end latency, min / max event over all 3 runs | 0.425 s / 30.962 s | same | same |
| Trip complete (trip's last event → first score that includes it), median | 17.454 s (16.933–21.031) | same | same |
| Drivers per e2e run | 5 (5–5) | same | same |
| Trips per e2e run | 15 (15–15); all 15 trips received a score in every run | same | same |
| Events per e2e run (sent = acked) | 461 (454–492) | same | same |
| Scored records written to InfluxDB per run | 37 (34–39) | same | same |
| Events with no score written after them | 0 in every run | same | same |
| Spark micro-batch time (`triggerExecution`), batches that had input | **median 938 ms** (408–1,823 ms) over 29 batches | `spark_with_progress.py` listener in the container above | `raw/spark_progress.jsonl` |
| of which `addBatch` (aggregation + scoring + InfluxDB writes) | median 860 ms (326–1,561 ms) | same | same |
| Input rows per micro-batch (batches with input) | median 100 (21–200) | same | same |
| Spark trigger interval (configuration, not a measurement) | 30 s | `spark/stream_processor.py`: `trigger(processingTime="30 seconds")` | n/a |
| Model MAE, held-out test set | **2.646** (2.646–2.646) | `cd ml && python train_model.py` | `raw/train_model_run{1,2,3}.log` |
| Model R², held-out test set | **0.975** (0.975–0.975) | same | same |
| Train/test split | 10,000 synthetic rows, `train_test_split(test_size=0.2, random_state=42)` → 8,000 train / 2,000 test, random (not stratified, not by driver) | same (printed `Train samples: 8000 \| Test samples: 2000`) | same |
| Pipeline tests (`tests/`) | **17 passed / 17** (17–17), 100% pass rate. Same result in 3 runs on the original code, 3 after committing the fix, and 3 after removing `api/`/`deploy/` | `python -m pytest tests/ -v --cov=ml --cov=simulator --cov=storage --cov=spark --cov-report=term` | `raw/pytest_pipeline_run{1,2,3}.log`, `raw/pytest_pipeline_after_fix_commit_run{1,2,3}.log`, `raw/pytest_pipeline_post_api_removal_run{1,2,3}.log` |
| Pipeline test coverage (statements in ml/, simulator/, storage/, spark/) | **33%** (33–33) in all 9 runs; risk_scorer 91%, trip_generator 91%, kafka_producer / stream_processor / influxdb_writer / train_model 0% | same | same |
| Risk API tests (`risk_api/`, the only API in the repo), unit + integration against the compose InfluxDB | **46 passed / 46** (46–46), **100%** statement coverage of `risk_api/app` (100–100). Same before and after the `api/`/`deploy/` removal | `cd risk_api && RISK_API_IT_INFLUXDB_URL=http://localhost:8086 RISK_API_IT_INFLUXDB_TOKEN=<dev token> python -m pytest -v --cov=app --cov-report=term-missing` | `raw/pytest_risk_api_run{1,2,3}.log`, `raw/pytest_risk_api_post_api_removal_run{1,2,3}.log` |
| Risk API unit tests only (InfluxDB mocked, what CI's `unit` job runs) | 41 passed / 41, 99% coverage (1 line uncovered: closing an app-owned client on shutdown) | `python -m pytest -m "not integration" -v --cov=app --cov-report=term-missing` | `raw/pytest_risk_api_unit_only.log` |
| Risk API container against live pipeline data | `/health` 200, top-risk 200, driver 200, trip 200, `limit=0` 422, bad id 422, unknown driver 404, startup refused without token, Docker health `healthy` | see log | `raw/risk_api_container_check.log` |
| Risk API k8s manifest | `kubectl apply --dry-run=server` accepted Deployment + Service (nothing created) | `kubectl apply --dry-run=server -f devops/k8s/risk-api-deployment.yaml` | `raw/k8s_risk_api_dry_run.log` |
| One-command stack from a fresh clone (no model file, no uncommitted changes) | `docker compose up -d --build` exit 0 in 165 s (incl. image builds); 10 services; model trained in-container (MAE 2.646, R² 0.975); first score visible via API 60 s after `up` returned | `git clone --branch feature/risk-api …; docker compose up -d --build` | `raw/compose_fresh_clone_up.log`, `raw/compose_fresh_clone_checks.log` |
| Trips scored, **committed** `trip_generator.py` (trips backdated up to 24 h), fresh clone, trips started after Spark's query (06:15:40–06:20:00 UTC) | **7 of 27** trips got a score and 20 got none. That matches RUNBOOK.md's explanation (late events behind the 2-minute watermark are dropped), but the cause itself was not separately measured. For comparison, the uncommitted fix (trips start near "now") scored 15 of 15 trips in each e2e run above | `python benchmarks/trip_coverage.py --since 2026-11-26T06:15:40Z --until 2026-11-26T06:20:00Z` | `raw/trip_coverage_committed_generator.log` |
| Lint (CI's commands), measured on `feature/risk-api` on 2026-11-26 (not the current state of `main`) | flake8 exit 0, black: 25 files unchanged at that time. `main` @ `02426bb` now has 26 files, and black reports them unchanged (checked later the same day, not saved as a log) | `flake8 . --max-line-length=120 --extend-exclude=ml/models` / `black --check --line-length=120 .` | `raw/lint_check_post_api_removal.log` |
| Docker Compose services | 3 compose files (re-counted 2026-11-26 on `main` @ `02426bb`). `docker-compose.yml` (root) defines 7 services itself (influxdb, grafana, model-trainer, kafka-init, spark, simulator, risk-api) and `include`s `kafka/docker-compose.yml`, which defines 3 (zookeeper, kafka, kafka-ui): **10 services** from one `docker compose up`. `docker-compose.aws.yml` is an override that defines no new services. It re-binds the published ports of 6 of them (zookeeper, kafka, kafka-ui, influxdb, grafana, risk-api) to `127.0.0.1`. Root + override still gives 10 services | `git ls-files "*docker-compose*.yml"`; `docker compose config --services \| wc -l` (10); `docker compose -f kafka/docker-compose.yml config --services` (3); `docker compose -f docker-compose.yml -f docker-compose.aws.yml config --services \| wc -l` (10) | command output in this session (not saved as a log); earlier count: `raw/infra_counts_after_api_removal.log` |
| Dockerfiles | 4: `devops/Dockerfile.{ml,simulator,spark}`, `risk_api/Dockerfile` | `git ls-files "*Dockerfile*" \| wc -l` | `raw/infra_counts_after_api_removal.log` |
| Kubernetes manifests | 6 files, 11 objects (5 Deployment, 3 Service, 1 ConfigMap, 1 Secret, 1 Namespace), including the new `risk-api-deployment.yaml` | `ls devops/k8s/*.yaml \| wc -l`; `grep -h "^kind:" devops/k8s/*.yaml \| sort \| uniq -c` | `raw/infra_counts_after_api_removal.log` |
| Kubernetes deployments running on the local Docker Desktop cluster | 4/4 Running (namespace age 125 days; the Risk API manifest was only dry-run, not deployed) | `kubectl get all,configmap,secret -n telematics` | `raw/k8s_local_state.log` |
| Terraform resource blocks | **13** in 2 stacks (re-counted 2026-11-26 on `main` @ `02426bb`). `devops/terraform` (MSK design): **7** (3 security groups, MSK, EC2, DB subnet group, RDS). `devops/terraform-ec2` (single-EC2 demo): **6** (security group, egress rule, IAM role, policy attachment, instance profile, EC2 instance) | `git ls-files "*.tf"` (6 files); `grep -c '^resource "' $(git ls-files "*.tf")` | command output in this session (not saved as a log); earlier count: `raw/infra_counts_after_api_removal.log` |
| Terraform validity | `devops/terraform`: `Success! The configuration is valid.` `devops/terraform-ec2`: `Success! The configuration is valid.` | `cd devops/terraform && terraform validate`; `cd devops/terraform-ec2 && terraform validate` | `raw/infra_counts_after_api_removal.log`; `aws/terraform_fmt_init_validate.log` |
| Terraform applied? | `devops/terraform` (MSK stack): **No, never applied.** No state file and no remote backend configured. `devops/terraform-ec2`: **applied once** on 2026-11-26 and destroyed the same day (see [AWS deployment](#aws-deployment)). Its local state file is gitignored and not in the repo | `find devops/terraform -name "*.tfstate*"` (no output); `grep -rn backend devops/terraform/*.tf` (no output); `git ls-files \| grep -c tfstate` (0) | `aws/terraform_apply.log`, `aws/terraform_destroy.log` |

### Notes on the latency numbers

- A score is written only when a micro-batch runs, and batches run every
  30 s. An event therefore waits up to 30 s for the next trigger plus about
  1 s of processing. The measured median (16.155 s) and p95 (29.454 s)
  follow from that design. The bottleneck is the trigger interval, not
  Kafka or the model.
- Latency for an event is measured to the first score for its trip written
  by a micro-batch that **started after** Kafka acked the event, since that
  is the first batch that could read it. Batch start times come from the
  Spark listener.
- Host and container clocks differ. The offset was measured before each run
  (+47.0, +39.4, +39.3 ms) and corrected for. Its uncertainty is ±54.8,
  ±52.2 and ±51.8 ms (half the shortest `docker exec` round trip).
- One trip produces **several score records**: 15 trips gave 34–39
  records, because `update` output mode re-scores a window every trigger
  while events keep arriving, and trips can cross a 60 s window boundary.
- Two earlier attempts were discarded and kept for transparency: v1 used an
  invalid metric that produced a negative minimum, and v2 accidentally ran
  two producers at once. See `results/raw/discarded_e2e_*/README.txt`.

### Model behaviour on simulator data

`python benchmarks/feature_skew.py --drivers 300 --trips 4 --seed 7` →
`raw/feature_skew.log` (1,200 whole trips, scored with the trained model):

| Profile | Trips | Median risk score | Categories | Sim. mean trip duration vs training mean |
|---|---|---|---|---|
| safe | 500 | 25.2 | Low 244, Medium 256 | 62.07 s vs 900 s |
| moderate | 456 | 72.99 | Medium 31, High 219, Critical 206 | 63.23 s vs 1,100 s |
| aggressive | 244 | 92.68 | High 3, Critical 241 | 62.43 s vs 1,300 s |

The training data is synthetic (labels come from a hand-written weighted
formula), and its distributions don't match what the simulator actually
produces: trips are much shorter, and harsh-event counts are higher (for
example, moderate harsh braking averages 10.63 vs a training mean of 4.0).
Night ratio was 0 because trips are stamped with the current time and the
runs happened in daytime.

## Screenshots

- `docs/grafana-dashboard.png` (2026-11-26, branch `fix/grafana-panels`): the
  dashboard after its four panels were fixed, last 15 minutes of live local
  data, 1920 px wide with the empty bottom area cropped. No panel shows
  `_start`/`_stop`/`_measurement` metadata. The pie counts, top-5 averages and harsh-braking sums matched
  direct InfluxDB queries over the same range. Two limits remain: the "Live Risk Score per Driver" lines look jagged because several trips per driver are scored milliseconds apart in the same micro-batch, and the dashboard references datasource uid `InfluxDB-Telematics` while the provisioned datasource gets a generated uid. That mismatch does not stop the panels loading.
- `results/grafana_dashboard.png` (earlier, before the fix; kept as history): the provisioned dashboard
  (http://localhost:3000/d/telematics-driver-risk/), last 30 minutes,
  captured with headless Chrome (`raw/grafana_screenshot_cmd.log`). Visible
  problems: "Risk Category Distribution" is one `_value` slice rather than
  one per category, and "Top 5 Riskiest Drivers" shows `_start`/`_stop`
  columns with the score column off-screen.

## AWS deployment

**What it was:** the full root `docker compose` stack (10 services) running
on **one EC2 instance**. It was not EKS, MSK, EMR or a managed service. All
logs are in `results/aws/`. Account IDs, IP addresses and SSM session IDs
were removed when the logs were saved (`devops/terraform-ec2/ssm_run.py`).

| | |
|---|---|
| Date / region | 2026-11-26, ap-southeast-2 (Sydney), availability zone ap-southeast-2c |
| Instance | `m7i-flex.large` (2 vCPU, 8 GiB), Amazon Linux 2023, 30 GB gp3 encrypted root, 4 GB swap file, IMDSv2 required |
| Network | Default VPC. Security group with 0 inbound rules, no key pair, no SSH. Every container port bound to `127.0.0.1` (`docker-compose.aws.yml`). Access only through SSM Run Command and Session Manager port forwarding. |
| Code deployed | `main` cloned by `user_data`, then `docker compose -f docker-compose.yml -f docker-compose.aws.yml up -d --build` |
| Terraform | `devops/terraform-ec2/`, 6 resources. Apply 08:34:22 → 08:34:59 UTC (`terraform_apply.log`). Destroy 09:32:07 → 09:32:48 UTC (`terraform_destroy.log`) |
| Total run time | 58 min 26 s from apply start to destroy end |
| Estimated cost (**estimate, not measured from the bill**) | about $0.13 ($0.1253). That is 58 min 26 s at AWS's published Sydney on-demand prices: m7i-flex.large $0.1197/h, 30 GB gp3 at $0.096 per GB-month (730 h/month), public IPv4 $0.005/h. Prices are listed in `devops/terraform-ec2/README.md`. |
| Safety net | `shutdown -h +300` with `instance_initiated_shutdown_behavior = terminate`. It was not needed. |

**Steps and evidence**

| Step | Result | Log |
|---|---|---|
| Session Manager reaches the instance | session opened and showed a `sh-5.2$` prompt. The piped, non-TTY test then hit its timeout (exit 124), so the `whoami` output was not captured. Run Command worked for every later step. | `c1_ssm_session_check.log` |
| Bootstrap (docker, compose v2.29.7, buildx v0.17.1 checked by sha256, repo clone) | done marker present | `c2_bootstrap_check.log`, `c2_bootstrap_full.log` |
| `compose up --build` | started 08:38:57 UTC. At the 08:43:48 check the unit reported `Result=success`, exit 0, with all services up. The exact end time was not recorded. The first attempt, sent directly through Run Command, ended `TimedOut` (exit 137). It was re-run as a `systemd-run` unit. | `c2_compose_up.log`, `c2_build_evidence.log` |
| Kafka receiving events | end offsets 1014 → 1119 in 30 s (+105) | `c2_verify_stack.log` |
| Spark → InfluxDB | 71 `risk_score` points from 30 distinct trips in the last 15 min | `c2_verify_stack.log` |
| Risk API (inside the instance) | `/health` 200, `/drivers/top-risk?limit=3` 200, `limit=0` 422 | `c2_verify_stack.log` |
| Grafana | `/api/health` 200 on the instance. Through an SSM port-forward to the laptop: `/api/health` and the dashboard URL both 200. Screenshot taken. | `c2_verify_stack.log`, `c3_port_forward_check.log`, `grafana_aws_via_ssm.png` |
| Tests (1 run, `python:3.11-slim` containers, `aws_tests.sh`) | `tests/` 17 passed. `risk_api` 46 passed (including 5 integration tests against the deployed InfluxDB), 1 warning, 100% coverage | `c2_tests.log` |
| Destroy + check | 6 destroyed. No running instances, volumes, own snapshots or AMIs, Elastic IPs, network interfaces, non-default security groups, `telematics*` IAM roles, instance profiles or log groups in the region. The terminated instance shows `User initiated (2026-11-26 09:32:15 GMT)`. | `terraform_destroy.log`, `d_post_destroy_verify.log` |

**End-to-end latency on EC2.** The method is the same as the local run:
`benchmarks/run_e2e.sh`, 3 runs, with the looping simulator stopped and
Spark started through `spark_with_progress.py`
(`devops/terraform-ec2/aws_e2e_benchmark.sh`). Numbers are from
`python benchmarks/summarize.py results/aws/raw` → `results/aws/summary_aws.json`,
shown as median (min–max) over 3 runs.

| Metric | EC2 | Local (above) |
|---|---|---|
| Event → score, median | 15.562 s (15.062–16.157) | 16.155 s |
| Event → score, p95 | 29.446 s (29.434–29.482) | 29.454 s |
| Last event of trip → score, median | 15.879 s (12.511–17.922) | |
| Events sent per run | 512 (492–531) | |
| Trips scored / produced | 15 / 15 in all 3 runs | |
| Events without a matching score | 0 in all 3 runs | |
| Clock offset applied | 32.5, 19.6, 18.8 ms | |
| Spark triggerExecution, batches with input | median 1,064 ms (605–1,683), 18 of 22 batches | 938 ms |

The latency is about the same as on the laptop because the 30 s trigger
sets it, not the hardware.

**Resources** (`c2_verify_stack.log`, `c2_resources_snapshots.log`, `free -m`, 4
readings with the full stack running): 2,285–2,370 MiB used of 7,779 MiB, swap
0 MiB used, root disk 12G of 30G (40%). The largest container was Spark at
829.9–831.5 MiB. On the laptop it was 786.7–789.8 MiB
(`raw/local_memory_docker_stats.log`).

**Not done on AWS:** see the NOT MEASURED table below. The host's `sshd`
was listening on port 22, but no inbound rule and no key pair existed, so it
could not be reached. It was not disabled.

## NOT MEASURED

| What | Why |
|---|---|
| Throughput of the **whole pipeline** (events/s Spark can score) | The demo producer sends about 3 events/s, and the 30 s trigger dominates. I didn't run a Spark load test, which would need a separate test design (rising input rate, watching batch time vs trigger). The ~1 s batch time at ≤200 rows/batch says nothing about capacity at higher load. |
| Model quality on real driving data | There is none in this project. Training and test data are both synthetic, from the same generator. |
| Model quality on live pipeline windows | No ground-truth label exists for simulated trips, so no MAE/R² is possible. The category mix above is the closest available evidence. |
| GitHub Actions run times and logs on GitHub | Not measured: `gh` isn't installed, and no CI run time or log was saved. **Observed only in the GitHub UI:** on 2026-11-26 the repo owner saw the checks pass on the pull request pages for #1, #2 and #3. That covered lint (flake8 + black), pytest and the Docker image build, and for #1 also the Risk API unit, integration and build jobs. The `Deploy to AWS (manual only)` job was skipped, as designed: it is a manual-only placeholder. |
| Kafka throughput, producer speed, model MAE/R² **on AWS** | Not run on the EC2 instance. Only the e2e latency benchmark, the stack check and the test suites were repeated there (see [AWS deployment](#aws-deployment)). |
| MSK, EKS, EMR, managed Grafana | Never deployed. The AWS run was one EC2 instance running Docker Compose. `devops/terraform` (MSK stack) has never been applied. |
| Actual AWS cost of the deployment (from the bill) | Not measured. Only the estimate in [AWS deployment](#aws-deployment) is given. AWS Billing shows the real figure after up to 24 h. |
| Kubernetes pipeline latency/throughput | The k8s deployment runs a different simulator config (10 drivers, 5 trips, 0.5 s) and its InfluxDB isn't exposed. Only its pod status and logs were checked. |
| GPU usage | Nothing in the project uses a GPU. |
| Coverage of `kafka_producer.py`, `stream_processor.py`, `influxdb_writer.py`, `train_model.py` | Measured as 0%. They have no unit tests. |

## What the project actually does

A Python simulator generates fake trip events (speed, braking/acceleration
g-force, GPS, night flag) for "safe", "moderate" and "aggressive" driver
profiles and sends them to a Kafka topic. A Spark Structured Streaming job,
running in local mode in one container, groups the events into 60-second
event-time windows per driver and trip. Every 30 seconds it collects those
windows on the driver and scores each one with a scikit-learn
GradientBoostingRegressor. The model was trained on 10,000 synthetic rows
whose labels come from a hand-written formula. The job writes each score and
a Low/Medium/High/Critical category to InfluxDB, and a provisioned Grafana
dashboard plots them. On this laptop, a score appears in InfluxDB a median
of about 16 s after its event is produced. That delay comes almost entirely
from the 30 s trigger. The repo also contains Dockerfiles, Kubernetes
manifests that have been run on a local Docker Desktop cluster, an MSK
Terraform stack that validates but has never been applied, a
single-EC2-instance Terraform stack that was applied once and destroyed the
same day (see [AWS deployment](#aws-deployment)), a read-only FastAPI
service (`risk_api/`) that serves the scores from InfluxDB, and GitHub
Actions workflows that lint, test and build images. The CI deploy job is
only a placeholder.
