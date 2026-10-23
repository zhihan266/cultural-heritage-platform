#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MODEL_DIR="${RERANKER_MODEL_DIR:-${PROJECT_MODELS_DIR:-$HOME/models}/Qwen3-Reranker-0.6B}"
TEMPLATE="${RERANKER_CHAT_TEMPLATE:-$SCRIPT_DIR/qwen3_reranker.jinja}"
[[ -f "$MODEL_DIR/config.json" && -f "$TEMPLATE" ]] || { echo "Reranker model/template missing; set PROJECT_MODELS_DIR or RERANKER_CHAT_TEMPLATE" >&2; exit 1; }
export VLLM_WSL2_ENABLE_PIN_MEMORY="${VLLM_WSL2_ENABLE_PIN_MEMORY:-1}"
exec vllm serve "$MODEL_DIR" \
  --served-model-name "${RERANKER_MODEL:-qwen3-reranker}" \
  --runner pooling --host "${MODEL_HOST:-127.0.0.1}" --port "${RERANKER_PORT:-8002}" \
  --max-model-len "${RERANKER_MAX_MODEL_LEN:-4096}" \
  --gpu-memory-utilization "${RERANKER_GPU_MEMORY_UTILIZATION:-0.35}" \
  --hf_overrides '{"architectures":["Qwen3ForSequenceClassification"],"classifier_from_token":["no","yes"],"is_original_qwen3_reranker":true}' \
  --chat-template "$TEMPLATE" "$@"
