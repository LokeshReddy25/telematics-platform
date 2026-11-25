# deploy/terraform/main.tf
#
# AWS deployment for the Vehicle Telematics API (FastAPI + PostgreSQL),
# as a standalone, independently destroyable stack. The API is deployed on
# ECS Fargate behind an Application Load Balancer, with its own RDS
# PostgreSQL database. It is deliberately separate from
# devops/terraform/ (the Kafka/Spark streaming platform) so the API can be
# deployed, updated, or torn down without touching the platform stack.
#
# COST CONTROL DESIGN DECISIONS (read this before running `terraform apply`):
#   - No NAT Gateway anywhere. NAT Gateways bill ~$32+/month just for
#     existing. Instead, the Fargate tasks run in PUBLIC subnets with
#     public IPs (assign_public_ip = true), so they can pull the image
#     from ECR and reach RDS over the VPC-internal network. The tasks are
#     still only reachable through the ALB -- the service security group
#     allows port 8000 only from the ALB's security group.
#   - The ALB is the single biggest fixed cost here (~$19-20/month).
#     There is no cheaper managed way to give the API a public URL with
#     health checks; if the API only needs to be reachable from your own
#     machine, the ALB can be removed and the task reached directly (see
#     deploy/README.md "Cheaper variants").
#   - RDS has `skip_final_snapshot = true`, so `terraform destroy`
#     doesn't leave a final snapshot behind racking up storage charges.
#   - RDS storage is 20 GB gp3 (the minimum) and backups are kept 1 day.
#   - Fargate is sized at 0.25 vCPU / 0.5 GB, 1 task -- enough for a
#     demo API, not for production traffic.
#   - Secrets Manager holds exactly 2 secrets (DATABASE_URL, JWT_SECRET).
#     Each bills $0.40/month, so this is ~$0.80/month of the total.
#
# Deliberately NOT included here: the Kafka/Spark streaming pipeline and
# InfluxDB/Grafana (they live in devops/terraform/ and the repo-root
# docker-compose.yml). The Spark job can write risk scores to this API
# via POST /drivers/{driver_id}/risk-scores on the ALB URL.
#
# Secrets in this stack: db_password and jwt_secret are input variables
# (never hardcoded). Terraform state contains them in plaintext, so keep
# the state file local and private -- never commit *.tfstate or
# *.tfvars (both are gitignored; see the repo-root .gitignore).

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# ---------------------------------------------------------------------------
# VPC -- minimal, 2 public subnets across 2 AZs, no NAT Gateway
# ---------------------------------------------------------------------------

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "api" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name        = "telematics-${var.environment}-api-vpc"
    Environment = var.environment
  }
}

resource "aws_internet_gateway" "api" {
  vpc_id = aws_vpc.api.id

  tags = {
    Name        = "telematics-${var.environment}-api-igw"
    Environment = var.environment
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.api.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.api.id
  }

  tags = {
    Name        = "telematics-${var.environment}-api-public-rt"
    Environment = var.environment
  }
}

# Two public subnets, one per AZ. map_public_ip_on_launch = true so the
# Fargate tasks get public IPs and can reach ECR/the internet without a
# NAT Gateway. RDS also sits in these subnets but is NOT publicly
# accessible (publicly_accessible = false + security group below).
resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.api.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index + 1)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true

  tags = {
    Name        = "telematics-${var.environment}-api-public-${count.index + 1}"
    Environment = var.environment
  }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# ---------------------------------------------------------------------------
# Security Groups
# ---------------------------------------------------------------------------

# ALB: HTTP from anywhere (this is the public front door of the API).
resource "aws_security_group" "alb" {
  name        = "telematics-${var.environment}-api-alb-sg"
  description = "Security group for the Vehicle Telematics API load balancer"
  vpc_id      = aws_vpc.api.id

  ingress {
    description = "HTTP from the internet"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "Allow all outbound traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "telematics-${var.environment}-api-alb-sg"
    Environment = var.environment
  }
}

# Fargate service: port 8000 ONLY from the ALB. Nothing else on the
# internet can reach the containers directly.
resource "aws_security_group" "ecs" {
  name        = "telematics-${var.environment}-api-ecs-sg"
  description = "Security group for the Vehicle Telematics API Fargate tasks"
  vpc_id      = aws_vpc.api.id

  ingress {
    description     = "API port from the load balancer only"
    from_port       = var.api_port
    to_port         = var.api_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    description = "Allow all outbound traffic (ECR, RDS, package pulls)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "telematics-${var.environment}-api-ecs-sg"
    Environment = var.environment
  }
}

# RDS: PostgreSQL ONLY from the Fargate tasks. No public access rule at
# all -- and the instance itself also has publicly_accessible = false
# below, as a second layer of the same restriction.
resource "aws_security_group" "rds" {
  name        = "telematics-${var.environment}-api-rds-sg"
  description = "Security group for the Vehicle Telematics API RDS database"
  vpc_id      = aws_vpc.api.id

  ingress {
    description     = "PostgreSQL from the Fargate tasks"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }

  egress {
    description = "Allow all outbound traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "telematics-${var.environment}-api-rds-sg"
    Environment = var.environment
  }
}

# ---------------------------------------------------------------------------
# ECR repository for the API image
# ---------------------------------------------------------------------------

# ESTIMATED COST (ap-south-1, approximate):
#   - Image storage ~200 MB @ $0.10/GB-month = ~$0.02/month (negligible)
resource "aws_ecr_repository" "api" {
  name                 = "telematics-${var.environment}-vehicle-api"
  image_tag_mutability = "MUTABLE" # allows re-pushing the :latest tag on each deploy

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name        = "telematics-${var.environment}-vehicle-api"
    Environment = var.environment
  }
}

# Keep only the last 10 images so old pushes don't accumulate storage.
resource "aws_ecr_lifecycle_policy" "api" {
  repository = aws_ecr_repository.api.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep only the last 10 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 10
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

# ---------------------------------------------------------------------------
# RDS PostgreSQL -- the API's own database (separate from the streaming
# pipeline's InfluxDB time-series store)
# ---------------------------------------------------------------------------

resource "aws_db_subnet_group" "api" {
  name       = "telematics-${var.environment}-api-db-subnet-group"
  subnet_ids = aws_subnet.public[*].id

  tags = {
    Name        = "telematics-${var.environment}-api-db-subnet-group"
    Environment = var.environment
  }
}

# ESTIMATED COST (ap-south-1, approximate):
#   - 1x db.t3.micro single-AZ  = ~$0.019/hr  = ~$14/month
#     (may be AWS Free Tier eligible: 750 hrs/month for the first 12
#     months of a new account, which would make this ~$0)
#   - 20 GB gp3 storage         = ~$0.115/GB-month = ~$2.30/month
#   - RDS subtotal              ~= $16/month (~$2 with Free Tier)
resource "aws_db_instance" "api" {
  identifier             = "telematics-${var.environment}-api"
  engine                 = "postgres"
  engine_version         = "16.4"
  instance_class         = var.rds_instance_class
  allocated_storage      = var.rds_allocated_storage
  storage_type           = "gp3"
  db_name                = "telematics"
  username               = var.db_username
  password               = var.db_password
  db_subnet_group_name   = aws_db_subnet_group.api.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  # Both settings below exist specifically so `terraform destroy` leaves
  # NOTHING behind: no final snapshot billing storage after the DB is
  # gone, and the DB was never publicly reachable in the first place.
  skip_final_snapshot = true
  publicly_accessible = false

  storage_encrypted       = true
  backup_retention_period = 1
  deletion_protection     = false

  tags = {
    Name        = "telematics-${var.environment}-api"
    Environment = var.environment
  }
}

# ---------------------------------------------------------------------------
# Secrets Manager -- DATABASE_URL and JWT_SECRET, injected into the
# Fargate task as environment variables. The values come from the input
# variables (set via TF_VAR_... or terraform.tfvars, never committed).
# ---------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "database_url" {
  name        = "telematics-${var.environment}-api-database-url"
  description = "DATABASE_URL for the Vehicle Telematics API (PostgreSQL connection string for the RDS instance)"

  tags = {
    Name        = "telematics-${var.environment}-api-database-url"
    Environment = var.environment
  }
}

resource "aws_secretsmanager_secret_version" "database_url" {
  secret_id = aws_secretsmanager_secret.database_url.id
  secret_string = format(
    "postgresql://%s:%s@%s:%d/telematics",
    var.db_username,
    var.db_password,
    aws_db_instance.api.address,
    aws_db_instance.api.port,
  )
}

resource "aws_secretsmanager_secret" "jwt_secret" {
  name        = "telematics-${var.environment}-api-jwt-secret"
  description = "JWT_SECRET for the Vehicle Telematics API (HS256 signing key, 60-minute tokens)"

  tags = {
    Name        = "telematics-${var.environment}-api-jwt-secret"
    Environment = var.environment
  }
}

resource "aws_secretsmanager_secret_version" "jwt_secret" {
  secret_id     = aws_secretsmanager_secret.jwt_secret.id
  secret_string = var.jwt_secret
}

# The AWS-managed Secrets Manager KMS key, so the execution role's
# kms:Decrypt permission below can be scoped to exactly this key instead
# of "*".
data "aws_kms_key" "secretsmanager" {
  key_id = "alias/aws/secretsmanager"
}

# ---------------------------------------------------------------------------
# ECS -- cluster, execution role, task definition, service
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/telematics-${var.environment}-api"
  retention_in_days = 7

  tags = {
    Name        = "telematics-${var.environment}-api"
    Environment = var.environment
  }
}

resource "aws_ecs_cluster" "api" {
  name = "telematics-${var.environment}-api"

  tags = {
    Name        = "telematics-${var.environment}-api"
    Environment = var.environment
  }
}

resource "aws_iam_role" "ecs_execution" {
  name = "telematics-${var.environment}-api-ecs-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Name        = "telematics-${var.environment}-api-ecs-execution"
    Environment = var.environment
  }
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Lets the execution role read the two Secrets Manager secrets (for the
# task definition's `secrets` block) and decrypt them with the
# AWS-managed Secrets Manager key.
resource "aws_iam_role_policy" "ecs_execution_secrets" {
  name = "telematics-${var.environment}-api-read-secrets"
  role = aws_iam_role.ecs_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = "secretsmanager:GetSecretValue"
        Resource = [
          aws_secretsmanager_secret.database_url.arn,
          aws_secretsmanager_secret.jwt_secret.arn,
        ]
      },
      {
        Effect   = "Allow"
        Action   = "kms:Decrypt"
        Resource = data.aws_kms_key.secretsmanager.arn
      },
    ]
  })
}

# ESTIMATED COST (ap-south-1, approximate):
#   - 1x Fargate task, 0.25 vCPU / 0.5 GB = ~$0.0136/hr = ~$10/month
resource "aws_ecs_task_definition" "api" {
  family                   = "telematics-${var.environment}-api"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn

  container_definitions = jsonencode([
    {
      name      = "api"
      image     = "${aws_ecr_repository.api.repository_url}:${var.image_tag}"
      essential = true
      portMappings = [
        {
          containerPort = var.api_port
          protocol      = "tcp"
        }
      ]
      # DATABASE_URL and JWT_SECRET come from Secrets Manager, never from
      # the image or from plaintext in this file.
      secrets = [
        {
          name      = "DATABASE_URL"
          valueFrom = aws_secretsmanager_secret.database_url.arn
        },
        {
          name      = "JWT_SECRET"
          valueFrom = aws_secretsmanager_secret.jwt_secret.arn
        },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.api.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "api"
        }
      }
      # Container-level health check (the ALB also health-checks
      # /health). python:3.12-slim has no curl, so this uses the
      # standard-library urllib instead.
      healthCheck = {
        command = [
          "CMD-SHELL",
          "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/health')\"",
        ]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }
    },
  ])

  tags = {
    Name        = "telematics-${var.environment}-api"
    Environment = var.environment
  }
}

resource "aws_ecs_service" "api" {
  name             = "telematics-${var.environment}-api"
  cluster          = aws_ecs_cluster.api.id
  task_definition  = aws_ecs_task_definition.api.arn
  desired_count    = var.desired_count
  launch_type      = "FARGATE"
  platform_version = "LATEST"

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = var.api_port
  }

  # The service must wait for the listener, otherwise ECS tries to
  # attach the tasks to a target group with no listener yet.
  depends_on = [aws_lb_listener.http]

  tags = {
    Name        = "telematics-${var.environment}-api"
    Environment = var.environment
  }
}

# ---------------------------------------------------------------------------
# Application Load Balancer -- the public front door of the API
# ---------------------------------------------------------------------------

# ESTIMATED COST (ap-south-1, approximate):
#   - ALB hourly charge  = ~$0.0252/hr = ~$18.40/month
#   - LCU charges (low traffic)        = ~$1-2/month
#   - ALB subtotal                     ~= $20/month
resource "aws_lb" "api" {
  name               = "telematics-${var.environment}-api-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  tags = {
    Name        = "telematics-${var.environment}-api-alb"
    Environment = var.environment
  }
}

resource "aws_lb_target_group" "api" {
  name        = "telematics-${var.environment}-api-tg"
  port        = var.api_port
  protocol    = "HTTP"
  target_type = "ip" # Fargate tasks register by IP, not instance ID
  vpc_id      = aws_vpc.api.id

  health_check {
    path                = "/health"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 2
  }

  tags = {
    Name        = "telematics-${var.environment}-api-tg"
    Environment = var.environment
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.api.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

# ---------------------------------------------------------------------------
# TOTAL ESTIMATED COST while everything is running: ~$47/month
# (RDS ~$16 + ALB ~$20 + Fargate ~$10 + Secrets Manager ~$0.80 + ECR
# and CloudWatch Logs ~$0.50). With the AWS Free Tier (first 12 months:
# 750 hrs/month of db.t3.micro + 20 GB RDS storage free), the RDS part
# drops to ~$2 and the total is ~$33/month. Nothing here stops billing
# on its own -- run `terraform destroy` when you no longer need the API
# live. See deploy/README.md for the full breakdown.
# ---------------------------------------------------------------------------
