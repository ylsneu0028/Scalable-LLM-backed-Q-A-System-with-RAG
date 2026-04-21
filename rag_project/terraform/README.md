# AWS deployment (Terraform + ECS Fargate)

This stack deploys the RAG system as **one Fargate task** running six containers: **qdrant**, **ollama**, **embed**, **vector**, **llm**, **api**. Containers share the task network namespace and talk over `127.0.0.1` (same idea as Docker Compose on one host). A public **Application Load Balancer** forwards HTTP **:80** to the **api** container on **:8000**.

**Costs:** Defaults are **8 vCPU / 60 GiB** to reduce **Fargate vCPU quota** issues and cost. The ECS service uses **`minimum_healthy_percent = 0`** so a deploy does **not** run two tasks at once (which would need **2×** your task vCPU). **Ollama** runs a small shell: **`serve` in background → wait for API → `ollama pull` (same disk as serve)** → `wait` on serve. A separate pull-only container cannot share `/root/.ollama` without a mounted volume, which caused **404 model not found**. Tear down when finished: `terraform destroy`.

**Required order:** `terraform apply` **then** `./deploy/aws/build-and-push.sh` with the **same** `AWS_REGION`. If you skip the push, tasks fail with **`CannotPullContainerError` / `not found`** on `rag-qa-embed` (or other ECR repos).

**Changing only `.tf` (e.g. ALB idle timeout)** updates AWS resources but **does not** replace running tasks. To roll out **new container images** after editing `services/*`, run **`./deploy/aws/build-and-push.sh`** (or push only changed services) then:

`aws ecs update-service --cluster rag-qa-cluster --service rag-qa-svc --force-new-deployment --region us-west-2`

## Prerequisites

- AWS account, IAM user/role with permissions for VPC, EC2 (SG/Subnets), ELB, ECS, ECR, IAM, CloudWatch Logs.
- [Terraform](https://www.terraform.io/) `>= 1.5`, [AWS CLI](https://aws.amazon.com/cli/) v2, [Docker](https://docs.docker.com/get-docker/).
- On **Apple Silicon**, use Docker `--platform linux/amd64` (the script defaults to this) so images match Fargate **x86_64**.

## 1) Create infrastructure

```bash
cd rag_project/terraform
terraform init
terraform plan
terraform apply
```

On first apply, ECS may fail to pull custom images until you push to ECR (step 2). That is expected; after pushing, the service stabilizes or run `apply` again.

## 2) Build and push images

From `rag_project/` (not `terraform/`):

```bash
chmod +x deploy/aws/build-and-push.sh
./deploy/aws/build-and-push.sh
```

Set `IMAGE_TAG` to match `var.image_tag` if you change it in Terraform (default `latest`).

## 3) Use the API

```bash
cd terraform
ALB=$(terraform output -raw alb_url)
curl -s "$ALB/health" | python3 -m json.tool
curl -s -F "file=@sample.md" "$ALB/documents"
curl -s -X POST "$ALB/query" -H "Content-Type: application/json" \
  -d '{"question":"What is this document about?"}' | python3 -m json.tool
```

**Cold start:** **embed** loads sentence-transformers after **ollama** is healthy; the first **`/query`** may take a long time while Ollama downloads the model (watch **ollama** logs). ALB **`/health`** only hits **api** and does not wait for the LLM.

**Empty `/query` + `json.tool` error:** The ALB default idle timeout is **60s**. This stack sets **`alb_idle_timeout_seconds`** (default **900**) so long `/query` responses are not cut off. After changing it, run **`terraform apply`**.

**Exit 1 (ollama) + 137 (vector) after containers start:** This is **not** “forgot to push images” (that shows **CannotPullContainerError** before containers run). It is usually **Ollama startup** or **RAM**: `embed` was allowed to start while `ollama pull` was still running. The Ollama **health check** waits until **`ollama show <model>`** succeeds so **embed/llm** start only after the model is on disk. **ECS caps** container **`healthCheck.startPeriod` at 300s**; higher values make **`RegisterTaskDefinition`** fail—this repo uses **300**.

## 4) Logs

CloudWatch log group: `/ecs/<project_name>` (see `logs.tf`). Streams are prefixed by container name (`api`, `embed`, `qdrant`, …).

## 5) Destroy

```bash
cd rag_project/terraform
terraform destroy
```

If `destroy` fails with **RepositoryNotEmptyException**, ECR repos still have images. This project sets **`force_delete = true`** on repositories so a second `terraform destroy` removes repos and their images. After a **partial** destroy, run `terraform apply` (same `aws_region` as that stack) to refresh ECR settings, then `terraform destroy` again—or delete images manually in the ECR console.

## Variables

See `variables.tf` and `terraform.tfvars.example`. Important:

| Variable | Notes |
|----------|--------|
| `task_cpu` / `task_memory` | Must be a valid Fargate pair. If tasks stop with **exit code 137**, increase memory (and often CPU). |
| `task_ephemeral_storage_gib` | Large embedding images may need the default 50 GiB or more. |
| `allowed_ingress_cidr` | Restrict ALB access to your IP in production-ish demos. |

### Tasks stop immediately; containers exit **137** or “essential containers exited”

That almost always means **out-of-memory** inside the single Fargate task: **embed** and **ollama** are heavy. Bump resources, then redeploy:

```bash
cd rag_project/terraform
# Defaults are already 8192 CPU / 49152 MiB in variables.tf — or set in terraform.tfvars
terraform apply
cd .. && ./deploy/aws/build-and-push.sh   # optional if only Terraform changed; apply alone updates task size
```

After `terraform apply`, ECS starts a new task definition revision; you may run `aws ecs update-service ... --force-new-deployment` if the service does not roll automatically.

## Differences from local Docker Compose

- **Ollama** runs **inside** the task (not on the Mac host); the **llm** service calls `http://127.0.0.1:11434`.
- **Qdrant** data is **ephemeral** (task storage). Replacing the task clears the vector index unless you add EFS (not included here).
- **Benchmark reset** on the vector service is disabled (`ALLOW_BENCH_RESET=0`).
