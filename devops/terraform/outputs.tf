# devops/terraform/outputs.tf
#
# Values an operator needs after `terraform apply` to actually point the
# rest of the platform (Spark job config, kafka_producer.py --bootstrap
# flag, etc.) at the newly provisioned AWS resources.

output "msk_bootstrap_brokers_private_tls" {
  description = "Private TLS bootstrap broker string, reachable only from inside the VPC (e.g. from the Spark EC2 instance). Use this as KAFKA_BOOTSTRAP_SERVERS when running stream_processor.py on the EC2 box."
  value       = aws_msk_cluster.telematics.bootstrap_brokers_tls
}

output "msk_bootstrap_brokers_public_tls" {
  description = "Public TLS bootstrap broker string, reachable from your own machine (only from the IP in var.my_ip_cidr, per the MSK security group). Use this if you want to run kafka_producer.py directly from your laptop against the real cluster."
  value       = aws_msk_cluster.telematics.bootstrap_brokers_public_tls
}

output "msk_cluster_arn" {
  description = "ARN of the provisioned MSK cluster, useful for IAM policy scoping."
  value       = aws_msk_cluster.telematics.arn
}

output "spark_ec2_public_ip" {
  description = "Public IP address of the EC2 instance running the Spark streaming job. SSH here using the key pair named in var.ec2_key_name."
  value       = aws_instance.spark.public_ip
}

output "spark_ec2_instance_id" {
  description = "Instance ID of the Spark EC2 instance, useful for SSM Session Manager access."
  value       = aws_instance.spark.id
}

output "rds_endpoint" {
  description = "Connection endpoint (host:port) for the optional RDS PostgreSQL metadata store. Only reachable from inside the VPC (e.g. from the Spark EC2 instance) -- RDS is not publicly accessible."
  value       = aws_db_instance.metadata_store.endpoint
}

output "rds_database_name" {
  description = "Name of the PostgreSQL database created on the RDS instance."
  value       = aws_db_instance.metadata_store.db_name
}
