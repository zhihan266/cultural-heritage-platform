#!/usr/bin/env bash
set -euo pipefail
# Activate the separately installed vllm Conda environment before calling this file.
MODEL_DIR="${EMBEDDING_MODEL_DIR:-${PROJECT_MODELS_DIR:-$HOME/models}/Qwen3-Embedding-0.6B}"
[[ -f "$MODEL_DIR/config.json" ]] || { echo "Embedding model is missing: set PROJECT_MODELS_DIR or EMBEDDING_MODEL_DIR" >&2; exit 1; }
export VLLM_WSL2_ENABLE_PIN_MEMORY="${VLLM_WSL2_ENABLE_PIN_MEMORY:-1}"
exec vllm serve "$MODEL_DIR" \
  --served-model-name "${EMBEDDING_MODEL:-qwen3-embedding}" \
  --runner pooling --host "${MODEL_HOST:-127.0.0.1}" --port "${EMBEDDING_PORT:-8001}" \
  --max-model-len "${EMBEDDING_MAX_MODEL_LEN:-8192}" \
  --gpu-memory-utilization "${EMBEDDING_GPU_MEMORY_UTILIZATION:-0.35}" "$@"
