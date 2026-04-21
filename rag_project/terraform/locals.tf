locals {
  name_prefix = var.project_name

  # Same network namespace on Fargate: all containers use 127.0.0.1 for inter-service URLs.
  qdrant_url = "http://127.0.0.1:6333"
  embed_url  = "http://127.0.0.1:8001"
  vector_url = "http://127.0.0.1:8002"
  llm_url    = "http://127.0.0.1:8003"

  # Ollama container listens on 11434; llm service calls it via localhost (shared task network namespace).
  ollama_url_for_llm = "http://127.0.0.1:11434"

  # Same filesystem as `ollama serve` — required for the model to exist (a separate sidecar cannot share /root/.ollama without EFS).
  # No `set -e`: a failed pull must not skip `wait` and exit the whole task with code 1.
  ollama_entrypoint = <<-SHELL
ollama serve &
PID=$!
i=0
while [ $i -lt 180 ]; do
  ollama list >/dev/null 2>&1 && break
  i=$((i+1))
  sleep 2
done
ollama pull $OLLAMA_MODEL || echo "ollama: pull failed (network?); llm retries may still work later"
wait $PID
SHELL
}
