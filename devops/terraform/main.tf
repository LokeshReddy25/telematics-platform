# devops/terraform/main.tf
#
# AWS deployment for the telematics platform, sized and locked down for a
# short-lived, cost-controlled university project demo (~$60 total credit
# budget) -- NOT a production sizing. See README.md's "How this maps to
# production" section for the general local-to-cloud component mapping,
# and the per-resource "ESTIMATED COST" comments below for this config's
# actual approximate hourly cost.
#
# COST CONTROL DESIGN DECISIONS (read this before running `terraform apply`):
#   - No NAT Gateway anywhere. NAT Gateways bill ~$32+/month just for
#     existing, regardless of use -- that alone could blow this budget.
#     Instead, MSK and EC2 both sit in PUBLIC subnets with public IPs.
#   - No standalone Elastic IP resource. The EC2 instance gets an ephemeral
#     public IP that's released automatically on termination -- avoids the
#     classic "orphaned EIP still billing after `terraform destroy`" trap.
#   - RDS has `skip_final_snapshot = true`, so `terraform destroy` doesn't
#     leave a final snapshot behind racking up storage charges.
#   - EBS volumes (MSK broker storage, EC2 root disk) are sized down from
#     typical production defaults -- see variables.tf for the reasoning.
#   - MSK public access uses TLS transit encryption with NO client
#     authentication (no SASL/IAM). Security instead comes from the
#     security group restricting the public Kafka port to a single IP
#     (var.my_ip_cidr). This is a deliberate simplicity/cost tradeoff for a
#     personal demo -- it is NOT how you'd secure a shared/production
#     cluster, where SASL/IAM auth would be worth the added complexity.
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

# Firewall for the EC2 Spark instance. SSH is locked to var.my_ip_cidr
# (the same variable used for MSK's public port below) rather than left
# open to 0.0.0.0/0 -- an open SSH port is exactly the kind of thing that
# gets a demo box compromised and unexpectedly running someone else's
# workload on your AWS bill.
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
    description = "SSH, locked to your current public IP"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.my_ip_cidr]
  }

  tags = {
    Name        = "telematics-${var.environment}-spark-ec2-sg"
    Environment = var.environment
  }
}

# Firewall for the MSK cluster. Two ingress rules, matching the two ways
# this cluster gets reached:
#   1. From the EC2 Spark instance, over MSK's PRIVATE TLS port (9094) --
#      in-VPC traffic never needs to leave the VPC to reach the broker.
#   2. From your own machine, over MSK's PUBLIC TLS port (9194) -- locked
#      to var.my_ip_cidr. Public access requires a separate port from the
#      private one (AWS adds +100 to the private port number for the
#      public equivalent) and requires TLS -- MSK does not allow an
#      unencrypted public endpoint.
resource "aws_security_group" "msk" {
  name        = "telematics-${var.environment}-msk-sg"
  description = "Security group for the MSK Kafka cluster"
  vpc_id      = var.vpc_id

  ingress {
    description     = "Kafka private TLS port (9094) from the Spark EC2 instance"
    from_port       = 9094
    to_port         = 9094
    protocol        = "tcp"
    security_groups = [aws_security_group.spark_ec2.id]
  }

  ingress {
    description = "Kafka public TLS port (9194), locked to your current public IP"
    from_port   = 9194
    to_port     = 9194
    protocol    = "tcp"
    cidr_blocks = [var.my_ip_cidr]
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

# Firewall for RDS. Deliberately has NO public access rule at all -- only
# the Spark EC2 instance can reach it, over the VPC-internal network. RDS
# itself also has publicly_accessible = false set below as a second,
# belt-and-suspenders layer of the same restriction.
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

# ESTIMATED COST (eu-central-1, approximate, verify on the AWS Pricing
# Calculator before applying):
#   - 3x kafka.t3.small brokers @ ~$0.042/hr each  = ~$0.126/hr
#   - 3x 10GB EBS storage @ ~$0.0014/hr each        = ~$0.004/hr
#   - MSK subtotal                                  ~= $0.13/hr
resource "aws_msk_cluster" "telematics" {
  cluster_name           = "telematics-${var.environment}-msk"
  kafka_version          = "3.5.1"
  number_of_broker_nodes = var.msk_broker_count

  broker_node_group_info {
    instance_type   = var.msk_instance_type
    client_subnets  = var.subnet_ids
    security_groups = [aws_security_group.msk.id]

    storage_info {
      ebs_storage_info {
        volume_size = var.msk_ebs_volume_size
      }
    }

    # Enables MSK's public endpoint. "SERVICE_PROVIDED_EIPS" means AWS
    # manages the public IPs for you (not a standalone aws_eip resource
    # you'd have to remember to clean up) -- they're released automatically
    # when the cluster is destroyed.
    connectivity_info {
      public_access {
        type = "SERVICE_PROVIDED_EIPS"
      }
    }
  }

  # Public access requires TLS on the client-broker connection -- MSK does
  # not allow an unencrypted (PLAINTEXT) public endpoint. This encrypts
  # traffic on BOTH the private and public listeners (Kafka doesn't let you
  # mix encrypted-public with unencrypted-private on the same cluster).
  encryption_info {
    encryption_in_transit {
      client_broker = "TLS"
      in_cluster    = true
    }
  }

  # No client_authentication block -- deliberately left as unauthenticated.
  # See the file-level comment above for why: the security group already
  # restricts the public port to a single IP, and adding SASL/IAM auth
  # would require an AWS Secrets Manager secret (extra cost + complexity)
  # for a personal, short-lived demo where that tradeoff isn't worth it.

  # Cross-variable check that Terraform's per-variable `validation` blocks
  # can't express (they can only see the variable being validated, not
  # other variables) -- MSK requires broker count to be a whole multiple of
  # the subnet count. A lifecycle precondition CAN see other
  # variables/resources, and runs at plan time before anything is created.
  lifecycle {
    precondition {
      condition     = var.msk_broker_count % length(var.subnet_ids) == 0
      error_message = "msk_broker_count (${var.msk_broker_count}) must be a whole multiple of the number of subnet_ids provided (${length(var.subnet_ids)})."
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

# ESTIMATED COST (eu-central-1, approximate):
#   - 1x t3.micro on-demand      = ~$0.011/hr (may be AWS Free Tier eligible)
#   - 20GB gp3 root volume       = ~$0.002/hr
#   - EC2 subtotal               ~= $0.013/hr
resource "aws_instance" "spark" {
  ami                    = data.aws_ami.amazon_linux.id
  instance_type          = var.ec2_instance_type
  subnet_id              = var.subnet_ids[0]
  vpc_security_group_ids = [aws_security_group.spark_ec2.id]
  key_name               = var.ec2_key_name

  # Explicit, ephemeral public IP -- tied to the instance's lifecycle and
  # released automatically on termination. Deliberately NOT a standalone
  # aws_eip resource, which would keep billing (~$0.005/hr) if ever left
  # unattached after a partial destroy.
  associate_public_ip_address = true

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
    volume_size = var.ec2_root_volume_size
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

# ESTIMATED COST (eu-central-1, approximate):
#   - 1x db.t3.micro single-AZ   = ~$0.018/hr (may be AWS Free Tier eligible)
#   - 20GB gp2 storage           = ~$0.003/hr
#   - RDS subtotal               ~= $0.021/hr
resource "aws_db_instance" "metadata_store" {
  identifier             = "telematics-${var.environment}-metadata"
  engine                 = "postgres"
  engine_version         = "15.4"
  instance_class         = var.rds_instance_class
  allocated_storage      = 20
  db_name                = "telematics_metadata"
  username               = var.db_username
  password               = var.db_password
  db_subnet_group_name   = aws_db_subnet_group.telematics.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  # Both settings below exist specifically so `terraform destroy` leaves
  # NOTHING behind: no final snapshot billing storage after the DB is gone,
  # and the DB was never publicly reachable in the first place.
  skip_final_snapshot = true
  publicly_accessible = false

  backup_retention_period = 7

  tags = {
    Name        = "telematics-${var.environment}-metadata"
    Environment = var.environment
  }
}

# ---------------------------------------------------------------------------
# TOTAL ESTIMATED COST while everything is running: ~$0.16-0.17/hr
# (MSK ~$0.13 + EC2 ~$0.013 + RDS ~$0.021). An 8-hour demo day is roughly
# $1.30-1.40 -- comfortably inside a $60 budget PROVIDED you run
# `terraform destroy` when you're done for the day. Nothing here stops
# billing on its own; AWS charges for provisioned resources whether or not
# you're actively using them.
# ---------------------------------------------------------------------------
