# devops/terraform/outputs.tf
#
# Values an operator needs after `terraform apply` to actually point the
# rest of the platform (Spark job config, kafka_producer.py --bootstrap
# flag, etc.) at the newly provisioned AWS resources.

output "msk_bootstrap_brokers" {
  description = "Plaintext bootstrap broker connection string for the MSK cluster (use as KAFKA_BOOTSTRAP_SERVERS)."
  value       = aws_msk_cluster.telematics.bootstrap_brokers
}

output "msk_cluster_arn" {
  description = "ARN of the provisioned MSK cluster, useful for IAM policy scoping."
  value       = aws_msk_cluster.telematics.arn
}

output "spark_ec2_public_ip" {
  description = "Public IP address of the EC2 instance running the Spark streaming job."
  value       = aws_instance.spark.public_ip
}

output "spark_ec2_instance_id" {
  description = "Instance ID of the Spark EC2 instance, useful for SSM Session Manager access."
  value       = aws_instance.spark.id
}

output "rds_endpoint" {
  description = "Connection endpoint (host:port) for the optional RDS PostgreSQL metadata store."
  value       = aws_db_instance.metadata_store.endpoint
}

output "rds_database_name" {
  description = "Name of the PostgreSQL database created on the RDS instance."
  value       = aws_db_instance.metadata_store.db_name
}
