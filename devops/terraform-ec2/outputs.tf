# devops/terraform-ec2/outputs.tf
# No IP addresses or account IDs are output on purpose.

output "instance_id" {
  description = "EC2 instance ID (use it as the SSM target)."
  value       = aws_instance.app.id
}

output "availability_zone" {
  value = aws_instance.app.availability_zone
}

output "ami_id" {
  description = "Amazon Linux 2023 AMI resolved from the SSM public parameter."
  value       = aws_instance.app.ami
  sensitive   = true # the SSM parameter value is marked sensitive by the provider
}

output "ssm_session_command" {
  description = "Open a shell on the instance (no SSH, no open ports)."
  value       = "aws ssm start-session --region ${var.aws_region} --target ${aws_instance.app.id}"
}

output "grafana_port_forward_command" {
  description = "Forward Grafana on the instance to http://localhost:3000 on your laptop."
  value       = "aws ssm start-session --region ${var.aws_region} --target ${aws_instance.app.id} --document-name AWS-StartPortForwardingSession --parameters portNumber=3000,localPortNumber=3000"
}

output "auto_terminate_minutes" {
  value = var.auto_terminate_minutes
}
