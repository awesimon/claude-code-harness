#!/bin/bash
export ANTHROPIC_API_KEY=sk-cp-nvf5mdnanxyfacun
export ANTHROPIC_BASE_URL=https://cloud.infini-ai.com/maas/coding
export DEFAULT_MODEL=minimax-m2.7
export DEFAULT_MAX_TOKENS=4096
export DEFAULT_TEMPERATURE=0.7
export HOST=0.0.0.0
export PORT=8000

echo "启动 Claude Code Python API..."
echo "模型: $DEFAULT_MODEL"
echo "API: $ANTHROPIC_BASE_URL"
echo ""

python3 -m uvicorn main:app --host $HOST --port $PORT --reload
