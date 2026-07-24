# devops/terraform/variables.tf
#
# Input variables for the AWS deployment. Every value that differs between
# environments (dev/staging/prod, or between whoever is running `terraform
# apply`) is parameterised here rather than hardcoded in main.tf, so the
# same Terraform config can be reused across AWS accounts/regions.
#
# COST NOTE: every instance-size/storage-size default in this file was
# deliberately chosen for a short-lived, low-cost university demo (see the
# per-resource cost comments in main.tf), not for a production workload.

variable "aws_region" {
  description = "AWS region to deploy the telematics platform infrastructure into."
  type        = string
  default     = "eu-central-1" # Frankfurt -- keeps latency/data residency close to the simulated fleet's home region and to German insurer data-residency expectations.
}

variable "vpc_id" {
  description = "ID of the existing VPC to deploy MSK, EC2, and RDS into. Must have DNS hostnames and DNS resolution enabled (required for MSK) -- true by default for any VPC created via the AWS Console or `aws ec2 create-default-vpc`."
  type        = string
}

variable "subnet_ids" {
  description = "List of PUBLIC subnet IDs (each with a route to an Internet Gateway), one per Availability Zone. Must contain exactly as many subnets as var.msk_broker_count (default 3), since MSK requires broker count to be a whole multiple of the subnet count. Public subnets are required here because both MSK public access and the EC2 instance's public IP need a route to the internet -- this design avoids a NAT Gateway entirely (NAT Gateways cost ~$32+/month just for existing, which would blow a $60 demo budget on its own)."
  type        = list(string)
}

variable "my_ip_cidr" {
  description = "Your current public IP address in CIDR notation (e.g. \"203.0.113.42/32\"), used to lock down MSK's public Kafka port and SSH access to only your machine. Find your IP with `curl https://checkip.amazonaws.com` and append \"/32\". You'll need to update this and re-run `terraform apply` if your IP changes (e.g. switching networks) -- that's the tradeoff of locking access this tightly instead of leaving it open."
  type        = string
}

variable "msk_broker_count" {
  description = "Number of Kafka broker nodes in the MSK cluster. Must be a whole multiple of length(var.subnet_ids). Defaults to 3, both the project's stated minimum and a practical floor for a real multi-AZ MSK cluster (AWS technically allows fewer, but 3 brokers across 3 AZs is the smallest setup that behaves like a real deployment)."
  type        = number
  default     = 3

  validation {
    condition     = var.msk_broker_count >= 3
    error_message = "msk_broker_count must be at least 3 (project requirement + practical MSK minimum)."
  }
}

variable "msk_instance_type" {
  description = "EC2 instance type used for each MSK broker node. kafka.t3.small is AWS's cheapest MSK broker size -- appropriate for a low-traffic demo, not for sustained production throughput (t3 instances are burstable and will throttle under sustained load)."
  type        = string
  default     = "kafka.t3.small"
}

variable "msk_ebs_volume_size" {
  description = "EBS storage size (GB) attached to EACH MSK broker. Deliberately small (10GB default, down from a typical production default of 100GB+) since this is a short-lived demo generating a small volume of simulated trip events, not a production topic with real retention needs. This is charged per GB-month for as long as the cluster exists, whether or not brokers are actively used, so keep it small."
  type        = number
  default     = 10
}

variable "ec2_instance_type" {
  description = "EC2 instance type for the Spark processing node. t3.micro is AWS's smallest general-purpose burstable instance -- may be eligible for the AWS Free Tier (750 hrs/month) depending on your account's age/eligibility, which would make this component effectively free for the demo."
  type        = string
  default     = "t3.micro"
}

variable "ec2_root_volume_size" {
  description = "Root EBS volume size (GB) for the Spark EC2 instance. 20GB is enough for the OS, Docker, and the Spark container image -- reduced from a general-purpose 50GB default to cut storage cost."
  type        = number
  default     = 20
}

variable "ec2_key_name" {
  description = "Name of an existing EC2 key pair used for SSH access to the Spark instance."
  type        = string
  default     = null
}

variable "rds_instance_class" {
  description = "RDS instance class for the optional PostgreSQL metadata store. db.t3.micro is AWS's cheapest RDS instance class and may be eligible for the AWS Free Tier (750 hrs/month single-AZ db.t2/t3.micro) for accounts within their first 12 months."
  type        = string
  default     = "db.t3.micro"
}

variable "db_username" {
  description = "Master username for the optional RDS PostgreSQL metadata store."
  type        = string
  default     = "telematics_admin"
}

variable "db_password" {
  description = "Master password for the optional RDS PostgreSQL metadata store. Pass via TF_VAR_db_password or a *.tfvars file (already gitignored) -- never commit a real value here."
  type        = string
  sensitive   = true
}

variable "environment" {
  description = "Deployment environment name, used for resource naming/tagging (e.g. dev, staging, prod)."
  type        = string
  default     = "dev"
}
