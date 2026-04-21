# Experiment 3 config: horizontal scaling on AWS Academy / Learner Lab sandbox
# (Fargate vCPU quota = 6 by default, quota-increase requests are blocked on sandbox.)
#
# Per-task budget: 1 vCPU / 4 GiB. Supports 1 / 2 / 4 replicas within 6 vCPU quota:
#   1 replica → 1 vCPU,  2 replicas → 2 vCPU,  4 replicas → 4 vCPU
#
# If ollama (+ llama3.2:1b) OOMs inside the task during seeding or warm-up,
# bump to task_memory = 6144 or 8192 (both valid for task_cpu=1024).

task_cpu    = 1024
task_memory = 4096

# Start at 1. Bump to 2 / 4 during the experiment with:
#   terraform apply -var="desired_count=2"
#   terraform apply -var="desired_count=4"
desired_count = 1

# Keep ephemeral storage small to speed up task starts; images + llama3.2:1b fit in ~15 GiB.
task_ephemeral_storage_gib = 30
