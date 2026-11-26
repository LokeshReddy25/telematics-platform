# devops/terraform-ec2: single-EC2 demo deployment

Runs the **whole telematics stack with Docker Compose on one EC2 instance** in
`ap-southeast-2` (Sydney). This is a short-lived demo, not a production design:
no EKS, no MSK, no load balancer, no managed services. It is separate from
`devops/terraform/` (the MSK design), which is not used here.

## What it creates (6 resources)

| Resource | Purpose |
|---|---|
| `aws_security_group.instance` | **No inbound rules.** Outbound only |
| `aws_vpc_security_group_egress_rule.all_outbound` | Outbound access to SSM endpoints, dnf, GitHub, Docker Hub, PyPI, Maven Central |
| `aws_iam_role.ssm` | Role that EC2 can assume |
| `aws_iam_role_policy_attachment.ssm_core` | AWS-managed `AmazonSSMManagedInstanceCore` only |
| `aws_iam_instance_profile.ssm` | Attaches the role to the instance |
| `aws_instance.app` | `m7i-flex.large` (2 vCPU, 8 GiB), Amazon Linux 2023 from the SSM public parameter, default VPC public subnet |

Plus read-only lookups: the AMI parameter, default VPC and subnets, and the
AZs that offer the instance type.

## Safety properties

- **No inbound access.** No ingress rules, no SSH key pair, no Elastic IP.
  Shell and port-forwarding go through **SSM Session Manager** only. The public
  IP is used only for outbound traffic (the default VPC has no NAT gateway).
- `docker-compose.aws.yml` also binds every container port to `127.0.0.1`.
- IMDSv2 required. Root volume gp3, encrypted, `delete_on_termination = true`.
- **Auto-terminate:** user_data runs `shutdown -h +<auto_terminate_minutes>`
  (default 300 = 5 h) as its first step, and
  `instance_initiated_shutdown_behavior = "terminate"` turns that shutdown into
  a termination. Still run `terraform destroy`: the safety net doesn't remove
  the security group or the IAM resources.
- Region locked to `ap-southeast-2` by variable validation.
- Tags on everything: `Project=telematics-demo`, `Owner=lokesh`, `AutoTerminate=true`.
- No secrets in the code. State, plans and `*.tfvars` are gitignored.

## user_data (first boot)

1. Schedules the auto-terminate shutdown.
2. Creates a 4 GB swap file.
3. `dnf install docker git`, then starts Docker.
4. Installs the Compose v2.29.7 and Buildx v0.17.1 plugins, checked against
   their published SHA-256 values.
5. Clones `main` to `/opt/telematics-platform` and writes `docker-compose.aws.yml` there.

It does **not** start the stack. Progress is logged to
`/var/log/telematics-bootstrap.log`, and `/var/lib/telematics-bootstrap.done`
marks completion.

## Use

```bash
cd devops/terraform-ec2
terraform init
terraform plan -out ec2.tfplan               # *.tfplan is gitignored
terraform apply ec2.tfplan                   # creates billable resources

aws ssm start-session --region ap-southeast-2 --target <instance_id>
#   on the instance:
#   cd /opt/telematics-platform
#   sudo docker compose -f docker-compose.yml -f docker-compose.aws.yml up -d --build

# Grafana on your laptop at http://localhost:3000
aws ssm start-session --region ap-southeast-2 --target <instance_id> \
  --document-name AWS-StartPortForwardingSession \
  --parameters portNumber=3000,localPortNumber=3000

terraform destroy
```

## Cost (on-demand, ap-southeast-2)

Source: AWS's public price list files, published 2026-11-24 (EC2/EBS) and
2026-11-25 (VPC).

| Item | Price |
|---|---|
| m7i-flex.large, Linux | $0.1197 per hour |
| gp3 storage | $0.096 per GB-month |
| In-use public IPv4 address | $0.005 per hour |

Free-plan credits may cover these charges.
