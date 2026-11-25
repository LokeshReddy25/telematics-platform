# deploy/terraform/variables.tf
#
# Input variables for the Vehicle Telematics API deployment. Secrets
# (db_password, jwt_secret) have no defaults and no values in this file --
# pass them via TF_VAR_db_password / TF_VAR_jwt_secret environment
# variables or a terraform.tfvars file (gitignored, never committed).

variable "aws_region" {
  description = "AWS region to deploy the API infrastructure into."
  type        = string
  default     = "ap-south-1" # Mumbai -- closest region to the user (Chennai) and to the API's expected users.
}

variable "environment" {
  description = "Deployment environment name, used for resource naming/tagging (e.g. dev, staging, prod). Keep it short (12 chars max) so generated resource names stay within AWS length limits."
  type        = string
  default     = "dev"

  validation {
    condition     = can(regex("^[a-z0-9-]{1,12}$", var.environment))
    error_message = "environment must be 1-12 lowercase letters, digits, or hyphens (keeps ALB/target-group names within AWS limits)."
  }
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC this stack creates. Must not overlap with any VPC you plan to peer with later."
  type        = string
  default     = "10.20.0.0/16"
}

variable "api_port" {
  description = "Port the API container listens on (the Dockerfile exposes 8000; change this only if the app changes port)."
  type        = number
  default     = 8000
}

variable "image_tag" {
  description = "Tag of the API image in ECR to deploy. Build and push the image first (see deploy/README.md), then apply with this tag -- defaults to 'latest'."
  type        = string
  default     = "latest"
}

variable "task_cpu" {
  description = "Fargate CPU units for the API task (256 = 0.25 vCPU, the smallest Fargate size). Valid values: 256, 512, 1024, 2048, 4096."
  type        = number
  default     = 256
}

variable "task_memory" {
  description = "Fargate memory (MiB) for the API task. 512 MiB is the minimum for 0.25 vCPU. Valid values depend on task_cpu -- see the Fargate docs if you change either."
  type        = number
  default     = 512
}

variable "desired_count" {
  description = "Number of Fargate tasks to run. 1 is enough for a demo; raise it (and add an autoscaling policy) only if the API gets real traffic."
  type        = number
  default     = 1
}

variable "rds_instance_class" {
  description = "RDS instance class for the API's PostgreSQL database. db.t3.micro is AWS's cheapest RDS class and may be Free Tier eligible (750 hrs/month) for accounts within their first 12 months."
  type        = string
  default     = "db.t3.micro"
}

variable "rds_allocated_storage" {
  description = "RDS storage size in GB (gp3). 20 GB is the RDS minimum."
  type        = number
  default     = 20
}

variable "db_username" {
  description = "Master username for the API's RDS PostgreSQL database."
  type        = string
  default     = "telematics_admin"
}

variable "db_password" {
  description = "Master password for the API's RDS PostgreSQL database. At least 8 characters; avoid / \" @ and spaces (RDS rejects them). Pass via TF_VAR_db_password or a terraform.tfvars file (gitignored) -- never commit a real value. This password is stored in Secrets Manager as part of DATABASE_URL, so the API never needs it directly."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.db_password) >= 8
    error_message = "db_password must be at least 8 characters (RDS requirement)."
  }
}

variable "jwt_secret" {
  description = "Secret key the API uses to sign JWTs (HS256, 60-minute tokens). Generate a long random string, e.g. `openssl rand -hex 32`. Pass via TF_VAR_jwt_secret or a terraform.tfvars file (gitignored) -- never commit a real value. Changing it later invalidates every issued token (users just log in again)."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.jwt_secret) >= 16
    error_message = "jwt_secret must be at least 16 characters -- generate a long random value with `openssl rand -hex 32`."
  }
}
