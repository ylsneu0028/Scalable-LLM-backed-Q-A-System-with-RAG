resource "aws_ecs_cluster" "main" {
  name = "${local.name_prefix}-cluster"
}

locals {
  awslogs = {
    logDriver = "awslogs"
    options = {
      "awslogs-group"         = aws_cloudwatch_log_group.ecs.name
      "awslogs-region"        = var.aws_region
      "awslogs-stream-prefix" = "placeholder"
    }
  }

  container_definitions = [
    {
      name      = "qdrant"
      image     = var.qdrant_image
      essential = true
      portMappings = [
        { containerPort = 6333, protocol = "tcp" },
        { containerPort = 6334, protocol = "tcp" },
      ]
      logConfiguration = merge(local.awslogs, {
        options = merge(
          local.awslogs.options,
          { "awslogs-stream-prefix" = "qdrant" }
        )
      })
    },
    {
      name      = "ollama"
      image     = var.ollama_image
      essential = true
      portMappings = [
        { containerPort = 11434, protocol = "tcp" },
      ]
      environment = [
        { name = "OLLAMA_HOST", value = "0.0.0.0:11434" },
        { name = "OLLAMA_MODEL", value = var.ollama_model },
      ]
      entryPoint = ["/bin/sh", "-c"]
      command    = [local.ollama_entrypoint]
      # Require the model in `ollama list` before HEALTHY — otherwise embed starts during `pull` and spikes RAM (vector 137, ollama 1).
      healthCheck = {
        command     = ["CMD-SHELL", format("ollama show %s >/dev/null 2>&1 || exit 1", var.ollama_model)]
        interval    = 15
        timeout     = 10
        retries     = 5
        # ECS caps container healthCheck.startPeriod at 300s (not the same as ALB / service grace).
        startPeriod = 300
      }
      logConfiguration = merge(local.awslogs, {
        options = merge(
          local.awslogs.options,
          { "awslogs-stream-prefix" = "ollama" }
        )
      })
    },
    {
      name      = "embed"
      image     = "${aws_ecr_repository.embed.repository_url}:${var.image_tag}"
      essential = true
      portMappings = [
        { containerPort = 8001, protocol = "tcp" },
      ]
      environment = [
        { name = "MODEL_NAME", value = var.embed_model_name },
        { name = "OMP_NUM_THREADS", value = "4" },
        { name = "MKL_NUM_THREADS", value = "4" },
        { name = "TOKENIZERS_PARALLELISM", value = "false" },
      ]
      dependsOn = [
        { containerName = "ollama", condition = "HEALTHY" },
      ]
      logConfiguration = merge(local.awslogs, {
        options = merge(
          local.awslogs.options,
          { "awslogs-stream-prefix" = "embed" }
        )
      })
    },
    {
      name      = "vector"
      image     = "${aws_ecr_repository.vector.repository_url}:${var.image_tag}"
      essential = true
      portMappings = [
        { containerPort = 8002, protocol = "tcp" },
      ]
      environment = [
        { name = "QDRANT_URL", value = local.qdrant_url },
        { name = "COLLECTION_NAME", value = var.collection_name },
        { name = "VECTOR_DIM", value = tostring(var.vector_dim) },
        { name = "ALLOW_BENCH_RESET", value = "0" },
      ]
      dependsOn = [
        { containerName = "qdrant", condition = "START" },
      ]
      logConfiguration = merge(local.awslogs, {
        options = merge(
          local.awslogs.options,
          { "awslogs-stream-prefix" = "vector" }
        )
      })
    },
    {
      name      = "llm"
      image     = "${aws_ecr_repository.llm.repository_url}:${var.image_tag}"
      essential = true
      portMappings = [
        { containerPort = 8003, protocol = "tcp" },
      ]
      environment = [
        { name = "OLLAMA_URL", value = local.ollama_url_for_llm },
        { name = "OLLAMA_MODEL", value = var.ollama_model },
      ]
      dependsOn = [
        { containerName = "ollama", condition = "HEALTHY" },
      ]
      logConfiguration = merge(local.awslogs, {
        options = merge(
          local.awslogs.options,
          { "awslogs-stream-prefix" = "llm" }
        )
      })
    },
    {
      name      = "api"
      image     = "${aws_ecr_repository.api.repository_url}:${var.image_tag}"
      essential = true
      portMappings = [
        { containerPort = 8000, protocol = "tcp" },
      ]
      environment = [
        { name = "VECTOR_URL", value = local.vector_url },
        { name = "EMBED_URL", value = local.embed_url },
        { name = "LLM_URL", value = local.llm_url },
        { name = "OLLAMA_MODEL", value = var.ollama_model },
        { name = "COLLECTION_NAME", value = var.collection_name },
        { name = "TOP_K", value = tostring(var.top_k) },
      ]
      dependsOn = [
        { containerName = "embed", condition = "START" },
        { containerName = "vector", condition = "START" },
        { containerName = "llm", condition = "START" },
      ]
      logConfiguration = merge(local.awslogs, {
        options = merge(
          local.awslogs.options,
          { "awslogs-stream-prefix" = "api" }
        )
      })
    },
  ]
}

resource "aws_ecs_task_definition" "app" {
  family                   = "${local.name_prefix}-rag"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = tostring(var.task_cpu)
  memory                   = tostring(var.task_memory)

  execution_role_arn = aws_iam_role.ecs_task_execution.arn
  task_role_arn      = aws_iam_role.ecs_task.arn

  ephemeral_storage {
    size_in_gib = var.task_ephemeral_storage_gib
  }

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode(local.container_definitions)
}

resource "aws_ecs_service" "app" {
  name             = "${local.name_prefix}-svc"
  cluster          = aws_ecs_cluster.main.id
  task_definition  = aws_ecs_task_definition.app.arn
  desired_count    = var.desired_count
  launch_type      = "FARGATE"
  platform_version = "1.4.0"

  # Avoid needing 2× task vCPU during deploy (default rolling keeps old task while starting new → quota errors on small Fargate limits).
  deployment_maximum_percent         = 100
  deployment_minimum_healthy_percent = 0

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  health_check_grace_period_seconds = 1200

  depends_on = [aws_lb_listener.http]
}
