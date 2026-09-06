import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any

from langchain_mcp_adapters.client import MultiServerMCPClient
from app.tools.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


# ==========================================
# 0. 安全的类型转换辅助函数 (防止崩溃)
# ==========================================
def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value not in ("", None) else default
    except (ValueError, TypeError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value) if value not in ("", None) else default
    except (ValueError, TypeError):
        return default


# ==========================================
# 1. MCP 客户端工厂 (解决频繁启动 npx 的性能问题)
# ==========================================

def get_mcp_client():
    """
    返回 MCP 客户端实例。
    已修复：使用真实存在于 npm 仓库中的高德官方/社区维护包。
    """
    return MultiServerMCPClient({
        "amap": {
            "transport": "stdio",  # 必须显式声明
            "command": "npx",
            # ✅ 核心修复：更换为可用的 npm 包名
            "args": ["-y", "@amap/amap-maps-mcp-server"],
            "env": {
                "AMAP_MAPS_API_KEY": settings.amap_api_key
            }
        }
    })


