# devops/terraform/variables.tf
#
# Input variables for the AWS deployment. Every value that differs between
# environments (dev/staging/prod, or between whoever is running `terraform
# apply`) is parameterised here rather than hardcoded in main.tf, so the
# same Terraform config can be reused across AWS accounts/regions.

variable "aws_region" {
  description = "AWS region to deploy the telematics platform infrastructure into."
  type        = string
  default     = "eu-central-1" # Frankfurt -- keeps latency/data residency close to the simulated fleet's home region and to German insurer data-residency expectations.
}

variable "vpc_id" {
  description = "ID of the existing VPC to deploy MSK, EC2, and RDS into."
  type        = string
}

variable "subnet_ids" {
  description = "List of subnet IDs (at least two, in different AZs) used by MSK brokers and RDS for high availability."
  type        = list(string)
}

variable "msk_broker_count" {
  description = "Number of Kafka broker nodes to provision in the MSK cluster. Must be a multiple of the number of subnets provided."
  type        = number
  default     = 2
}

variable "msk_instance_type" {
  description = "EC2 instance type used for each MSK broker node."
  type        = string
  default     = "kafka.m5.large"
}

variable "ec2_instance_type" {
  description = "EC2 instance type for the Spark processing node."
  type        = string
  default     = "m5.xlarge"
}

variable "ec2_key_name" {
  description = "Name of an existing EC2 key pair used for SSH access to the Spark instance."
  type        = string
  default     = null
}

variable "db_username" {
  description = "Master username for the optional RDS PostgreSQL metadata store."
  type        = string
  default     = "telematics_admin"
}

variable "db_password" {
  description = "Master password for the optional RDS PostgreSQL metadata store. Pass via TF_VAR_db_password or a secrets manager -- never commit a real value here."
  type        = string
  sensitive   = true
}

variable "environment" {
  description = "Deployment environment name, used for resource naming/tagging (e.g. dev, staging, prod)."
  type        = string
  default     = "dev"
}
