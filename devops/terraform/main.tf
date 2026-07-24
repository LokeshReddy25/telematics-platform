# devops/terraform/main.tf
#
# AWS deployment for the telematics platform's production topology. This is
# the cloud equivalent of `kafka/docker-compose.yml` + the Spark container:
# local Kafka -> Amazon MSK, local Spark container -> an EC2 instance
# running Spark. See README.md's "How this maps to production" section for
# the full local-to-cloud component mapping.
#
# Deliberately NOT included here: InfluxDB and Grafana. In production these
# would typically be swapped for managed equivalents (Amazon Timestream /
# InfluxDB Cloud, Amazon Managed Grafana) which is an infra decision left to
# the operator rather than baked into this starter config.

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
# Security Groups
# ---------------------------------------------------------------------------

# Allows the Spark EC2 instance to reach MSK's broker ports, and allows
# operator SSH access. Kept separate from the MSK security group so each
# resource's network policy can evolve independently.
resource "aws_security_group" "spark_ec2" {
  name        = "telematics-${var.environment}-spark-ec2-sg"
  description = "Security group for the Spark processing EC2 instance"
  vpc_id      = var.vpc_id

  egress {
    description = "Allow all outbound traffic (MSK, RDS, package installs)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "SSH for operator access"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "telematics-${var.environment}-spark-ec2-sg"
    Environment = var.environment
  }
}

# MSK brokers only need to accept traffic from within the VPC (Spark, and
# any other in-VPC producers/consumers) -- never exposed to the public
# internet.
resource "aws_security_group" "msk" {
  name        = "telematics-${var.environment}-msk-sg"
  description = "Security group for the MSK Kafka cluster"
  vpc_id      = var.vpc_id

  ingress {
    description     = "Kafka plaintext broker port from the Spark instance"
    from_port       = 9092
    to_port         = 9092
    protocol        = "tcp"
    security_groups = [aws_security_group.spark_ec2.id]
  }

  ingress {
    description = "Kafka broker port from within the VPC CIDR (other producers/consumers)"
    from_port   = 9092
    to_port     = 9092
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/8"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "telematics-${var.environment}-msk-sg"
    Environment = var.environment
  }
}

# RDS only needs to be reachable from the Spark instance (which would run
# any metadata read/write jobs) -- not from MSK or the public internet.
resource "aws_security_group" "rds" {
  name        = "telematics-${var.environment}-rds-sg"
  description = "Security group for the optional RDS PostgreSQL metadata store"
  vpc_id      = var.vpc_id

  ingress {
    description     = "PostgreSQL from the Spark instance"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.spark_ec2.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "telematics-${var.environment}-rds-sg"
    Environment = var.environment
  }
}

# ---------------------------------------------------------------------------
# Amazon MSK (managed Kafka) -- replaces kafka/docker-compose.yml in production
# ---------------------------------------------------------------------------

resource "aws_msk_cluster" "telematics" {
  cluster_name           = "telematics-${var.environment}-msk"
  kafka_version           = "3.5.1"
  number_of_broker_nodes  = var.msk_broker_count

  broker_node_group_info {
    instance_type   = var.msk_instance_type
    client_subnets  = var.subnet_ids
    security_groups = [aws_security_group.msk.id]

    storage_info {
      ebs_storage_info {
        volume_size = 100
      }
    }
  }

  encryption_info {
    encryption_in_transit {
      client_broker = "TLS_PLAINTEXT"
      in_cluster    = true
    }
  }

  tags = {
    Name        = "telematics-${var.environment}-msk"
    Environment = var.environment
  }
}

# ---------------------------------------------------------------------------
# EC2 instance running the Spark streaming job -- replaces the local
# devops/Dockerfile.spark container in production (a small step toward EMR).
# ---------------------------------------------------------------------------

data "aws_ami" "amazon_linux" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_instance" "spark" {
  ami                    = data.aws_ami.amazon_linux.id
  instance_type          = var.ec2_instance_type
  subnet_id              = var.subnet_ids[0]
  vpc_security_group_ids = [aws_security_group.spark_ec2.id]
  key_name               = var.ec2_key_name

  # Bootstraps Docker on first boot so the same devops/Dockerfile.spark
  # image built locally can be pulled and run unchanged on this instance.
  user_data = <<-EOF
              #!/bin/bash
              set -e
              dnf update -y
              dnf install -y docker
              systemctl enable docker
              systemctl start docker
              EOF

  root_block_device {
    volume_size = 50
    volume_type = "gp3"
  }

  tags = {
    Name        = "telematics-${var.environment}-spark"
    Environment = var.environment
  }
}

# ---------------------------------------------------------------------------
# Optional RDS PostgreSQL metadata store (driver/trip reference data,
# separate from the InfluxDB time-series store used for risk scores).
# ---------------------------------------------------------------------------

resource "aws_db_subnet_group" "telematics" {
  name       = "telematics-${var.environment}-db-subnet-group"
  subnet_ids = var.subnet_ids

  tags = {
    Name        = "telematics-${var.environment}-db-subnet-group"
    Environment = var.environment
  }
}

resource "aws_db_instance" "metadata_store" {
  identifier              = "telematics-${var.environment}-metadata"
  engine                  = "postgres"
  engine_version          = "15.4"
  instance_class          = "db.t3.micro"
  allocated_storage       = 20
  db_name                 = "telematics_metadata"
  username                = var.db_username
  password                = var.db_password
  db_subnet_group_name    = aws_db_subnet_group.telematics.name
  vpc_security_group_ids  = [aws_security_group.rds.id]
  skip_final_snapshot     = true
  publicly_accessible     = false
  backup_retention_period = 7

  tags = {
    Name        = "telematics-${var.environment}-metadata"
    Environment = var.environment
  }
}
