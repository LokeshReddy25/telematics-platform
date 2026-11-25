# Vehicle Telematics API — AWS deployment (ECS Fargate)

Deploys the FastAPI Vehicle Telematics API (`api/`) to AWS on **ECS Fargate**
behind an **Application Load Balancer**, with its own **RDS PostgreSQL**
database. Everything is defined in `terraform/` as infrastructure-as-code.
Nothing in this folder touches the streaming platform stack in
`devops/terraform/` — the two stacks are independent, so you can deploy or
destroy the API without affecting Kafka/Spark.

## Architecture

```
Internet
   │  HTTP :80
   ▼
Application Load Balancer (public, 2 AZs)
   │  :8000 (only from the ALB)
   ▼
ECS Fargate service (1 task, 0.25 vCPU / 0.5 GB)
   │  DATABASE_URL + JWT_SECRET from Secrets Manager
   │  PostgreSQL :5432 (only from the Fargate tasks)
   ▼
RDS PostgreSQL 16, db.t3.micro, 20 GB gp3 (not publicly accessible)

ECR repository holds the API image the Fargate task runs.
CloudWatch Logs group /ecs/telematics-dev-api keeps the task logs.
```

The stack creates its own VPC (`10.20.0.0/16`) with 2 public subnets across
2 AZs and an Internet Gateway. There is **no NAT Gateway** (it would cost
~$32/month just to exist): the Fargate tasks run in public subnets with
public IPs, and the security groups keep them reachable only through the
ALB, while RDS is reachable only from the tasks.

## Monthly cost estimate (ap-south-1, prices approximate — verify with the AWS Pricing Calculator)

| Component | Details | ~$/month |
|---|---|---|
| RDS db.t3.micro | 1×, single-AZ | ~$14.00 |
| RDS storage | 20 GB gp3 | ~$2.30 |
| Application Load Balancer | hourly + LCU, low traffic | ~$20.00 |
| Fargate | 1 task × 0.25 vCPU / 0.5 GB | ~$10.00 |
| Secrets Manager | 2 secrets | $0.80 |
| ECR | ~200 MB image | ~$0.02 |
| CloudWatch Logs | small volume, 7-day retention | ~$0.50 |
| **Total** | | **~$47/month** |

With the AWS Free Tier (first 12 months of a new account: 750 hrs/month of
db.t3.micro + 20 GB RDS storage free), the RDS part drops to ~$2 and the
total is **~$33/month**. The ALB (~$20) is the biggest fixed cost. Nothing
here stops billing on its own — run `terraform destroy` when you no longer
need the API live.

## Prerequisites

- An AWS account with permissions to create VPC, ECS, ECR, RDS, ALB, IAM,
  Secrets Manager, and CloudWatch resources.
- [AWS CLI](https://aws.amazon.com/cli/) configured (`aws configure` or
  `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` env vars), region
  `ap-south-1` (Mumbai).
- [Docker](https://docs.docker.com/get-docker/) (to build and push the image).
- [Terraform](https://developer.hashicorp.com/terraform/install) >= 1.5.0.
- This repo cloned locally, with the API code in `api/`.

## Deploy — steps in order

### 1. Set the secrets (do this before `terraform apply`)

The API needs a database password and a JWT signing secret. Both are
Terraform input variables — they are stored in Secrets Manager, never in
the image or in git. Pick one of these two ways:

**Option A — a `terraform.tfvars` file (simplest):**

```bash
cd deploy/terraform
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars: set a strong db_password (min 8 chars, no / " @
# or spaces) and jwt_secret (generate one with: openssl rand -hex 32)
```

`terraform.tfvars` is gitignored — never commit it.

**Option B — environment variables (no file):**

```bash
export TF_VAR_db_password='a-strong-password-here'
export TF_VAR_jwt_secret="$(openssl rand -hex 32)"
```

> Changing `jwt_secret` later invalidates every issued token (users just
> log in again). Changing `db_password` later requires updating the
> DATABASE_URL secret too — see "Rotating secrets" below.

### 2. Create the infrastructure

```bash
cd deploy/terraform
terraform init
terraform apply
```

Review the plan, type `yes`. This creates the VPC, ECR repo, RDS
database, Secrets Manager secrets, ECS cluster/service, and the ALB. It
takes ~5-10 minutes (RDS is the slow part).

Note the outputs — especially `api_url` (the public API URL) and
`ecr_repository_url`:

```bash
terraform output api_url
terraform output ecr_repository_url
```

### 3. Build the image and push it to ECR

The ECS service was created pointing at the `:latest` tag, which doesn't
exist in ECR yet — its first tasks will fail to start. That is expected;
push the image and the service will pick it up in step 4.

```bash
# from the repo root
aws ecr get-login-password --region ap-south-1 \
  | docker login --username AWS --password-stdin \
    "$(terraform -chdir=deploy/terraform output -raw ecr_repository_url | cut -d/ -f1)"

ECR_URL="$(terraform -chdir=deploy/terraform output -raw ecr_repository_url)"

docker build -t "$ECR_URL:latest" api/
docker push "$ECR_URL:latest"
```

(The `docker build` context is `api/` — the Dockerfile lives there.)

### 4. Start the service on the new image

```bash
aws ecs update-service \
  --cluster "$(terraform -chdir=deploy/terraform output -raw ecs_cluster_name)" \
  --service "$(terraform -chdir=deploy/terraform output -raw ecs_service_name)" \
  --force-new-deployment \
  --region ap-south-1
```

Wait a minute or two for the task to start and pass the ALB health check.

### 5. Verify

```bash
API_URL="$(terraform -chdir=deploy/terraform output -raw api_url)"

curl "$API_URL/health"          # expect: {"status":"ok"} (or similar 200)
curl "$API_URL/docs"            # interactive API docs (open in a browser)
```

Then exercise the API: register a user at `POST $API_URL/auth/register`,
log in at `POST $API_URL/auth/login` for a Bearer token, and call the
vehicles/trips/telemetry/risk endpoints with
`Authorization: Bearer <token>`. The full endpoint list is in the repo
README's "Vehicle Telematics API" section.

### 6. Check logs

```bash
aws logs tail /ecs/telematics-dev-api --follow --region ap-south-1
```

Or open CloudWatch → Log groups → `/ecs/telematics-dev-api` in the AWS
Console. Task failures (e.g. a bad image or a DB connection error) show
up here and in the ECS Console under the service's "Deployments" tab.

## Updating the API later

```bash
# rebuild + push with a versioned tag (or :latest again)
docker build -t "$ECR_URL:v2" api/
docker push "$ECR_URL:v2"

# point the service at it: either re-apply with -var image_tag=v2 ...
terraform -chdir=deploy/terraform apply -var image_tag=v2
# ... or update the service directly:
aws ecs update-service --cluster <cluster> --service <service> \
  --force-new-deployment --region ap-south-1
```

## Rotating secrets

- **JWT_SECRET:** update the secret value in Secrets Manager (Console or
  `aws secretsmanager put-secret-value`), then force a new deployment
  (step 4). Old tokens stop working immediately.
- **DB password:** change the password on the RDS instance, update the
  `DATABASE_URL` secret value to match, then force a new deployment. (Or
  change `db_password` and re-run `terraform apply` — Terraform updates
  both RDS and the secret for you.)

## Tear it all down

```bash
cd deploy/terraform
terraform destroy
```

Type `yes`. RDS is created with `skip_final_snapshot = true`, so destroy
leaves no final snapshot behind. Check the Console afterwards for anything
unexpectedly left running — `terraform destroy` should remove everything
this stack created.

## Cheaper variants

- **Skip the ALB:** if the API only needs to be reachable from your own
  machine, remove the ALB/target group/listener and reach the task
  directly (the task gets a public IP). Saves ~$20/month but loses the
  health checks and the stable DNS name.
- **Smaller footprint:** nothing here goes smaller than it already is
  (smallest Fargate size, smallest RDS class, minimum storage).
- **Turn it off when idle:** `terraform destroy` when you're done for
  the day and `terraform apply` again when you need it — the whole stack
  is reproducible from this folder.

## Troubleshooting

- **Tasks stuck in PENDING / failing to start:** usually the image
  hasn't been pushed yet (do step 3, then step 4), or the task can't
  reach the database. Check the ECS service events and the CloudWatch
  logs (step 6).
- **ALB health check failing (targets unhealthy):** the API's `/health`
  endpoint checks the database connection, so an unhealthy target
  usually means the task can't reach RDS — check the RDS security group
  and that the task is running. Give it 2-3 minutes after a fresh
  deployment; the first start includes pulling the image.
- **`terraform apply` fails on the DB password:** RDS rejects passwords
  shorter than 8 characters or containing `/`, `"`, `@`, or spaces.
- **"No space left" during `terraform init`:** the AWS provider download
  needs ~600 MB of temp space. Set `TMPDIR` to a bigger disk before
  init, e.g. `TMPDIR=$HOME/.terraform-tmp terraform init`.
