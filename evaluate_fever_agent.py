"""遗留冒烟测试（legacy smoke test）。

注意：
1. 本脚本针对的是早期内联演示版 `app.py`（已废弃，未接真实管线），
   不代表 `src/` 真实管线的评测结果；
2. 测试集仅为 3 条手工构造用例（基于标杆病例 cmaid/1673770），
   输出仅用于开发期 sanity check，样本量极小，不得作为基准结果引用；
3. 真实管线的端到端评测见 `experiments/evaluate_pipeline.py`
   （30 条分层抽样，结果见 `out/table2_pipeline_eval.tex`）。
"""
import json
import os
from typing import Any, Dict, List

import sys
import types

mock_st = types.ModuleType("streamlit")
mock_st.cache_data = lambda f: f
sys.modules["streamlit"] = mock_st

import app


COLLOQUIAL_TEST_SET: List[Dict[str, Any]] = [
    {
        "test_id": "CMCR_1673770_FULL",
        "colloquial_query": "大夫，我前天吃了冰葡萄后发高烧到40度，寒战、肚子胀还吐了两天；我以前有成人Still病一直在吃泼尼松，今天觉得明显气短、测血氧只有92%，腋下摸到肿块、身上起红疹，吃消炎药完全没退烧。",
        "gold_cmaid": "1673770",
        "expected_supported_ids": ["F1", "F2", "F3", "F4", "F5"],
        "expected_gap_ids": [],
        "expected_absent_ids": [],
        "trap_type": "生冷饮食诱因误导(胃肠炎) + 既往AOSD锚定误导(风湿复发)"
    },
    {
        "test_id": "CMCR_1673770_PARTIAL_GAP",
        "colloquial_query": "我前天吃了冰葡萄后发高烧到39.8度、伴寒战呕吐，我有成人Still病史在吃泼尼松；目前不气短、血氧正常98%，但还没注意腋下有没有淋巴结肿大。",
        "gold_cmaid": "1673770",
        "expected_supported_ids": ["F1", "F2"],
        "expected_gap_ids": ["F3", "F4"],
        "expected_absent_ids": ["F5"],
        "trap_type": "部分事实缺失(F5 Absent) + 明确否定低氧(F4 Gap) + 提及但未检查淋巴结(F3 Gap)"
    },
    {
        "test_id": "CMCR_GI_DISTRACTOR",
        "colloquial_query": "我昨天吃了冰葡萄后发烧38.4度、恶心呕吐拉肚子，但我既往体健无风湿病史，无气短、血氧正常98%、无淋巴结肿大、无皮疹。",
        "gold_cmaid": "CMCR_GI_ACUTE",
        "expected_supported_ids": ["G1", "G2"],
        "expected_gap_ids": [],
        "expected_absent_ids": [],
        "trap_type": "真急性胃肠炎病例(测试Jev重排与事实核验能否区分1673770与普通胃肠炎)"
    }
]


def run_ablation_experiment() -> Dict[str, Any]:
    total_props_checked = 0
    correct_role_assignments = 0
    correct_top1_rerank = 0

    for item in COLLOQUIAL_TEST_SET:
        ranked = app.retrieve_and_jev_rerank(item["colloquial_query"], app.CMCR_CORPUS, tau_f=0.7)
        top1 = ranked[0]
        if top1["case"]["cmaid"] == item["gold_cmaid"]:
            correct_top1_rerank += 1

        for vp in top1["verified_props"]:
            total_props_checked += 1
            pid = vp["prop_id"]
            role = vp["role"]
            if (
                (role == "supported" and pid in item["expected_supported_ids"])
                or (role == "gap" and pid in item["expected_gap_ids"])
                or (role == "absent" and pid in item["expected_absent_ids"])
            ):
                correct_role_assignments += 1

    jev_role_acc = round((correct_role_assignments / max(total_props_checked, 1)) * 100.0, 1)
    top1_rerank_acc = round((correct_top1_rerank / len(COLLOQUIAL_TEST_SET)) * 100.0, 1)

    return {
        "top1_jev_rerank_accuracy": top1_rerank_acc,
        "jev_fact_role_accuracy": jev_role_acc,
    }


if __name__ == "__main__":
    res = run_ablation_experiment()
    print("=" * 78)
    print("Legacy smoke test (deprecated app.py, 3 hand-crafted toy cases)")
    print("NOT a benchmark -- for development sanity check only.")
    print("=" * 78)
    print(f"Pointwise Jev Reranking Top-1 Accuracy : {res['top1_jev_rerank_accuracy']}%")
    print(f"Two-Question Jev Fact Role Accuracy    : {res['jev_fact_role_accuracy']}% (supported / gap / absent)")
    print("=" * 78)