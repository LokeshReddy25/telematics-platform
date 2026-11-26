# devops/terraform-ec2/main.tf
#
# One EC2 instance running the whole telematics stack with Docker Compose.
# Access is ONLY through AWS Systems Manager Session Manager:
#   - the security group has no inbound rules at all,
#   - no SSH key pair, no Elastic IP,
#   - the public IP exists only so the instance can reach SSM, GitHub and
#     Docker Hub outbound (there is no NAT gateway in the default VPC).
# Separate from devops/terraform/ (the MSK design), which is not used here.

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.100"
    }
  }
}

locals {
  name = "telematics-demo"
  tags = {
    Project       = "telematics-demo"
    Owner         = "lokesh"
    AutoTerminate = "true"
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.tags
  }
}

# ---------------------------------------------------------------------------
# Lookups (read-only)
# ---------------------------------------------------------------------------

# Latest Amazon Linux 2023 AMI from AWS's public SSM parameter (not hardcoded).
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

data "aws_vpc" "default" {
  default = true
}

# AZs in this region that actually offer the chosen instance type.
data "aws_ec2_instance_type_offerings" "available" {
  location_type = "availability-zone"
  filter {
    name   = "instance-type"
    values = [var.instance_type]
  }
}

data "aws_subnets" "default_public" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
  filter {
    name   = "availability-zone"
    values = data.aws_ec2_instance_type_offerings.available.locations
  }
}

# ---------------------------------------------------------------------------
# Security group: NO inbound rules. Outbound HTTPS/HTTP/DNS for SSM, dnf,
# GitHub, Docker Hub, PyPI and Maven Central (Spark's Kafka connector).
# ---------------------------------------------------------------------------

resource "aws_security_group" "instance" {
  name        = "${local.name}-no-inbound"
  description = "telematics demo: no inbound rules; outbound only"
  vpc_id      = data.aws_vpc.default.id
  # No ingress blocks on purpose. Egress is defined below.
}

resource "aws_vpc_security_group_egress_rule" "all_outbound" {
  security_group_id = aws_security_group.instance.id
  description       = "Outbound to the internet (SSM endpoints, package and image registries)"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

# ---------------------------------------------------------------------------
# IAM: lets the SSM agent register the instance (Session Manager).
# Only AmazonSSMManagedInstanceCore; nothing else.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ssm" {
  name               = "${local.name}-ssm-role"
  description        = "telematics demo EC2: Systems Manager access only"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.ssm.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "ssm" {
  name = "${local.name}-ssm-profile"
  role = aws_iam_role.ssm.name
}

# ---------------------------------------------------------------------------
# The instance
# ---------------------------------------------------------------------------

resource "aws_instance" "app" {
  ami                         = data.aws_ssm_parameter.al2023.value
  instance_type               = var.instance_type
  subnet_id                   = sort(data.aws_subnets.default_public.ids)[0]
  vpc_security_group_ids      = [aws_security_group.instance.id]
  iam_instance_profile        = aws_iam_instance_profile.ssm.name
  associate_public_ip_address = true # outbound only; no inbound rules exist

  instance_initiated_shutdown_behavior = "terminate"

  metadata_options {
    http_tokens                 = "required" # IMDSv2 only
    http_endpoint               = "enabled"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_gb
    encrypted             = true
    delete_on_termination = true
    tags                  = merge(local.tags, { Name = "${local.name}-root" })
  }

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    auto_terminate_minutes = var.auto_terminate_minutes
    swap_gb                = var.swap_gb
    repo_url               = var.repo_url
    repo_branch            = var.repo_branch
    compose_override_b64   = filebase64("${path.module}/../../docker-compose.aws.yml")
  })
  user_data_replace_on_change = true

  tags = { Name = "${local.name}-app" }
}
