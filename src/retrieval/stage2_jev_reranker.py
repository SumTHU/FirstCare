# -*- coding: utf-8 -*-
"""Task 4.2：Stage 2 Pointwise Jev 精排器（Retrieve-then-Rerank 第二阶段）。

对每个 (患者完整口语咨询 q, 候选病例 d_i) 独立评估该病例的临床鉴别要件
是否直接、特异地支撑患者的核心症候群与风险特征，输出连续相关性分数
score_Jev(q, d_i) ∈ [0, 1]；并发请求，(hash(q), cmaid) 结果持久化至
data/cache/jev_cache.sqlite，按得分降序返回 Top-k（默认 k=10）。

打分链（优先级从高到低）：
1. 真实 Jev（TypeSafe AI System One 判别模型，jev-latest）：Noul 原语
   一次判别 → 0-1 校准概率（70-500ms，无文本生成幻觉），需 .env 配置
   JEV_API_KEY / TYPESAFE_API_KEY（console.typesafe.ai/keys 创建）；
2. 并行智算云 LLM 评分（未配置 Jev Key 时的神经判别替代）；
3. 词面覆盖率兜底（完全离线）。

用法：
    python -m src.retrieval.stage2_jev_reranker --query "你好，帮我看看……"
    python -m src.retrieval.stage2_jev_reranker --query "..." --candidates-cmaid 1346400 1673770
"""

import argparse
import hashlib
import json
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

from src.config import CACHE_DIR, PARATERA_BASE_URL, STRUCTURED_CASES_DIR, get_env, load_env
from src.data_pipeline.proposition_generator import _strip_english, load_category_index
from src.retrieval.stage1_retriever import load_index

DB_PATH = CACHE_DIR / "jev_cache.sqlite"
TOP_K_DEFAULT = 10

JEV_SYSTEM_PROMPT = """你是医疗导诊检索的相关性判别专家。给定一位患者向 AI 健康助手发出的口语化咨询，以及一份候选临床病例的摘要，请判断：该病例的临床鉴别要件（症状组合、关键体征、病程特点、既往史、鉴别诊断逻辑）是否直接且特异地支撑患者的核心症候群与风险特征——而不仅仅是表面症状相似。
评分标准（输出 0-1 连续分数，保留两位小数）：
- 0.90-1.00：鉴别要件几乎完全吻合，该病例就是患者情形的强对应（如同一疾病方向且关键特征一致）；
- 0.70-0.89：多数鉴别要件直接支撑，个别要素不符；
- 0.40-0.69：部分表面症状相似，但深层鉴别要件支撑不足（近似干扰项）；
- 0.10-0.39：仅有零星弱关联；
- 0.00-0.09：症候群方向完全不符。
只依据给定材料判断，不要编造。全部使用简体中文。"""

_JEV_SPEC = """{"score": 0到1的连续分数, "reason_zh": "30字以内的判分理由"}"""


def _ensure_db() -> sqlite3.Connection:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS jev_scores (
        q_hash TEXT NOT NULL, cmaid TEXT NOT NULL, score REAL NOT NULL,
        model TEXT, reason_zh TEXT, created_at TEXT,
        PRIMARY KEY (q_hash, cmaid))""")
    return conn


def _q_hash(q: str) -> str:
    return hashlib.sha256(q.strip().encode("utf-8")).hexdigest()


def _load_doc_text(cmaid: str, limit: int = 1200) -> str:
    """候选病例文档视图（与 Stage 1 语料一致 + 鉴别诊断前置）。"""
    p = STRUCTURED_CASES_DIR / f"case_{cmaid}_structured.json"
    if not p.exists():
        return ""
    case = json.loads(p.read_text(encoding="utf-8"))
    cr = case.get("clinical_record", {})
    parts = [f"标题：{case.get('title', '')}",
             "[主诉与现病史] " + re.sub(r"\s+", " ", str(cr.get("patient_general_information", "")))[:600],
             "[鉴别诊断] " + re.sub(r"\s+", " ", str(cr.get("diagnosis_and_differential", "")))[:600]]
    return "\n".join(parts)[:limit * 2]


def _score_via_jev(q: str, doc_text: str) -> Tuple[Optional[float], str]:
    """真实 Jev 打分：Noul 原语一次判别 → 0-1 校准概率。"""
    from src.verification.jev_client import jev_score
    state = (f"【患者咨询（口语）】\n{q}\n\n【候选病例】\n{doc_text}")
    score = jev_score(
        state,
        instructions=("该候选病例的临床鉴别要件（症状组合、关键体征、病程特点、既往史、"
                      "鉴别诊断逻辑）是否直接且特异地支撑患者的核心症候群与风险特征"
                      "——而不仅仅是表面症状相似。完全不符为假，强对应为真。"))
    if score is None:
        return None, "Jev 调用失败或未配置 Key"
    return round(score, 4), "Jev Noul 校准概率"


def _score_via_api(client, model: str, q: str, doc_text: str) -> Tuple[Optional[float], str]:
    """单对 (q, d) 的 Pointwise Jev 打分。返回 (score|None, reason)。"""
    user_msg = (f"【患者咨询】\n{q}\n\n【候选病例】\n{doc_text}\n\n"
                f"请输出符合以下 JSON 结构的对象（不要输出其他内容）：\n{_JEV_SPEC}")
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": JEV_SYSTEM_PROMPT},
                      {"role": "user", "content": user_msg}],
            temperature=0.1)
        raw = resp.choices[0].message.content or ""
    except Exception as exc:  # noqa: BLE001
        return None, f"API 调用失败: {exc}"
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S)
    m = re.search(r"\{.*\}", raw, flags=re.S)
    if not m:
        return None, "返回中未找到 JSON 对象"
    try:
        data = json.loads(m.group(0))
        score = max(0.0, min(1.0, float(data.get("score", -1))))
        return score, _strip_english(str(data.get("reason_zh", "")))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None, "JSON 解析失败"


def _score_lexical_fallback(q: str, doc_text: str) -> Tuple[float, str]:
    """离线兜底：字符 bigram 覆盖率近似（仅无 API 时使用）。"""
    import string
    stop = set(string.punctuation + "，。、；：？！（） ")
    qg = {q[i:i + 2] for i in range(len(q) - 1)} - stop
    dg = {doc_text[i:i + 2] for i in range(len(doc_text) - 1)} - stop
    if not qg:
        return 0.0, "空查询"
    score = round(min(1.0, len(qg & dg) / len(qg)), 4)
    return score, "词面覆盖率兜底打分"


def rerank(q: str, candidates: List[str], workers: int = 8,
           use_api: bool = True, model: Optional[str] = None) -> List[Dict[str, Any]]:
    """对 Stage 1 候选做 Pointwise Jev 精排，返回按分降序的记录列表。"""
    env = load_env()
    model = model or get_env("PARATERA_JEV_MODEL") or get_env("PARATERA_DECOMPOSE_MODEL", "")
    qh = _q_hash(q)
    conn = _ensure_db()
    cached = {c: (s, r) for c, s, r in conn.execute(
        "SELECT cmaid, score, reason_zh FROM jev_scores WHERE q_hash=?", (qh,))}

    missing = [c for c in candidates if c not in cached]
    if missing and use_api:
        docs = {c: _load_doc_text(c) for c in missing}
        use_jev = False
        try:
            from src.verification.jev_client import jev_available
            use_jev = jev_available()
        except Exception:
            use_jev = False
        if not use_jev:  # 未配置 Jev Key → 智算云 LLM 评分链路
            import httpx
            from openai import OpenAI
            http = httpx.Client(trust_env=False, timeout=180)
            client = OpenAI(base_url=env.get("PARATERA_BASE_URL_OVERRIDE") or PARATERA_BASE_URL,
                            api_key=get_env("PARATERA_API_KEY", ""), timeout=180,
                            max_retries=1, http_client=http)
            client_ref, model_name = client, model
        else:
            client_ref, model_name = None, "jev-latest"

        def work(cmaid: str) -> Tuple[str, Optional[float], str]:
            if use_jev:
                return cmaid, *_score_via_jev(q, docs[cmaid])
            return cmaid, *_score_via_api(client_ref, model_name, q, docs[cmaid])

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(work, c) for c in missing]
            for fut in as_completed(futs):
                cmaid, s, reason = fut.result()
                if s is None:
                    s, reason = _score_lexical_fallback(q, docs[cmaid])
                cached[cmaid] = (s, reason)
                conn.execute("INSERT OR REPLACE INTO jev_scores VALUES (?,?,?,?,?,?)",
                             (qh, cmaid, s, model, reason, time.strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
    conn.close()

    out = [{"cmaid": c, "score": cached[c][0], "reason_zh": cached[c][1]}
           for c in candidates if c in cached]
    out.sort(key=lambda x: -x["score"])
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Task 4.2 Stage 2 Pointwise Jev 精排器")
    parser.add_argument("--query", required=True, help="患者口语化咨询")
    parser.add_argument("--stage1-k", type=int, default=50, help="Stage 1 召回数 K")
    parser.add_argument("--top-k", type=int, default=TOP_K_DEFAULT, help="最终返回数 k")
    parser.add_argument("--candidates-cmaid", nargs="*", default=None,
                        help="跳过 Stage 1，直接指定候选病例")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--no-api", action="store_true", help="使用词面覆盖率兜底打分")
    args = parser.parse_args()

    cat_index = load_category_index()
    if args.candidates_cmaid:
        candidates = args.candidates_cmaid
        stage1_scores: Dict[str, float] = {}
    else:
        r = load_index()
        t0 = time.time()
        cand = r.query(args.query, top_k=args.stage1_k)
        print(f"Stage 1 召回 {len(cand)} 例（{(time.time() - t0) * 1000:.0f}ms）")
        candidates = [c for c, _ in cand]
        stage1_scores = dict(cand)

    t0 = time.time()
    ranked = rerank(args.query, candidates, workers=args.workers, use_api=not args.no_api)
    print(f"Stage 2 精排 {len(candidates)} 例（{time.time() - t0:.1f}s）| Top-{args.top_k}：")
    for item in ranked[:args.top_k]:
        s1 = f" stage1={stage1_scores[item['cmaid']]:.4f}" if item["cmaid"] in stage1_scores else ""
        print(f"  {item['cmaid']}  Jev={item['score']:.2f}{s1} "
              f"[{cat_index.get(item['cmaid'], '')}] {item['reason_zh']}")


if __name__ == "__main__":
    main()
