# devops/terraform-ec2/variables.tf

variable "aws_region" {
  description = "AWS region. Locked to Sydney for this project."
  type        = string
  default     = "ap-southeast-2"

  validation {
    condition     = var.aws_region == "ap-southeast-2"
    error_message = "This stack may only be deployed to ap-southeast-2 (Sydney)."
  }
}

variable "instance_type" {
  description = "EC2 instance type. Must be free-plan eligible in this account (checked with describe-instance-types --filters Name=free-tier-eligible,Values=true)."
  type        = string
  default     = "m7i-flex.large" # 2 vCPU, 8 GiB
}

variable "root_volume_gb" {
  description = "Size of the encrypted gp3 root volume in GB."
  type        = number
  default     = 30
}

variable "auto_terminate_minutes" {
  description = "Safety net: user_data schedules a shutdown this many minutes after first boot. With instance_initiated_shutdown_behavior = terminate, the instance (and its delete-on-termination root volume) is then terminated."
  type        = number
  default     = 300 # 5 hours

  validation {
    condition     = var.auto_terminate_minutes >= 30 && var.auto_terminate_minutes <= 480
    error_message = "auto_terminate_minutes must be between 30 and 480."
  }
}

variable "swap_gb" {
  description = "Swap file size created by user_data."
  type        = number
  default     = 4
}

variable "repo_url" {
  description = "Public Git repository cloned by user_data."
  type        = string
  default     = "https://github.com/LokeshReddy25/telematics-platform"
}

variable "repo_branch" {
  description = "Branch cloned by user_data."
  type        = string
  default     = "main"
}
