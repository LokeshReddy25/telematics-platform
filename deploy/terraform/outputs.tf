# deploy/terraform/outputs.tf
#
# Values an operator needs after `terraform apply` to deploy the image,
# verify the API, and point clients at it.

output "api_url" {
  description = "Public URL of the Vehicle Telematics API (the ALB DNS name). Use this as the API base URL -- e.g. curl $api_url/health, and the interactive docs live at $api_url/docs."
  value       = "http://${aws_lb.api.dns_name}"
}

output "alb_dns_name" {
  description = "DNS name of the Application Load Balancer fronting the API."
  value       = aws_lb.api.dns_name
}

output "ecr_repository_url" {
  description = "URL of the ECR repository for the API image. Build the image and push it here (see deploy/README.md), then update the ECS service to the new image."
  value       = aws_ecr_repository.api.repository_url
}

output "ecs_cluster_name" {
  description = "Name of the ECS cluster running the API."
  value       = aws_ecs_cluster.api.name
}

output "ecs_service_name" {
  description = "Name of the ECS service running the API."
  value       = aws_ecs_service.api.name
}

output "rds_endpoint" {
  description = "Connection endpoint (host:port) of the API's RDS PostgreSQL instance. Only reachable from inside the VPC (from the Fargate tasks) -- RDS is not publicly accessible."
  value       = aws_db_instance.api.endpoint
}

output "rds_database_name" {
  description = "Name of the PostgreSQL database created on the RDS instance."
  value       = aws_db_instance.api.db_name
}

output "database_url_secret_arn" {
  description = "ARN of the Secrets Manager secret holding DATABASE_URL, for reference if you need to rotate the database password (update the secret, then force a new ECS deployment)."
  value       = aws_secretsmanager_secret.database_url.arn
}

output "jwt_secret_arn" {
  description = "ARN of the Secrets Manager secret holding JWT_SECRET, for reference if you need to rotate it (update the secret, then force a new ECS deployment -- old tokens stop working)."
  value       = aws_secretsmanager_secret.jwt_secret.arn
}
