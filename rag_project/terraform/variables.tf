variable "aws_region" {
  type        = string
  description = "AWS region (e.g. us-west-2)"
  default     = "us-west-2"
}

variable "project_name" {
  type        = string
  description = "Prefix for resource names"
  default     = "rag-qa"
}

variable "image_tag" {
  type        = string
  description = "Tag for images pushed to ECR (api, embed, vector, llm)"
  default     = "latest"
}

variable "ollama_model" {
  type        = string
  description = "Model id for Ollama (pulled on first task start)"
  default     = "llama3.2:1b"
}

variable "qdrant_image" {
  type    = string
  default = "qdrant/qdrant:v1.12.5"
}

variable "ollama_image" {
  type    = string
  default = "ollama/ollama:latest"
}

variable "embed_model_name" {
  type    = string
  default = "sentence-transformers/all-MiniLM-L6-v2"
}

variable "collection_name" {
  type    = string
  default = "rag_chunks"
}

variable "vector_dim" {
  type    = number
  default = 384
}

variable "top_k" {
  type    = number
  default = 5
}

variable "task_cpu" {
  type        = number
  description = "Fargate task CPU units (1024 = 1 vCPU). Default 8 vCPU fits many accounts’ quotas; raise in tfvars if you have quota + still see OOM (137)."
  default     = 8192
}

variable "task_memory" {
  type        = number
  description = "Fargate task memory (MiB); must match CPU per AWS table. 61440 is max for 8 vCPU."
  default     = 61440
}

variable "task_ephemeral_storage_gib" {
  type        = number
  description = "Fargate ephemeral disk (GiB); embedding image layers are large"
  default     = 50
}

variable "desired_count" {
  type        = number
  description = "Number of ECS tasks (1 = single-task multi-container)"
  default     = 1
}

variable "allowed_ingress_cidr" {
  type        = string
  description = "CIDR allowed to reach the public ALB (HTTP)"
  default     = "0.0.0.0/0"
}

variable "alb_deletion_protection" {
  type    = bool
  default = false
}

variable "alb_idle_timeout_seconds" {
  type        = number
  description = "ALB idle timeout (1–4000s). POST /query can run many minutes on cold start."
  default     = 900
}
