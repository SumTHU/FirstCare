# -*- coding: utf-8 -*-
"""Jev 客户端（TypeSafe AI System One 判别模型）共享接入层。

Jev 是判别型模型：一次请求携带 state（患者陈述/病例文本）与多个类型化
questions（原语），并行独立评估，返回类型安全的校准概率——不做文本生成、
无类型错误。本项目使用两种原语：

* Noul：陈述真伪判断，返回 0-1 概率 → Two-Question 核验（Task 5）
        与 Pointwise 相关性打分（Task 4.2）
* Score：量规打分（备用）

调用通道：
* DMXAPI 聚合（国内直连，推荐）：.env 设 JEV_BASE_URL="https://www.dmxapi.cn/typesafe/v1/systemone"，
  JEV_API_KEY 填 DMXAPI 的 Key（sk-...）；协议与官方逐字一致（state+questions →
  answers.noul/score/choice + confidence + probabilities），模型 jev-latest 指向最新版本
* TypeSafe 官方直连：JEV_BASE_URL="https://api.typesafe.ai/v1/systemone"，
  Key 来自 console.typesafe.ai/keys；官方 Python SDK（typesafe-sdk）可用时优先
任一失败由调用方回落确定性规则模式。

用法：
    from src.verification.jev_client import jev_available, jev_noul_batch
    if jev_available():
        probs = jev_noul_batch(state, {"p_men": "患者陈述提及该要素...",
                                       "p_sup": "患者陈述肯定该事实..."})
        # → {"p_men": 0.87, "p_sup": 0.32}（校准概率）
"""

import json
from typing import Dict, Optional

from src.config import JEV_BASE_URL, JEV_MODEL_DEFAULT, get_env, jev_api_key


def jev_available() -> bool:
    """配置了 API Key 即视为可用（early access Key 来自 console.typesafe.ai/keys）。"""
    return bool(jev_api_key())


def _post_direct(state: str, questions: Dict[str, Dict], model: str) -> Dict:
    """HTTP 直连 /v1/systemone（无 SDK 依赖的等价实现）。"""
    import httpx
    key = jev_api_key()
    payload = {"state": state, "model": model, "questions": questions}
    resp = httpx.post(
        JEV_BASE_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload, timeout=30.0, trust_env=False)
    resp.raise_for_status()
    return resp.json()


def _post_sdk(state: str, questions: Dict[str, Dict], model: str) -> Dict:
    """官方 SDK 调用（typesafe-sdk 安装时优先）。"""
    from typesafe_sdk import TypeSafeClient  # noqa: import 出错由上层捕获
    qmap = {"noul": _sdk_noul, "score": _sdk_score, "choice": _sdk_choice}
    with TypeSafeClient(model=model) as client:  # SDK 内部读 TYPESAFE_API_KEY
        response = client.system_one(
            state=state,
            questions={k: qmap[v["type"]](v) for k, v in questions.items()})
    return response.model_dump() if hasattr(response, "model_dump") else dict(response)


def _sdk_noul(spec: Dict):
    from typesafe_sdk import Noul
    return Noul(instructions=spec["instructions"])


def _sdk_score(spec: Dict):
    from typesafe_sdk import Score
    return Score(instructions=spec["instructions"], criteria=spec["criteria"])


def _sdk_choice(spec: Dict):
    from typesafe_sdk import Choice
    return Choice(instructions=spec["instructions"], criteria=spec["criteria"])


def jev_query(state: str, questions: Dict[str, Dict],
              model: Optional[str] = None) -> Optional[Dict[str, Dict]]:
    """一次 Jev 请求并行评估多个问题。

    questions: {qid: {"type": "noul"|"score"|"choice", ...原语参数}}
    返回: {qid: 原语答案 dict}（noul → {"noul": 0-1}），失败返回 None。
    """
    if not jev_available():
        return None
    model = model or get_env("JEV_MODEL", JEV_MODEL_DEFAULT) or JEV_MODEL_DEFAULT
    try:
        try:  # 官方直连且安装了 SDK 时优先走 SDK；DMXAPI 等第三方通道走 HTTP
            if "api.typesafe.ai" in JEV_BASE_URL:
                data = _post_sdk(state, questions, model)
            else:
                data = _post_direct(state, questions, model)
        except ImportError:
            data = _post_direct(state, questions, model)
        answers = data.get("answers", {})
        return answers or None
    except Exception:
        return None


def jev_noul_batch(state: str, noul_questions: Dict[str, str],
                   model: Optional[str] = None) -> Optional[Dict[str, float]]:
    """便捷封装：批量 Noul 问题（同 state 并行评估），返回 {qid: 0-1 概率}。"""
    questions = {qid: {"type": "noul", "instructions": ins}
                 for qid, ins in noul_questions.items()}
    answers = jev_query(state, questions, model=model)
    if answers is None:
        return None
    out: Dict[str, float] = {}
    for qid, ans in answers.items():
        try:
            out[qid] = float(ans["noul"])
        except (KeyError, TypeError, ValueError):
            return None
    return out


def jev_score(state: str, instructions: str, model: Optional[str] = None) -> Optional[float]:
    """便捷封装：单条 Noul 判别 → 0-1 概率（Pointwise 精排用）。"""
    probs = jev_noul_batch(state, {"q": instructions}, model=model)
    return None if probs is None else probs.get("q")
