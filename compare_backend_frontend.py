# -*- coding: utf-8 -*-
"""前后端对拍校验：用后端真实管线（Stage-1 混合召回 → Stage-2 词面兜底精排 →
Task-5 确定性双问题核验）处理同一批查询，输出 Top-2 与三态计数，
与前端 JS 冒烟测试输出逐位比对。"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.retrieval.stage1_retriever import load_index
from src.retrieval.stage2_jev_reranker import _load_doc_text, _score_lexical_fallback
from src.schemas import ClinicalProposition
from src.verification.jev_fact_verifier import verify_query, filter_facts_for_generator
from src.generation.deductive_generator import (generate_deductive_answer, load_case,
                                                select_props)
from build_frontend_real import PROP_CAP as CORPUS_PROP_CAP   # 与前端语料 cap 保持一致

QUERIES = [
    "大夫，我前天吃了冰葡萄后开始发高烧到40度，伴寒战、肚子胀还吐了两天；我以前确诊过成人斯蒂尔病，一直在吃泼尼松。",
    "我前天吃冰葡萄后高烧40度伴呕吐，有成人斯蒂尔病史；今天觉得明显气短、测血氧只有92%，而且腋窝摸到肿大淋巴结、身上起了散在红色皮疹，吃消炎药抗感染无效。",
    "我昨天吃冰葡萄后发烧38.3度、恶心呕吐拉肚子，但我既往体健无风湿病史，而且无气短、血氧正常98%、无淋巴结肿大、无皮疹。",
    "我昨天开始发烧到39.2度，浑身肌肉酸痛、头疼嗓子疼，还有点干咳。",
]

idx = load_index()
for q in QUERIES:
    cand = idx.query(q, top_k=50)
    scored = []
    for cmaid, _s1 in cand:
        doc = _load_doc_text(cmaid)
        s, _reason = _score_lexical_fallback(q, doc)
        scored.append((cmaid, s))
    scored.sort(key=lambda x: -x[1])
    top2 = [c for c, _ in scored[:2]]
    cases = [load_case(c) for c in top2]
    props = []
    for c in cases:
        # select_props：红旗命题全量入选（cap 与前端语料构建一致）
        props.extend(ClinicalProposition(**p) for p in select_props(c, CORPUS_PROP_CAP))
    facts = verify_query(q, props, use_api=False)
    gated = filter_facts_for_generator(facts)
    ans = generate_deductive_answer(q, cases, gated, use_llm=False, all_facts=facts)
    sup = sum(1 for f in facts if f.role == "supported")
    gap = sum(1 for f in facts if f.role == "gap")
    absent = sum(1 for f in facts if f.role == "absent")
    print("---- q:", q[:22] + "…")
    print("  top2:", ",".join(top2))
    print("  三态 sup/gap/abs:", f"{sup}/{gap}/{absent}")
    print("  urgency:", ans.urgency, "| route:", ans.triage_route)
    print("  cited:", ",".join(ans.cited_cmaids))
    print("  posthoc:", ans.posthoc_scrubbed, "| rescued:", ans.red_flag_rescued_n)
