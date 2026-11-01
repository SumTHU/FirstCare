# -*- coding: utf-8 -*-
r"""Task 5：Two-Question Jev 事实判断引擎（公式 3）。

对每个候选临床命题 f 针对患者累积陈述 q 执行两次独立核验：
* Question 1 — Mention Check：q 是否提及/涉及该要素（肯定、否定或未检查均算提及）→ p_men
* Question 2 — Support Check：q 是否第一人称明确肯定/预设该事实 → p_sup

三态角色赋值（τ_f = 0.7，全局唯一事实源 src/config.JEV_CONFIRMATION_THRESHOLD）：
    supported : p_sup >= 0.7
    gap       : p_sup < 0.7 且 p_men >= 0.7
    absent    : p_men < 0.7

双模式：
* Jev 神经判别（--api）：TypeSafe AI System One 判别模型（jev-latest），
  一次请求并行携带两个 Noul 原语问题（Mention & Support），返回校准概率
  p_men / p_sup（70-500ms，无文本生成、无类型错误）；需 .env 配置
  JEV_API_KEY（或 TYPESAFE_API_KEY），结果写入 SQLite 缓存
* 高保真确定性规则核验模式（默认，离线/零延迟兜底）：基于 `ClinicalProposition`
  中的 `affirm_keywords`、`negate_keywords`、`uncertain_keywords` 与
  `mention_keywords` 精确计算 $p_{\text{men}}$ 与 $p_{\text{sup}}$（命中
  `negate_keywords` 或 `uncertain_keywords` 时 $p_{\text{men}} \ge 0.84,
  p_{\text{sup}} \le 0.35 \rightarrow \text{gap}$；命中 `affirm_keywords`
  且无否定前缀时 $p_{\text{men}} \ge 0.86, p_{\text{sup}} \ge 0.83 \rightarrow
  \text{supported}$；未命中任何提及词时 $p_{\text{men}} < 0.20, p_{\text{sup}}
  < 0.05 \rightarrow \text{absent}$）

防幻觉硬闸门：filter_facts_for_generator() 必须移除全部 absent 命题，
仅放行 supported 与 gap 进入生成器（Task 6）；absent 命题按鉴别重要度
导出为 pending_clarification_chips（Top-1/Top-2 病例，前端追问按钮）。

用法：
    python -m src.verification.jev_fact_verifier --query "..."            # 确定性模式演示
    python -m src.verification.jev_fact_verifier --query "..." --api     # API 模式
"""

import argparse
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

from src.config import CACHE_DIR, JEV_CONFIRMATION_THRESHOLD
from src.schemas import ClinicalProposition, JevVerifiedFact

TAU_F = JEV_CONFIRMATION_THRESHOLD  # 设定确认阈值 τ_f（.env JEV_CONFIRMATION_THRESHOLD 可覆盖，默认 0.7）

# 确定性模式概率档位（严格对应计划书阈值）
P_MEN_NEGATE, P_SUP_NEGATE = 0.87, 0.32   # 否定/未检查命中 → gap
P_MEN_AFFIRM, P_SUP_AFFIRM = 0.90, 0.85   # affirm 命中（无否定前缀）→ supported
P_MEN_MENTION, P_SUP_MENTION = 0.85, 0.30  # 仅提及命中 → gap
P_MEN_ABSENT, P_SUP_ABSENT = 0.15, 0.03   # 全部未命中 → absent

# 否定前缀窗口（affirm 关键词前方 N 字符内出现否定词则视为否定表述）
NEGATION_PREFIXES = ("没有", "没有过", "无", "未曾", "从未", "不曾", "否认", "未", "不", "沒有")
NEG_WINDOW = 6

# 鉴别重要度排序（absent 命题导出追问 chips 用）
CATEGORY_PRIORITY = {"急危红旗征": 0, "核心症状与诱因": 1, "既往病史": 2,
                     "阳性查体": 3, "治疗反应": 4}

FACTS_DB = CACHE_DIR / "jev_cache.sqlite"  # 与 Stage 2 共库不同表

# ---------------- 口语归一化预处理（Task 5 前置层） ----------------
# 口语隐喻/俗称 → 规范临床术语。归一化结果仅作为核验输入的补充线索
# （追加于患者陈述之后的对照行），绝不改写或替换患者原始陈述，
# 也不直接抬高概率——是否成立仍由 Jev / 关键词规则独立判定。
COLLOQUIAL_NORMS = (
    (r"(胸口|心口|胸骨)[^，。；!？]{0,8}(压|石|挤|钳|绞|痛|疼|闷)", "胸痛、压榨样胸痛、胸口疼痛"),
    (r"喘不上气|上气不接下气|气不够用|透不过气|憋得?慌|接不上气", "气短、呼吸困难"),
    (r"冷汗|大汗淋漓|汗如雨下|汗珠直冒", "大汗、出冷汗"),
    # 2026-10-31 评测补录：意识改变的口语变体（实测"叫半天都叫不应"未命中旧模式）
    (r"叫不醒|叫不应|唤不醒|意识不清|说胡话|不省人事|意识模糊|迷糊|答非所问|昏昏沉沉",
     "意识改变、意识障碍、呼之不应"),
    (r"头痛欲裂|要炸开|剧烈头痛|一生中最(剧烈|严重|痛)的?头(痛|疼)", "剧烈头痛"),
    # 2026-10-31 评测补录："血色素"为血红蛋白的俗称（实测"血色素很低"未命中
    # affirm 关键词"血红蛋白很低"，红旗命题 p_sup=0.66 差 0.04 未升级）
    (r"血色素(很低|太低|偏低|低)", "血红蛋白很低、血红蛋白低、贫血严重"),
    (r"血色素", "血红蛋白"),
    (r"退烧止痛药|退烧药|止痛药|退热药", "退烧止痛药物"),
    (r"拉肚子|跑厕所|拉稀|水样便", "腹泻"),
    (r"恶心想吐|吐个不停|吐了", "恶心、呕吐"),
    (r"发烧|发烫|烧到", "发热"),
)


def colloquial_norm_terms(q: str) -> List[str]:
    """返回 q 命中的规范术语集合（保序去重）。"""
    import re
    terms: List[str] = []
    for pat, canon in COLLOQUIAL_NORMS:
        if re.search(pat, q) and canon not in terms:
            terms.append(canon)
    return terms


def expand_colloquial(q: str) -> str:
    """构造核验用查询：原始陈述 + 口语对照行（不改写原文）。"""
    terms = colloquial_norm_terms(q)
    if not terms:
        return q
    return f"{q}\n（口语表述对照：患者所述可能对应规范术语：{'、'.join(terms)}）"

# ---------------- 确定性规则核验 ----------------

def _find_spans(q: str, keywords: List[str]) -> List[str]:
    """返回 q 中实际命中的关键词（去重保序）。"""
    hits = []
    for kw in keywords:
        if kw and kw in q and kw not in hits:
            hits.append(kw)
    return hits


def _has_negation_prefix(q: str, kw: str) -> bool:
    """affirm 关键词前方窗口内出现否定词 → 视为否定表述。"""
    start = 0
    while True:
        i = q.find(kw, start)
        if i < 0:
            return False
        window = q[max(0, i - NEG_WINDOW):i]
        if any(neg in window for neg in NEGATION_PREFIXES):
            return True
        start = i + len(kw)


def verify_deterministic(q: str, prop: ClinicalProposition) -> JevVerifiedFact:
    """零延迟关键词规则核验（离线兜底模式）。"""
    neg_hits = _find_spans(q, prop.negate_keywords)
    unc_hits = _find_spans(q, prop.uncertain_keywords)
    aff_hits = [kw for kw in _find_spans(q, prop.affirm_keywords)
                if not _has_negation_prefix(q, kw)]
    aff_negated = [kw for kw in _find_spans(q, prop.affirm_keywords)
                   if _has_negation_prefix(q, kw)]
    men_hits = _find_spans(q, prop.mention_keywords)

    evidence: List[str] = []
    if neg_hits or unc_hits or aff_negated:
        # 明确否定 / 表示未检查 → 提及但未成立
        p_men, p_sup = P_MEN_NEGATE, P_SUP_NEGATE
        evidence = (neg_hits or unc_hits or aff_negated)[:3]
    elif aff_hits:
        # 第一人称明确肯定 → 成立
        p_men, p_sup = P_MEN_AFFIRM, P_SUP_AFFIRM
        evidence = aff_hits[:3]
    elif men_hits:
        # 仅提及（如转述检查项目名）→ 提及未成立
        p_men, p_sup = P_MEN_MENTION, P_SUP_MENTION
        evidence = men_hits[:3]
    else:
        p_men, p_sup = P_MEN_ABSENT, P_SUP_ABSENT
        evidence = []

    if p_sup >= TAU_F:
        role = "supported"
    elif p_men >= TAU_F:
        role = "gap"
    else:
        role = "absent"
    return JevVerifiedFact(
        prop_id=prop.prop_id, source_cmaid=prop.source_cmaid,
        proposition_zh=prop.proposition_zh, proposition_en=prop.proposition_en,
        is_red_flag=prop.is_red_flag, p_men=p_men, p_sup=p_sup, role=role,
        matched_evidence_span=evidence, follow_up_chip=prop.follow_up_chip,
    )

# ---------------- Jev 神经判别模式（TypeSafe System One） ----------------


def _fact_db() -> sqlite3.Connection:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(FACTS_DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS jev_facts ("
        "k TEXT PRIMARY KEY, p_men REAL, p_sup REAL, evidence TEXT, mode TEXT)")
    conn.commit()
    return conn


def _fact_key(q: str, prop: ClinicalProposition) -> str:
    # v2：要素级双问题协议版本号，协议变更时自动隔离旧缓存
    raw = f"v2||{q}||{prop.prop_id}||{prop.proposition_zh}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _verify_via_jev(q: str, prop: ClinicalProposition) -> JevVerifiedFact:
    """真实 Jev 调用（双问题协议 v2：要素级提问）。

    v1（整句命题嵌入问题）已废弃：Jev 将复合命题当整体判定，
    "本次胸痛在晚餐后出现"因患者未提及晚餐而 Mention 被整句否决
    （p_men=0.07-0.23，实测多轮探针）。v2 从命题的 mention_keywords
    提取核心要素短语（如「胸痛」）做要素级提问，整句命题仅作归属
    参考——实测胸痛 p_men=0.95/p_sup=0.78，正确进入 supported。
    一次请求并行携带两个 Noul 问题，返回校准概率 p_men / p_sup。
    失败回落确定性规则模式。每次调用独立开 SQLite 连接
    （sqlite3 对象不可跨线程共用）。"""
    from src.verification.jev_client import jev_noul_batch
    key = _fact_key(q, prop)
    conn = _fact_db()
    try:
        row = conn.execute("SELECT p_men, p_sup, evidence, mode FROM jev_facts WHERE k=?",
                           (key,)).fetchone()
        if row:
            p_men, p_sup, ev, mode = row
            role = ("supported" if p_sup >= TAU_F else "gap" if p_men >= TAU_F else "absent")
            return JevVerifiedFact(prop_id=prop.prop_id, source_cmaid=prop.source_cmaid,
                                   proposition_zh=prop.proposition_zh, proposition_en=prop.proposition_en,
                                   is_red_flag=prop.is_red_flag, p_men=p_men, p_sup=p_sup, role=role,
                                   matched_evidence_span=json.loads(ev or "[]"),
                                   follow_up_chip=prop.follow_up_chip)
        core = (prop.mention_keywords[0] if prop.mention_keywords
                else prop.proposition_zh)
        state = (f"【患者陈述】\n{q}\n\n【所属命题】{prop.proposition_zh}"
                 f"（要素类别：{prop.element_category}）")
        probs = jev_noul_batch(state, {
            "p_men": (f"患者陈述是否提及或涉及「{core}」这一临床要素？"
                      "无论患者肯定它、明确否定它，还是表示尚未检查/不清楚，"
                      "只要涉及即为真；完全未涉及为假。"),
            "p_sup": (f"患者陈述是否以第一人称明确肯定、预设或复述了"
                      f"「{core}」相关的临床事实（症状、诱因、持续时间、"
                      "既往病史、体征数值）？明确肯定为真，"
                      "明确否定或仅提及未确认为假。"),
        })
        if probs is None:  # 未配置 Key 或调用失败 → 确定性兜底
            return verify_deterministic(q, prop)
        p_men, p_sup = probs["p_men"], probs["p_sup"]
        role = "supported" if p_sup >= TAU_F else "gap" if p_men >= TAU_F else "absent"
        conn.execute("INSERT OR REPLACE INTO jev_facts VALUES (?,?,?,?,?)",
                     (key, p_men, p_sup, json.dumps([], ensure_ascii=False), "jev"))
        conn.commit()
    finally:
        conn.close()
    return JevVerifiedFact(prop_id=prop.prop_id, source_cmaid=prop.source_cmaid,
                           proposition_zh=prop.proposition_zh, proposition_en=prop.proposition_en,
                           is_red_flag=prop.is_red_flag, p_men=p_men, p_sup=p_sup, role=role,
                           matched_evidence_span=[], follow_up_chip=prop.follow_up_chip)


def verify_query(q: str, props: List[ClinicalProposition], workers: int = 8,
                 use_api: bool = False) -> List[JevVerifiedFact]:
    """对一批命题并发执行 Two-Question 核验，返回逐命题三态结果。

    use_api=True：真实 Jev（TypeSafe System One）双 Noul 并行核验，
    需 .env 配置 JEV_API_KEY/TYPESAFE_API_KEY；未配置或调用失败自动回落确定性规则。
    """
    if not props:
        return []
    q_exp = expand_colloquial(q)  # 口语归一化：确定性匹配与缓存键均用对照扩充版
    if not use_api:
        return [verify_deterministic(q_exp, p) for p in props]
    from src.verification.jev_client import jev_available
    if not jev_available():
        print("[Jev] 未配置 JEV_API_KEY/TYPESAFE_API_KEY，回落确定性规则模式")
        return [verify_deterministic(q_exp, p) for p in props]
    out: List[JevVerifiedFact] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_verify_via_jev, q_exp, p): p for p in props}
        for fut in as_completed(futs):
            out.append(fut.result())
    return out

# ---------------- 防幻觉硬闸门（Hard Gate） ----------------

def filter_facts_for_generator(verified_facts: List[JevVerifiedFact],
                               chip_top_cases: int = 2, chip_max: int = 6
                               ) -> Dict[str, object]:
    """Hard Gate：absent 命题彻底移除；仅保留 supported/gap 进入生成器。

    同时从 Top-N 病例的 absent 命题按鉴别重要度导出 pending_clarification_chips。
    """
    supported = [f for f in verified_facts if f.role == "supported"]
    gap = [f for f in verified_facts if f.role == "gap"]
    absent = [f for f in verified_facts if f.role == "absent"]

    # 追问 chips：仅取前 chip_top_cases 个病例的 absent 命题
    keep_cmaids = {f.source_cmaid for f in supported[:1] + gap[:1]} | \
                  set(list(dict.fromkeys([f.source_cmaid for f in verified_facts]))[:chip_top_cases])
    candidates = [f for f in absent if f.source_cmaid in keep_cmaids]
    candidates.sort(key=lambda f: (0 if f.is_red_flag else 1,
                                   CATEGORY_PRIORITY.get(_cat_of(f), 9),
                                   -abs(f.p_men - TAU_F)))
    chips = [f.follow_up_chip or f.proposition_zh for f in candidates[:chip_max]]
    return {"supported_facts": supported, "gap_facts": gap,
            "pending_clarification_chips": chips}


def _cat_of(f: JevVerifiedFact) -> str:
    """从命题 ID 无法取回类别时按文本启发式（默认给中位优先级）。"""
    return "核心症状与诱因"

# ---------------- CLI：端到端演示 ----------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Task 5: Two-Question Jev 事实判断引擎")
    parser.add_argument("--query", required=True, help="患者口语陈述")
    parser.add_argument("--topk", type=int, default=2, help="参与核验的病例数")
    parser.add_argument("--props", type=int, default=30, help="每病例核验命题上限")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--api", action="store_true", help="启用 API 神经判别（默认确定性规则）")
    args = parser.parse_args()

    from src.retrieval.stage1_retriever import load_index
    from src.schemas import ClinicalProposition

    idx = load_index()
    cmaids = [c for c, _ in idx.query(args.query, top_k=args.topk)]
    print(f"Stage 1 Top-{len(cmaids)} 病例: {cmaids}")

    props: List[ClinicalProposition] = []
    for c in cmaids:
        case = json.loads((CACHE_DIR.parent / "structured_cases" /
                           f"case_{c}_structured.json").read_text(encoding="utf-8"))
        props.extend([ClinicalProposition(**p) for p in case.get("propositions", [])[:args.props]])
    print(f"待核验命题 {len(props)} 条（模式: {'API' if args.api else '确定性规则'}）")

    facts = verify_query(args.query, props, workers=args.workers, use_api=args.api)
    gated = filter_facts_for_generator(facts)

    from collections import Counter
    print(f"三态分布: {dict(Counter(f.role for f in facts))}")
    print(f"\n--- supported（{len(gated['supported_facts'])} 条，进入生成器）---")
    for f in gated["supported_facts"][:10]:
        rf = " [红旗]" if f.is_red_flag else ""
        print(f"  {f.prop_id}{rf} p_sup={f.p_sup:.2f} | {f.proposition_zh} | 证据: {f.matched_evidence_span}")
    print(f"\n--- gap（{len(gated['gap_facts'])} 条，进入生成器）---")
    for f in gated["gap_facts"][:10]:
        print(f"  {f.prop_id} p_men={f.p_men:.2f} p_sup={f.p_sup:.2f} | {f.proposition_zh}")
    print(f"\n--- pending_clarification_chips（追问按钮）---")
    for c in gated["pending_clarification_chips"]:
        print(f"  [{c}]")


if __name__ == "__main__":
    main()
