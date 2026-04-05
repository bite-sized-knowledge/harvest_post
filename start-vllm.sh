#!/bin/bash
# vLLM 서버 시작 스크립트
# Ollama에서 다운로드한 GGUF 모델을 직접 사용

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/.venv/bin/activate"

GGUF_PATH="$SCRIPT_DIR/qwen3.5-9b.gguf"

echo "[vLLM] Starting server..."
echo "[vLLM] Model: qwen3.5:9b (GGUF Q4_K_M)"
echo "[vLLM] Max model len: 32768"
echo "[vLLM] GPU memory utilization: 0.55"

python3 -m vllm.entrypoints.openai.api_server \
    --model "$GGUF_PATH" \
    --tokenizer "Qwen/Qwen3.5-9B-Instruct" \
    --served-model-name "qwen3.5:9b" \
    --max-model-len 32768 \
    --gpu-memory-utilization 0.55 \
    --dtype half \
    --port 8000 \
    --host 0.0.0.0
