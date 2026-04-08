#!/bin/bash

# 从.env文件加载环境变量
if [ -f .env ]; then
    echo "从.env文件加载配置..."
    # 使用source加载.env文件
    set -a
    source .env
    set +a
else
    echo "警告: .env文件不存在"
fi

# 设置默认值（如果.env中没有定义）
HOST=${HOST:-0.0.0.0}
PORT=${PORT:-8000}
DEFAULT_MODEL=${DEFAULT_MODEL:-gpt-4o}

echo "启动 Claude Code Python API..."
echo "模型: $DEFAULT_MODEL"
echo "API: $OPENAI_BASE_URL"
echo "Host: $HOST:$PORT"
echo ""

# 启动服务，禁用access log以减少health检查日志输出
python3 -m uvicorn main:app --host $HOST --port $PORT --reload --no-access-log
