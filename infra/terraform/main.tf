# Latest Amazon Linux 2023 AMI, looked up at plan time
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

data "aws_vpc" "default" {
  default = true
}

# HTTP only. No SSH port: the server bootstraps itself from user data.
resource "aws_security_group" "web" {
  name        = "infra-monitor-web"
  description = "HTTP access to infra-health-monitor"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "All outbound (package installs, git clone, LLM API)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_instance" "monitor" {
  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = var.instance_type
  vpc_security_group_ids = [aws_security_group.web.id]

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    repo_url       = var.repo_url
    gemini_api_key = var.gemini_api_key
  })

  user_data_replace_on_change = true

  root_block_device {
    volume_size           = 8
    volume_type           = "gp3"
    delete_on_termination = true
  }

  metadata_options {
    http_tokens = "required" # IMDSv2 only
  }

  tags = {
    Name = "infra-monitor"
  }
}

# Emails you as soon as anything costs more than $0.01
resource "aws_budgets_budget" "zero_spend" {
  count = var.alert_email == "" ? 0 : 1

  name         = "infra-monitor-zero-spend"
  budget_type  = "COST"
  limit_amount = "0.01"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }
}
