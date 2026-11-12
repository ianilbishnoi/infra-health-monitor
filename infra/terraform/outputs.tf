output "instance_id" {
  value = aws_instance.monitor.id
}

output "public_ip" {
  value = aws_instance.monitor.public_ip
}

output "url" {
  description = "Open /docs here once the bootstrap finishes (about 3 minutes)"
  value       = "http://${aws_instance.monitor.public_ip}"
}
