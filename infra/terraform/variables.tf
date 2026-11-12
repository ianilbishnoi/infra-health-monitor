variable "region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "ap-south-1"
}

variable "instance_type" {
  description = "EC2 instance type (keep it free-tier eligible)"
  type        = string
  default     = "t3.micro"
}

variable "repo_url" {
  description = "Git repository the server clones and builds"
  type        = string
  default     = "https://github.com/ianilbishnoi/infra-health-monitor.git"
}

variable "alert_email" {
  description = "Email for the zero-spend budget alert. Leave empty to skip creating the budget."
  type        = string
  default     = ""
}

variable "gemini_api_key" {
  description = "Optional Gemini API key for the /analyze endpoint. Leave empty to use rule-based triage."
  type        = string
  default     = ""
  sensitive   = true
}
