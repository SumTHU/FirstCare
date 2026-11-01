# -*- coding: utf-8 -*-
"""MedIR-CMCR 全局配置与路径约定（Task 1.2 工程环境规范）。"""

import os
from pathlib import Path
from typing import Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
RAW_CMCR_DIR = DATA_DIR / "raw_cmcr"
STRUCTURED_CASES_DIR = DATA_DIR / "structured_cases"
COLLOQUIAL_DIR = DATA_DIR / "cmcr_colloquial"
CACHE_DIR = DATA_DIR / "cache"
ENV_PATH = PROJECT_ROOT / ".env"


def load_env(path: Path = ENV_PATH) -> Dict[str, str]:
    """极简 .env 解析器（KEY="value" 或 KEY=value），支持 # 注释。"""
    env: Dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def get_env(key: str, default: Optional[str] = None) -> Optional[str]:
    """优先读进程环境变量，其次读项目 .env。"""
    val = os.environ.get(key)
    if val is not None:
        return val
    return load_env().get(key, default)


# 并行智算云 MaaS（生成/拆解/评测模型）
PARATERA_BASE_URL = "https://llmapi.paratera.com/v1"

# Jev（TypeSafe AI System One 判别模型）：事实核验与相关性精排
# 真实端点为 /v1/systemone（state + questions 并行原语，Noul 返回 0-1 校准概率）
# 官方直连: https://api.typesafe.ai/v1/systemone
# DMXAPI 聚合通道（国内直连、人民币计价）: https://www.dmxapi.cn/typesafe/v1/systemone
JEV_BASE_URL = get_env("JEV_BASE_URL", "https://api.typesafe.ai/v1/systemone")
JEV_MODEL_DEFAULT = "jev-latest"  # DMXAPI 上指向最新版本（如 jev-1.14.0）
JEV_CONFIRMATION_THRESHOLD = float(get_env("JEV_CONFIRMATION_THRESHOLD", "0.70"))


def jev_api_key() -> Optional[str]:
    """Jev API Key：环境变量 TYPESAFE_API_KEY / JEV_API_KEY 或 .env。"""
    return get_env("TYPESAFE_API_KEY") or get_env("JEV_API_KEY")
