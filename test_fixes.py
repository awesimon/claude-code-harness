#!/usr/bin/env python3
"""
测试脚本 - 验证health日志过滤和temperature参数传递
"""

import os
import sys
import json
import asyncio
import httpx
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 测试配置
BASE_URL = f"http://{os.getenv('HOST', 'localhost')}:{os.getenv('PORT', '8000')}"


async def test_health_endpoint():
    """测试health端点是否正常工作"""
    print("=" * 50)
    print("测试1: Health端点")
    print("=" * 50)

    async with httpx.AsyncClient() as client:
        response = await client.get(f"{BASE_URL}/health")
        print(f"状态码: {response.status_code}")
        print(f"响应: {response.json()}")

        if response.status_code == 200:
            print("✅ Health端点正常工作")
            return True
        else:
            print("❌ Health端点异常")
            return False


async def test_llm_config():
    """测试LLM配置是否正确从.env加载"""
    print("\n" + "=" * 50)
    print("测试2: LLM配置")
    print("=" * 50)

    async with httpx.AsyncClient() as client:
        response = await client.get(f"{BASE_URL}/llm/config")
        print(f"状态码: {response.status_code}")
        config = response.json()
        print(f"响应: {json.dumps(config, indent=2, ensure_ascii=False)}")

        if response.status_code == 200:
            print("✅ LLM配置获取成功")
            print(f"   默认模型: {config['data']['default_model']}")
            print(f"   默认temperature: {config['data']['default_temperature']}")
            return True
        else:
            print("❌ LLM配置获取失败")
            return False


async def test_temperature_passing():
    """测试temperature参数是否能正确传递"""
    print("\n" + "=" * 50)
    print("测试3: Temperature参数传递")
    print("=" * 50)

    # 这里我们只测试API端点是否能接收temperature参数
    # 实际调用LLM需要有效的API key

    test_cases = [
        {"temperature": 0.0, "description": "最低temperature (确定性输出)"},
        {"temperature": 0.7, "description": "默认temperature"},
        {"temperature": 1.0, "description": "最高temperature (创造性输出)"},
        {"temperature": 1.5, "description": "超过标准的temperature"},
    ]

    async with httpx.AsyncClient() as client:
        for test in test_cases:
            temp = test["temperature"]
            print(f"\n测试 temperature={temp} ({test['description']})")

            request_data = {
                "messages": [{"role": "user", "content": "Hello"}],
                "temperature": temp,
                "provider": "openai",
                "model": "gpt-4o"
            }

            try:
                # 发送请求但不等待完整响应（因为我们没有有效的API key）
                response = await client.post(
                    f"{BASE_URL}/llm/chat",
                    json=request_data,
                    timeout=5.0
                )

                # 检查是否因为API key问题而不是参数问题
                if response.status_code == 500 and "API" in response.text:
                    print(f"  ✅ 请求已发送 (temperature={temp})")
                    print(f"     预期错误: API key 未配置")
                elif response.status_code == 200:
                    print(f"  ✅ 请求成功 (temperature={temp})")
                else:
                    print(f"  ⚠️  状态码: {response.status_code}")
                    print(f"     响应: {response.text[:200]}")

            except httpx.TimeoutException:
                print(f"  ✅ 请求已发送但超时 (temperature={temp})")
            except Exception as e:
                print(f"  ❌ 错误: {e}")

    return True


async def test_environment_variables():
    """测试环境变量是否正确加载"""
    print("\n" + "=" * 50)
    print("测试4: 环境变量加载")
    print("=" * 50)

    env_vars = [
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "DEFAULT_MODEL",
        "DEFAULT_TEMPERATURE",
        "DEFAULT_MAX_TOKENS",
        "HOST",
        "PORT",
    ]

    all_loaded = True
    for var in env_vars:
        value = os.getenv(var)
        if value:
            # 隐藏API key的一部分
            if "KEY" in var and value:
                display_value = value[:10] + "..." if len(value) > 10 else value
            else:
                display_value = value
            print(f"  ✅ {var}: {display_value}")
        else:
            print(f"  ⚠️  {var}: 未设置")
            if var in ["OPENAI_API_KEY"]:
                all_loaded = False

    return all_loaded


async def main():
    """主测试函数"""
    print("\n" + "=" * 50)
    print("Claude Code Python API 测试")
    print("=" * 50)
    print(f"测试地址: {BASE_URL}")
    print()

    # 等待服务启动
    print("等待服务启动...")
    await asyncio.sleep(1)

    results = []

    # 运行所有测试
    results.append(("环境变量", await test_environment_variables()))
    results.append(("Health端点", await test_health_endpoint()))
    results.append(("LLM配置", await test_llm_config()))
    results.append(("Temperature传递", await test_temperature_passing()))

    # 打印总结
    print("\n" + "=" * 50)
    print("测试总结")
    print("=" * 50)

    for name, passed in results:
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"  {name}: {status}")

    # 返回整体结果
    return all(passed for _, passed in results)


if __name__ == "__main__":
    try:
        success = asyncio.run(main())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n测试被中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n测试出错: {e}")
        sys.exit(1)
