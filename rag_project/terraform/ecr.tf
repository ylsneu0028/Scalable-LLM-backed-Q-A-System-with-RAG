resource "aws_ecr_repository" "api" {
  name                 = "${local.name_prefix}-api"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_repository" "embed" {
  name                 = "${local.name_prefix}-embed"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_repository" "vector" {
  name                 = "${local.name_prefix}-vector"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_repository" "llm" {
  name                 = "${local.name_prefix}-llm"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

locals {
  ecr_lifecycle_policy_json = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last 10 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 10
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

resource "aws_ecr_lifecycle_policy" "api" {
  repository = aws_ecr_repository.api.name
  policy     = local.ecr_lifecycle_policy_json
}

resource "aws_ecr_lifecycle_policy" "embed" {
  repository = aws_ecr_repository.embed.name
  policy     = local.ecr_lifecycle_policy_json
}

resource "aws_ecr_lifecycle_policy" "vector" {
  repository = aws_ecr_repository.vector.name
  policy     = local.ecr_lifecycle_policy_json
}

resource "aws_ecr_lifecycle_policy" "llm" {
  repository = aws_ecr_repository.llm.name
  policy     = local.ecr_lifecycle_policy_json
}
