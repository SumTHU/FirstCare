# -*- coding: utf-8 -*-
"""预跑真实 API 全链路，生成 demo 录制所需的预设场景结果缓存。

复用后端 deductive_generator 的完整链路（Stage1 混合召回 → Stage2 真实 Jev
精排 → Task5 双问题真实 Jev 核验 → Task6 LLM 五步生成），对 5 个预设场景
（chest / fever / flu / gastro / abdomen）逐一跑通真实 API，把每个场景的
完整管线输出导出为 JSON，供注入前端作为「预设场景真实结果缓存」。

产出：data/cache/demo_preset_cache.json
结构：
{
  "generated_at": "...",
  "scenarios": {
    "chest": { "query_zh": "...", "pipeline": {...} },
    ...
  }
}
"""
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.generation.deductive_generator import (
    generate_deductive_answer,
    load_case,
    select_props,
)
from src.retrieval.stage1_retriever import load_index
from src.retrieval.stage2_jev_reranker import rerank, _load_doc_text
from src.schemas import ClinicalProposition
from src.verification.jev_fact_verifier import (
    filter_facts_for_generator,
    verify_query,
)

# 与前端 frontend_mock_template.html 的 SCENARIO_QUERIES 保持一致
SCENARIOS = {
    "chest": "胸口像被大石头压着，喘不上气，出了很多冷汗",
    "fever": "我发烧两天了，我有糖尿病13年，自己吃了退烧止痛药也没退烧",
    "flu": "我昨天开始发烧到39.2度，浑身肌肉酸痛、头疼嗓子疼，还有点干咳；家里孩子前两天先感冒的，我吃了布洛芬烧退了又升回来",
    "gastro": "我昨天在外面吃了烧烤后开始发烧38.3度、恶心呕吐拉肚子，但我既往体健，无气短、血氧正常98%、无淋巴结肿大、无皮疹",
    "abdomen": "我肚脐周围疼了一天多，现在疼到右下腹了，还有点低烧，碰到肚子会更疼",
}

STAGE1_K = 50
TOPK = 2
PROPS_CAP = 30
WORKERS = 8


def _serialize_fact(f) -> dict:
    return {
        "prop_id": f.prop_id,
        "cmaid": f.source_cmaid,
        "zh": f.proposition_zh,
        "en": f.proposition_en,
        "is_red_flag": f.is_red_flag,
        "p_men": f.p_men,
        "p_sup": f.p_sup,
        "role": f.role,
        "evidence": f.matched_evidence_span,
        "chip": f.follow_up_chip,
    }


def run_one(query: str) -> dict:
    idx = load_index()

    # Stage 1
    t0 = time.time()
    cand = idx.query(query, top_k=STAGE1_K)
    stage1_ms = (time.time() - t0) * 1000

    # Stage 2 真实 Jev 精排
    t1 = time.time()
    ranked = rerank(query, [c for c, _ in cand], workers=WORKERS, use_api=True)
    stage2_s = time.time() - t1
    top_cmaids = [r["cmaid"] for r in ranked[:TOPK]]

    # 组装 Top-k 病例 + 命题
    cases, props = [], []
    for c in top_cmaids:
        case = load_case(c)
        if case is None:
            continue
        cases.append(case)
        props.extend([ClinicalProposition(**p) for p in select_props(case, PROPS_CAP)])

    # Task 5 真实 Jev 双问题核验
    t2 = time.time()
    facts = verify_query(query, props, workers=WORKERS, use_api=True)
    task5_s = time.time() - t2
    gated = filter_facts_for_generator(facts)

    # Task 6 LLM 五步生成
    t3 = time.time()
    ans = generate_deductive_answer(query, cases, gated, use_llm=True)
    task6_s = time.time() - t3

    ranked_out = [
        {"cmaid": r["cmaid"], "score": r["score"], "reason_zh": r["reason_zh"],
         "title": load_case(r["cmaid"]).get("title", "") if load_case(r["cmaid"]) else ""}
        for r in ranked[:10]
    ]

    return {
        "query_zh": query,
        "stage1": {"k": STAGE1_K, "n": len(cand), "ms": round(stage1_ms),
                   "top10": [{"cmaid": c, "score": s} for c, s in cand[:10]]},
        "stage2": {"mode": "jev", "seconds": round(stage2_s, 1),
                   "ranked": ranked_out},
        "task5": {"mode": "jev", "seconds": round(task5_s, 1),
                  "distribution": dict(Counter(f.role for f in facts)),
                  "facts_top1": [_serialize_fact(f) for f in facts
                                 if f.source_cmaid == top_cmaids[0]],
                  "facts_top2": [_serialize_fact(f) for f in facts
                                 if len(top_cmaids) > 1 and f.source_cmaid == top_cmaids[1]]},
        "task6": {
            "mode": ans.mode,
            "seconds": round(task6_s, 1),
            "urgency": ans.urgency,
            "triage_route": ans.triage_route,
            "red_flag_triggered": ans.red_flag_triggered,
            "cited_cmaids": ans.cited_cmaids,
            "sections": ans.sections,
            "en_summary": ans.en_summary,
            "fallback_reason": ans.fallback_reason,
            "veto2_scrubbed": ans.veto2_scrubbed,
            "posthoc_scrubbed": ans.posthoc_scrubbed,
        },
    }


def _build_slim(script_scenarios: dict, query_map: dict) -> dict:
    """把完整缓存精简为可直接嵌入前端的 JS 片段结构。

    前端以「查询原文」为键命中（runPipeline 拿到的就是 q 原文），因此
    精简版的键是查询文本，与前端 SCENARIO_QUERIES 的 .zh 一一对应。
    每场景： (a) Stage-2 精排 Top-N {cmaid, score, reason}；
    (b) Task-5 事实的真实 p_men/p_sup/role（按 prop_id 关联，前端从 CORPUS
    取完整命题元数据）；(c) Task-6 五步文本/摘要/紧急度/路径/红旗/引用。
    """
    slim = {}
    for key, full in script_scenarios.items():
        qtext = query_map[key]
        slim[qtext] = {
            "ranked": [
                {"cmaid": r["cmaid"], "score": r["score"], "reason": r["reason_zh"]}
                for r in full["stage2"]["ranked"][:8]
            ],
            "facts": [
                {"prop_id": f["prop_id"], "p_men": f["p_men"], "p_sup": f["p_sup"],
                 "role": f["role"]}
                for f in (full["task5"]["facts_top1"] + full["task5"]["facts_top2"])
            ],
            "answer": {
                "mode": full["task6"]["mode"],
                "urgency": full["task6"]["urgency"],
                "route": full["task6"]["triage_route"],
                "red_flag": full["task6"]["red_flag_triggered"],
                "cited": full["task6"]["cited_cmaids"],
                "sections": full["task6"]["sections"],
                "en_summary": full["task6"]["en_summary"],
                "veto2": full["task6"]["veto2_scrubbed"],
            },
        }
    return slim


def main() -> None:
    out = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "scenarios": {}}
    for key, q in SCENARIOS.items():
        print(f"\n{'='*70}\n场景 {key}: {q}\n{'='*70}")
        out["scenarios"][key] = run_one(q)
        r = out["scenarios"][key]
        print(f"  Stage1: {r['stage1']['n']} 例 / {r['stage1']['ms']}ms")
        print(f"  Stage2: Jev 精排 Top1={r['stage2']['ranked'][0]['cmaid']} "
              f"score={r['stage2']['ranked'][0]['score']:.3f}")
        print(f"  Task5: 三态 {r['task5']['distribution']}")
        print(f"  Task6: mode={r['task6']['mode']} urgency={r['task6']['urgency']} "
              f"redflag={r['task6']['red_flag_triggered']}")

    dest = ROOT / "data" / "cache" / "demo_preset_cache.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # 精简版：供前端注入（键 = 查询原文，与前端 SCENARIO_QUERIES 的 .zh 对齐）
    slim = _build_slim(out["scenarios"], SCENARIOS)
    slim_dest = ROOT / "data" / "cache" / "demo_preset_slim.json"
    slim_dest.write_text(json.dumps(slim, ensure_ascii=False), encoding="utf-8")
    print(f"\n✅ 完整缓存已写入 {dest}")
    print(f"✅ 精简注入版已写入 {slim_dest}")


if __name__ == "__main__":
    main()
