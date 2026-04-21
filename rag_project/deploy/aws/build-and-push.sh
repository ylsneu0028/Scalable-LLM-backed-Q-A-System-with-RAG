#!/usr/bin/env bash
# Build app images for linux/amd64 (Fargate), tag with ECR URLs, push, then roll ECS.
#
# Prerequisites: AWS CLI configured; Docker; terraform applied once (ECR repos exist).
#
#   cd rag_project
#   chmod +x deploy/aws/build-and-push.sh
#   ./deploy/aws/build-and-push.sh
#
# Env:
#   AWS_REGION / AWS_DEFAULT_REGION   default us-west-2
#   IMAGE_TAG                         default latest (must match var.image_tag in Terraform)
#   DOCKER_PLATFORM                   default linux/amd64 (required on Apple Silicon)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAG_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TF_DIR="$RAG_ROOT/terraform"

REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-west-2}}"
export AWS_REGION="$REGION"
TAG="${IMAGE_TAG:-latest}"
PLATFORM="${DOCKER_PLATFORM:-linux/amd64}"

cd "$TF_DIR"
if ! terraform output -raw ecr_api_repository_url &>/dev/null; then
  echo "Terraform outputs missing. From rag_project/terraform run: terraform init && terraform apply"
  exit 1
fi

API_REPO=$(terraform output -raw ecr_api_repository_url)
EMBED_REPO=$(terraform output -raw ecr_embed_repository_url)
VECTOR_REPO=$(terraform output -raw ecr_vector_repository_url)
LLM_REPO=$(terraform output -raw ecr_llm_repository_url)
CLUSTER=$(terraform output -raw ecs_cluster_name)
SERVICE=$(terraform output -raw ecs_service_name)

REGISTRY="${API_REPO%%/*}"
echo "Logging in to ECR $REGISTRY ..."
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"

cd "$RAG_ROOT"

echo "Building for platform $PLATFORM (Fargate uses x86_64) ..."
docker build --platform "$PLATFORM" -t "${API_REPO}:${TAG}" services/api
docker build --platform "$PLATFORM" -t "${EMBED_REPO}:${TAG}" services/embed
docker build --platform "$PLATFORM" -t "${VECTOR_REPO}:${TAG}" services/vector
docker build --platform "$PLATFORM" -t "${LLM_REPO}:${TAG}" services/llm

echo "Pushing ..."
docker push "${API_REPO}:${TAG}"
docker push "${EMBED_REPO}:${TAG}"
docker push "${VECTOR_REPO}:${TAG}"
docker push "${LLM_REPO}:${TAG}"

echo "Forcing new ECS deployment ..."
aws ecs update-service \
  --region "$REGION" \
  --cluster "$CLUSTER" \
  --service "$SERVICE" \
  --force-new-deployment \
  >/dev/null

echo "Done. When tasks are healthy, open: $(terraform -chdir="$TF_DIR" output -raw alb_url)"
echo "Example: curl -s \$(terraform -chdir=$TF_DIR output -raw alb_url)/health"
