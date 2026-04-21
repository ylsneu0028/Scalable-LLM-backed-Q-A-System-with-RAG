output "alb_url" {
  description = "Public HTTP URL for the RAG API (use this instead of localhost:8000)"
  value       = "http://${aws_lb.main.dns_name}"
}

output "alb_dns_name" {
  value = aws_lb.main.dns_name
}

output "ecr_api_repository_url" {
  value = aws_ecr_repository.api.repository_url
}

output "ecr_embed_repository_url" {
  value = aws_ecr_repository.embed.repository_url
}

output "ecr_vector_repository_url" {
  value = aws_ecr_repository.vector.repository_url
}

output "ecr_llm_repository_url" {
  value = aws_ecr_repository.llm.repository_url
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "ecs_service_name" {
  value = aws_ecs_service.app.name
}

output "aws_region" {
  value = var.aws_region
}
