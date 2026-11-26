# Resume claims checked against the code

Checked on 2026-11-26 against `main` @ `1b5d95c`, plus the two uncommitted
working-tree changes noted below. All numbers are from `results/results.md`.
Updated the same day after `api/` (Vehicle API) was deleted on `main` and
`deploy/` (its ECS Fargate Terraform) was removed. The infrastructure
evidence below reflects the code as it is now
(`results/raw/infra_counts_after_api_removal.log`). Updated again after the
single-EC2 AWS deployment ([results.md](results.md#aws-deployment)).

Verdicts: **SUPPORTED** (code exists and ran in this session), **PARTIAL**
(part of the claim holds, part doesn't), **NOT FOUND** (no code for it).

---

## Claim 1: "Built an end-to-end telemetry pipeline using Apache Kafka and Spark Structured Streaming to process trip events and generate near-real-time driver risk scores"

**Verdict: SUPPORTED**

| Part | Evidence |
|---|---|
| Kafka producer | [simulator/kafka_producer.py](../simulator/kafka_producer.py): `confluent_kafka.Producer`, topic `vehicle.trip.events`, keyed by `driver_id` |
| Spark Structured Streaming | [spark/stream_processor.py](../spark/stream_processor.py): `readStream.format("kafka")`, 60 s event-time tumbling window, 2 min watermark, `foreachBatch`, 30 s trigger |
| Ran end to end | This session: events produced → Spark (container) → InfluxDB, see `results/raw/e2e_latency_run*.log` |

Things to be ready for in an interview:
- **"Near-real-time" means 30-second micro-batches.** Latency is bounded by
  the `processingTime="30 seconds"` trigger; see the measured numbers in
  results.md. Say "micro-batch, scores within about half a minute" rather
  than "real-time".
- **One trip produces several score records.** The query uses `update`
  output mode, so every trigger re-scores each window that received new
  events, and a trip that crosses a 60 s window boundary is scored as two
  separate windows. The scored-record count in results.md is higher than
  the trip count for this reason.
- **The end-to-end demo depended on a fix that was uncommitted when this
  check was written** (now committed as the first commit on
  `feature/risk-api`). The previous `simulator/trip_generator.py` backdated
  trips by up to 24 h, and the 2-minute watermark then dropped most of them.
  Measured on a fresh clone of the code without the fix: **7 of 27** trips got a score. With the fix,
  15 of 15 got one in each of 3 runs.
- **Scoring runs on the Spark driver.** `process_batch` calls
  `batch_df.collect()` and scores rows in a Python loop. That is fine for
  this volume, but it is not distributed scoring. Spark runs in local mode
  in one container, not on a cluster.

---

## Claim 2: "Implemented real-time risk scoring with a Gradient Boosting model and persisted results in InfluxDB, categorizing driver behavior into risk levels"

**Verdict: SUPPORTED, with caveats about the model**

| Part | Evidence |
|---|---|
| Gradient Boosting | [ml/train_model.py](../ml/train_model.py): `GradientBoostingRegressor(n_estimators=200, learning_rate=0.05, max_depth=3, subsample=0.9)` |
| Scoring in the stream | [ml/risk_scorer.py](../ml/risk_scorer.py) `score_trip()`, called from `process_batch` in stream_processor.py |
| InfluxDB | [storage/influxdb_writer.py](../storage/influxdb_writer.py): measurement `driver_risk`, tags `driver_id`/`trip_id`/`risk_category` |
| Risk levels | `risk_scorer._risk_category`: Low < 25 ≤ Medium < 50 ≤ High < 75 ≤ Critical |

Things you could not defend if asked, so phrase them carefully:
- **The training data is synthetic, and so are the labels.** `risk_score` is
  a hand-written weighted formula of the features, rescaled into
  per-profile bands, plus noise. The held-out R² (0.975) shows the model
  recovers that formula. It says nothing about real accident risk. Say
  "trained on synthetic labelled data".
- **The training data doesn't match what the simulator produces.**
  `benchmarks/feature_skew.py` (results/raw/feature_skew.log): simulated
  trips average about 62 s, but the training data averages 900–1300 s.
  Harsh-event counts are 2–3× the training means. Night ratio is 0 because
  trips are now stamped with the current (daytime) time. As a result, 425
  of 456 "moderate" trips came out High or Critical.
- **Categories are per trip window, not per driver.** No per-driver
  aggregate is stored.

---

## Claim 3: "Set up monitoring and deployment with Grafana, Docker, Kubernetes, Terraform, and GitHub Actions for containerized deployment and CI/CD"

**Verdict: PARTIAL**

| Tool | Verdict | Evidence / gap |
|---|---|---|
| Grafana | SUPPORTED | [dashboard/grafana_config/](../dashboard/grafana_config/): provisioned datasource and a 4-panel dashboard, rendered in this session (`results/grafana_dashboard.png`). Two panels render poorly: "Risk Category Distribution" shows one `_value` slice instead of one per category, and "Top 5 Riskiest Drivers" shows `_start`/`_stop` columns with no visible risk score. |
| Monitoring (ops) | NOT FOUND | The old README mentioned Prometheus twice, but there is no Prometheus config, service or scrape target anywhere in the repo (that claim is now removed from the README). Grafana shows business data only, no service health. |
| Docker | SUPPORTED | 4 Dockerfiles (`devops/Dockerfile.{ml,simulator,spark}`, `risk_api/Dockerfile`). One root compose file (including `kafka/docker-compose.yml`) runs 10 services from a single `docker compose up`, verified from a fresh clone. |
| Kubernetes | SUPPORTED (local cluster only) | 6 manifests in [devops/k8s/](../devops/k8s/), 11 objects. `kubectl get all -n telematics` on the local Docker Desktop cluster shows the 4 pipeline deployments Running, age 125 days (`results/raw/k8s_local_state.log`). The Risk API manifest was only server-side dry-run, not deployed. Never deployed to EKS. No Grafana manifest. The Secret holds the dev token in plain text. |
| Terraform | PARTIAL | Two stacks. `devops/terraform`: 7 resource blocks (MSK, EC2, RDS, DB subnet group, 3 security groups). Passes `terraform validate`. **Never applied**: no state file exists and no remote backend is configured. The RDS "metadata_store" isn't used by any pipeline code. `devops/terraform-ec2`: 6 resources (one EC2 instance, a security group with no inbound rules, an SSM-only IAM role and instance profile). **Applied once** on 2026-11-26 to run the whole stack with Docker Compose on that one instance, verified, and **destroyed the same day** ([results.md](results.md#aws-deployment)). Not EKS, not MSK, not production. |
| GitHub Actions | PARTIAL | [ci.yml](../.github/workflows/ci.yml): lint → test → build images, which is real CI. The **`deploy` job is a placeholder that only `echo`s text.** [risk-api-ci.yml](../.github/workflows/risk-api-ci.yml): Risk API unit + integration tests and image build. Whether these pass on GitHub is NOT MEASURED (`gh` isn't installed). The same lint and test commands pass locally (`results/raw/lint_check_post_api_removal.log`, pytest logs). |

Things you could not defend:
- "CI/CD": there is CI, but no CD. The deploy job is a placeholder.
- Anything implying a production, managed or highly available AWS deployment (MSK, EKS, EMR): the only AWS deployment was one EC2 instance running Docker Compose for under an hour on 2026-11-26, then destroyed.
- "Monitoring" in the ops sense: there is no Prometheus and no alerting.

---

## Suggested rewording (only what the code supports)

- Built a streaming telematics pipeline (Kafka → Spark Structured Streaming
  → InfluxDB → Grafana) that scores simulated trip events in 30-second
  micro-batches; measured median event-to-score latency of 16.155 s (p95
  29.454 s) on a laptop.
- Trained a scikit-learn Gradient Boosting regressor on synthetic trip
  features (held-out MAE 2.646, R² 0.975 on a seeded 80/20 split) and
  served it inside the stream, writing per-trip risk scores and 4 risk
  categories to InfluxDB.
- Built a read-only FastAPI + Pydantic REST API over the InfluxDB scores
  (4 endpoints, input validation, consistent error responses), with 46
  tests (unit + integration against a real InfluxDB, 100% statement
  coverage).
- Containerised every component with Docker/Docker Compose (whole stack
  from one `docker compose up`), deployed the pipeline to a local
  Kubernetes cluster, wrote Terraform for AWS (an MSK/EC2/RDS design, validated,
  not applied; and a single-EC2 stack used once to run the whole
  Docker Compose stack, verified over SSM, then destroyed), and set up GitHub Actions CI for lint, tests and image
  builds.
