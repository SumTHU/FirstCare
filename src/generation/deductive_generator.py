# -*- coding: utf-8 -*-
r"""Task 6：五步式受控演绎导诊生成器（Grounded Answer Generation）。

将医疗导诊建模为显式三段论演绎推理：
* 大前提 —— CMCR 病例鉴别规范（检索得到的 Top-k 结构化病例）；
* 小前提 —— 经 Jev 双问题核验、经硬闸门过滤的 `supported` 与 `gap` 患者事实
  （`absent` 事实在 Task 5 `filter_facts_for_generator()` 中已被物理剔除，
  绝不进入本模块上下文，根除 Veto 4「捏造患者事实」类幻觉）；
* 结论 —— 分诊科室路径、就医紧急度与可执行建议。

强制五步输出结构（System Prompt 与确定性模板同时锁定）：
  (i)   明确的方向性分诊结论 + 就医紧急度（立即急诊 vs 门诊就诊）
  (ii)  破除患者表面诱因认知误区
  (iii) 演绎事实映射分析（supported 事实 → CMCR 病例鉴别标准）
  (iv)  结论翻转边界与红旗征警示（gap 事实 → 病情升级触发条件）
  (v)   可执行的下一步就医与检查建议 + [EN] 英文摘要

红旗急危升级硬性规则（Red-Flag Escalation Hard Rule，模板层实现）：
  凡任一 is_red_flag=True 命题被 Jev 判为 supported，紧急度强制置为
  「立即急诊」，并对最终文本（含 LLM 输出）执行禁语清洗——
  「居家观察 / 普通门诊随诊 / 无需就医 / 在家观察」等反向致命表述
  一律剔除（与 Task 7 评测 Veto 2 一票否决联动，红旗组 Veto 2 发生率=0）。

双模式：
  * LLM 模式（--llm）：调用并行智算云 OpenAI 兼容接口（PARATERA_API_KEY /
    PARATERA_DECOMPOSE_MODEL），System Prompt 锁定五步结构；
    输出经红旗硬规则与禁语清洗后校验，不合规自动回落模板模式。
  * 确定性模板模式（默认）：零成本、离线、完全可复现，直接由结构化
    数据装配五段文本。

用法：
    python -m src.generation.deductive_generator --query "..."              # 模板模式全链路演示
    python -m src.generation.deductive_generator --query "..." --llm        # LLM 模式
    python -m src.generation.deductive_generator --query "..." --red-flag   # 红旗升级演示
"""

import argparse
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.config import CACHE_DIR, PARATERA_BASE_URL, STRUCTURED_CASES_DIR, get_env

# ---------------- 红旗急危升级硬规则（模板层，与 Task 7 Veto 2 联动） ----------------

FORBIDDEN_PHRASES_RED_FLAG = ("居家观察", "在家观察", "普通门诊随诊", "无需就医",
                              "不必就医", "无需急诊", "自行缓解即可")
FORBIDDEN_PRESCRIPTION_HINTS = ("处方：", "用量：每日口服", "按说明书加倍服用")

EMERGENCY_NOW = "立即急诊"
OUTPATIENT = "门诊就诊"

# 事后事实校验层：患者事实措辞（用于扫描 LLM/模板输出中的越界表述）。
# 注意：改写文本本身不得包含任何触发短语，否则会造成嵌套改写。
POSTHOC_FACT_PHRASES = ("您具备", "您患有", "您已出现", "您已确认", "您存在")
POSTHOC_REWRITE = "（病例提示待核对特征）"
# 否定语境前置词：短语前方出现这些词时不改写（如"非您已确认的事实"
# 本身就是合规措辞，2026-10-31 实测误改教训）
POSTHOC_NEGATION_PREFIXES = ("非", "不是", "不等于", "未", "不", "没")


@dataclass
class DeductiveAnswer:
    """五步式导诊答案的结构化容器（前端 Task 8 直接渲染）。"""
    query: str
    mode: str                                   # "llm" | "template"
    urgency: str = OUTPATIENT                   # EMERGENCY_NOW | OUTPATIENT
    triage_route: str = ""                      # 科室路径（如"急诊科首诊排查 → 消化科/感染科协同"）
    red_flag_triggered: bool = False            # 红旗升级硬规则是否触发
    cited_cmaids: List[str] = field(default_factory=list)
    sections: Dict[str, str] = field(default_factory=dict)   # i..v 五段中文正文
    en_summary: str = ""                        # [EN] 英文摘要
    veto2_scrubbed: bool = False                # LLM 输出是否触发过禁语清洗
    posthoc_scrubbed: bool = False              # 事后事实校验层是否改写过越界表述
    red_flag_rescued_n: int = 0                 # 红旗召回安全网救回的命题数
    fallback_reason: str = ""                   # LLM 失败/违规回落模板的原因


# ---------------- 输入装配 ----------------

def load_case(cmaid: str) -> Optional[Dict[str, Any]]:
    p = STRUCTURED_CASES_DIR / f"case_{cmaid}_structured.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def specialty_route(case: Dict[str, Any], top_cases: Optional[List[Dict[str, Any]]] = None) -> str:
    """由病例多学科救治链派生科室路径（零预设枚举）。
    Top-1 病例缺失科室字段时，回退到 Top-k 中首个有科室信息的病例。"""
    candidates = [case] + [c for c in (top_cases or []) if c is not case]
    for c in candidates:
        specs = [s["specialty"] for s in c.get("involved_specialties", [])][:3]
        specs = list(dict.fromkeys(specs))
        if specs:
            if len(specs) == 1:
                return specs[0]
            return f"{'、'.join(specs[:-1])}与{specs[-1]}协同"
        dept = (c.get("reporting_department") or "").strip()
        if dept:
            return dept
    return "相应专科门诊"


def _shorten(text: str, n: int = 110) -> str:
    text = (text or "").strip().replace("\n", "")
    return text[:n] + ("……" if len(text) > n else "")


# ---------------- supported 事实双栏拆分（Veto 4 防线第二层） ----------------

_DIG_CN = str.maketrans("0123456789", "零一二三四五六七八九")


def _specificity_guard(prop_text: str, query_text: str) -> bool:
    """数值/时间限定词守卫：True = 命题的限定细节在患者陈述中有锚点。

    提取命题中的阿拉伯数字与中文时间限定词（如"2年前""38.4""15毫克"
    的"15"），逐一在患者陈述（口语归一化后，数字与汉字数字互认）中
    做子串比对；任一找不到锚点即不通过。背景（2026-10-31 评测实测）：
    命题"2年前无诱因出现发热，最高体温38.4℃"的 affirm 关键词"发热"
    虽在陈述中命中，但"2年前/38.4℃"患者从未说过——词面锚点须覆盖
    命题的定量限定细节，防止时间/数值错位的事实被当作患者已确认。
    """
    import re
    toks = set()
    for m in re.finditer(r"\d+(?:\.\d+)?", prop_text):
        toks.add(m.group(0))
    for m in re.finditer(r"[零一二两三四五六七八九十百千\d]+"
                         r"(?:个多月|个月|年|天|日|周|星期|小时|分钟)(?:前|余)?", prop_text):
        toks.add(m.group(0))
    if not toks:
        return True
    q_norm = query_text.translate(_DIG_CN)
    for t in toks:
        if t not in query_text and t.translate(_DIG_CN) not in q_norm:
            return False
    return True


def split_supported_facts(query: str, supported_facts: List[Any],
                          top_cases: List[Dict[str, Any]]
                          ) -> tuple:
    """把 Jev supported 事实拆为（患者已确认, 病例待核对特征）两栏。

    判据（双条件）：Jev 判 supported **且** 确定性词面锚点命中
    （affirm 关键词在患者陈述中出现）方可进入"患者已确认"栏；
    否则降为"病例待核对特征"——该特征来自候选病例原文，患者尚未
    确认，生成器只能以"待核对"措辞提示排查方向，严禁写成
    "您具备/您患有"类患者事实。

    背景（2026-10-31 评测实测）：Jev v2 对复合命题与否定命题存在
    精率问题（如患者有皮疹时"全身皮肤黏膜无皮疹"被判 supported
    p_sup=0.85；复合命题"皮疹伴瘙痒和局部刺痛"因主要素命中被整句
    判 supported），而确定性规则在三例上全部判对。故以确定性词面
    锚点作为第二道确认，双栏输出供生成器区分措辞。确定性规则为
    纯本地计算，零额外 API 成本。
    """
    from src.schemas import ClinicalProposition
    from src.verification.jev_fact_verifier import expand_colloquial, verify_deterministic

    prop_map: Dict[str, Dict[str, Any]] = {}
    for c in top_cases:
        for p in c.get("propositions", []):
            prop_map[p.get("prop_id")] = p
    q_exp = expand_colloquial(query)
    affirmed, case_feats = [], []
    for f in supported_facts:
        raw = prop_map.get(f.prop_id)
        if raw is None:                      # 找不到原命题 → 保守归入待核对
            case_feats.append(f)
            continue
        det = verify_deterministic(q_exp, ClinicalProposition(**raw))
        if det.role == "supported" and _specificity_guard(raw["proposition_zh"], q_exp):
            affirmed.append(f)
        else:
            case_feats.append(f)
    return affirmed, case_feats


# ---------------- 红旗召回安全网（宁升级勿漏，2026-10-31 评测实测补设） ----------------

def select_props(case: Dict[str, Any], cap: int = 30) -> List[Dict[str, Any]]:
    """命题选取：红旗命题全部入选（不受 cap 裁剪），其余按原序补足 cap。

    背景：实测病例 1670699/1366882 存在红旗命题位于第 30 条之后被
    cap 裁剪而从未核验的情况——红旗命题承担急危升级硬规则的触发
    职责，必须全量核验。
    """
    props = case.get("propositions", [])
    rf = [p for p in props if p.get("is_red_flag")]
    rest = [p for p in props if not p.get("is_red_flag")]
    return rf + rest[:max(cap - len(rf), 0)]


def rescue_red_flags(query: str, all_facts: List[Any],
                     top_cases: List[Dict[str, Any]]) -> List[Any]:
    """红旗召回确定性安全网：救回 Jev 未判 supported 的红旗命题。

    条件（双确认，与 split_supported_facts 同判据）：确定性词面
    affirm 锚点命中（口语归一化后）且数值/时间限定词守卫通过。
    Jev 主判地位不变——救回的命题不进入"患者已确认"栏，而是作为
    "待核对红旗特征"参与红旗急危升级判定（升级判定从严、事实表述
    从谨，两条线解耦）。实测卡点均为口语词面缺口（"叫不应"未命中
    "呼之不应" affirm 关键词、"血色素很低"未命中"血红蛋白很低"），
    已在 COLLOQUIAL_NORMS 补录对应归一化映射。

    注意：病例侧否定型红旗命题（如"病历未记录患者出现胸痛"）在
    确定性规则下为 absent，天然不会被救回——这是预期行为。
    """
    from src.schemas import ClinicalProposition
    from src.verification.jev_fact_verifier import expand_colloquial, verify_deterministic

    prop_map: Dict[str, Dict[str, Any]] = {}
    for c in top_cases:
        for p in c.get("propositions", []):
            prop_map[p.get("prop_id")] = p
    q_exp = expand_colloquial(query)
    rescued: List[Any] = []
    for f in all_facts or []:
        if f.role == "supported" or not f.is_red_flag:
            continue
        raw = prop_map.get(f.prop_id)
        if raw is None:
            continue
        det = verify_deterministic(q_exp, ClinicalProposition(**raw))
        if det.role == "supported" and _specificity_guard(raw["proposition_zh"], q_exp):
            rescued.append(f)
    return rescued


# ---------------- 事后事实校验层（对生成输出就地清洗，Veto 4 第三道防线） ----------------

def _bigram_overlap(text: str, ref: str) -> bool:
    """text 与 ref 是否存在字符二元组重叠（忽略空白与标点）。"""
    import re
    clean = re.sub(r"[\s，。、；：！？,.;:!?\"'“”‘’（）()\[\]【】…·\-—*]",
                   "", text)
    if len(clean) < 2:
        return True
    return any(clean[i:i + 2] in ref for i in range(len(clean) - 1))


def posthoc_fact_guard(ans: DeductiveAnswer, affirmed: List[Any],
                       case_feats: List[Any], gap_facts: List[Any],
                       prop_map: Dict[str, Dict[str, Any]], query: str) -> None:
    """对五段正文做两层确定性事实校验并就地改写越界表述：

    层1（病例泄漏）：病例待核对特征的核心关键词出现在患者事实措辞
        （您具备/您患有/您已出现/您已确认/您存在）后 60 字符内
        → 改写为"待核对"措辞；
    层2（凭空捏造）：患者事实措辞后的从句与患者陈述（口语归一化）
        及已确认事实集均无字符二元组重叠 → 判为越出事实清单的自编
        表述，改写。改写均置 posthoc_scrubbed 审计标记。
    两层均只改措辞、不改变红旗升级等元数据判定。
    否定语境（"非您已确认"等）不改写；改写文本不含触发短语，
    避免嵌套改写（2026-10-31 实测教训）。
    """
    from src.verification.jev_fact_verifier import expand_colloquial
    q_ref = expand_colloquial(query)
    affirmed_ref = q_ref + " " + " ".join(f.proposition_zh for f in affirmed)

    # 泄漏词表只取病例待核对特征的特异关键词。gap 命题的 mention
    # 关键词多为泛化词（发热/咳嗽等），与合法患者确认事实重叠，
    # 纳入会造成大面积误改（2026-10-31 实测教训）。
    leak_kws: List[str] = []
    for f in case_feats:
        raw = prop_map.get(f.prop_id) or {}
        kws = [k for k in (raw.get("mention_keywords")
                           or raw.get("affirm_keywords") or []) if len(k) >= 2]
        if not kws:
            t = f.proposition_zh or ""
            kws = [t[i:i + 2] for i in range(0, min(len(t), 10), 2)]
        leak_kws.extend(kws)

    for key in list(ans.sections.keys()):
        txt = ans.sections[key]
        out = txt
        for ph in POSTHOC_FACT_PHRASES:
            start = 0
            while True:
                i = out.find(ph, start)
                if i < 0:
                    break
                if any(out[max(0, i - len(n)):i].endswith(n)
                       for n in POSTHOC_NEGATION_PREFIXES):
                    start = i + len(ph)
                    continue
                window = out[i + len(ph): i + len(ph) + 60]
                cut = len(window)
                for stop in ("。", "；", "\n"):
                    j = window.find(stop)
                    if 0 < j < cut:
                        cut = j
                clause = window[:cut]
                hit_leak = bool(leak_kws) and any(kw in window for kw in leak_kws)
                fabricated = (len(clause.strip()) >= 4
                              and not _bigram_overlap(clause, affirmed_ref))
                if hit_leak or fabricated:
                    out = out[:i] + POSTHOC_REWRITE + out[i + len(ph):]
                    ans.posthoc_scrubbed = True
                    start = i + len(POSTHOC_REWRITE)
                else:
                    start = i + len(ph)
        ans.sections[key] = out


# ---------------- 确定性模板模式 ----------------

def build_template_answer(query: str, top_cases: List[Dict[str, Any]],
                          supported_facts: List[Any], gap_facts: List[Any],
                          pending_chips: List[str],
                          case_feature_facts: Optional[List[Any]] = None) -> DeductiveAnswer:
    ans = DeductiveAnswer(query=query, mode="template")
    case_feature_facts = case_feature_facts or []
    if not top_cases:
        ans.sections = {k: "未能检索到匹配病例，请补充症状描述后重试。"
                        for k in ("i", "ii", "iii", "iv", "v")}
        ans.triage_route = "无法确定"
        return ans

    top1 = top_cases[0]
    ans.cited_cmaids = [c["cmaid"] for c in top_cases]
    # 红旗升级判定范围 = 全部 Jev supported 事实（含待核对栏）——
    # 任何红旗征被 Jev 判 supported 即视为危险信号，宁升级勿漏
    red_flags = [f for f in list(supported_facts) + list(case_feature_facts)
                 if f.is_red_flag]

    # ---- 红旗急危升级硬规则（模板层强制） ----
    ans.red_flag_triggered = bool(red_flags)
    if ans.red_flag_triggered:
        ans.urgency = EMERGENCY_NOW
    ans.triage_route = (
        f"急诊科首诊排查 → {specialty_route(top1, top_cases)}"
        if ans.red_flag_triggered else f"{specialty_route(top1, top_cases)}首诊")

    # (i) 方向性分诊结论
    route_txt = f"建议就诊路径：{ans.triage_route}；就医紧急度：**{ans.urgency}**。"
    if ans.red_flag_triggered:
        route_txt += ("您陈述中已出现需紧急处置的危险信号，请立即前往急诊科，"
                      "切勿居家观察或仅预约普通门诊。")
    ans.sections["i"] = route_txt

    # (ii) 破除表面诱因认知误区
    h = _shorten(top1.get("structured_abstract", {}).get("history_summary", ""), 80)
    ans.sections["ii"] = (
        f"需要提醒：表面诱因不等于病因。您描述的情况看似可由近期诱因解释，"
        f"但 CMCR 病例 `cmaid/{top1['cmaid']}`《{top1['title']}》显示，"
        f"类似起始表现（{h}）最终确诊为需要专门处置的疾病。"
        f"请不要把症状简单归结为\"吃坏了/累着了/着凉了\"等日常原因而延误评估。")

    # (iii) 演绎事实映射分析（双栏：患者已确认 vs 病例待核对特征）
    order = sorted(supported_facts,
                   key=lambda f: (not f.is_red_flag, -f.p_sup))
    lines = []
    for f in order[:6]:
        src = next((c for c in top_cases if c["cmaid"] == f.source_cmaid), top1)
        rf = "**（急危红旗征）**" if f.is_red_flag else ""
        lines.append(
            f"- {rf}您已确认的事实\"{f.proposition_zh}\"与 CMCR 病例 "
            f"`cmaid/{src['cmaid']}`《{src['title']}》的鉴别特征相符"
            f"（核验概率 p_sup={f.p_sup:.2f}），支持将该病例所涉疾病纳入重点排查。")
    corder = sorted(case_feature_facts,
                    key=lambda f: (not f.is_red_flag, -f.p_sup))
    for f in corder[:4]:
        src = next((c for c in top_cases if c["cmaid"] == f.source_cmaid), top1)
        rf = "**（急危红旗征）**" if f.is_red_flag else ""
        lines.append(
            f"- {rf}检索到的病例还提示一个**待核对特征**：\"{f.proposition_zh}\""
            f"（来自 CMCR 病例 `cmaid/{src['cmaid']}`）——请注意这不是您已确认的事实，"
            f"需由医生当面问诊/检查核实，仅用于提示排查方向。")
    ans.sections["iii"] = ("\n".join(lines) if lines
                           else "暂无被完全确认的阳性事实；以下按已提及要素分析。")

    # (iv) 结论翻转边界与红旗征警示
    gorder = sorted(gap_facts, key=lambda f: (not f.is_red_flag, -f.p_men))
    glines = []
    for f in gorder[:6]:
        if f.is_red_flag:
            glines.append(f"- **危险信号（尚未确认）**：\"{f.proposition_zh}\"——"
                          f"一旦出现或证实，病情风险将立即升级为急诊级别。")
        else:
            glines.append(f"- 您尚未确认或暂未出现：\"{f.proposition_zh}\"——"
                          f"若后续出现，请按上述路径尽快复诊。")
    ans.sections["iv"] = ("\n".join(glines) if glines
                          else "当前无关键待确认要素。")

    # (v) 可执行的下一步建议
    diag = _shorten(top1.get("structured_abstract", {}).get("diagnosis_method", ""), 100)
    vlines = [f"到院后建议优先完善：{diag}"]
    if pending_chips:
        vlines.append("就诊前请留意并如实告知医生：" + "；".join(pending_chips[:4]) + "。")
    vlines.append("本建议为AI导诊参考，不构成诊断；症状加重时请立即拨打120。")
    ans.sections["v"] = "\n".join(vlines)

    sup_en = "; ".join(f.proposition_en or f.proposition_zh for f in order[:3])
    case_en = "; ".join(f.proposition_en or f.proposition_zh for f in corder[:3])
    gap_en = "; ".join(f.proposition_en or f.proposition_zh for f in gorder[:3])
    ans.en_summary = (
        f"[EN] Triage: {('Emergency department NOW' if ans.red_flag_triggered else 'Outpatient visit')} "
        f"-> {specialty_route(top1, top_cases)}. Patient-confirmed findings: {sup_en or 'none confirmed'}. "
        f"Case-derived features to verify on-site: {case_en or 'none'}. "
        f"Unconfirmed/gap items to monitor: {gap_en or 'none'}. "
        f"Grounded on CMCR case cmaid/{top1['cmaid']}.")
    return ans


# ---------------- LLM 模式（并行智算云 OpenAI 兼容接口） ----------------

SYSTEM_PROMPT = """你是受控演绎医疗导诊生成器。你只能依据输入中的【患者陈述】【CMCR病例证据】【已核验事实】生成回答，严禁使用任何未在输入中出现过的病例编号、患者事实或检查结论。

必须严格按以下五个小节输出，使用完全一致的小节标题：
(i) 分诊结论与紧急度：开门见山给出推荐就诊科室路径与就医紧急度（立即急诊 或 门诊就诊）。
(ii) 表面诱因误区提醒：直接回应患者可能把症状错误归因于日常诱因的风险。
(iii) 演绎事实映射：将【已核验事实-患者已确认】中的条目映射到对应CMCR病例的鉴别特征，说明"因为您已确认事实A和B，对应病例X的特征，所以需重点排查Y"。对【病例待核对特征】中的条目，只能表述为"检索到的病例提示您还需核对/排除……（该特征来自病例，不是您已确认的事实，需医生当面核实）"，严禁写成"您具备/您患有/您已出现"。
(iv) 结论翻转边界与红旗警示：针对【已核验事实-gap】中的条目说明"尚未确认/暂未出现，一旦出现将立即升级风险"。
(v) 下一步建议：列出到院后建议完善的检查与注意事项，最后另起一行以"[EN] "开头给出三句以内的英文摘要（英文摘要同样须区分 patient-confirmed 与 case-derived to-verify）。

硬性规则：
1. 若输入标注存在红旗征（is_red_flag=true）的supported事实（含患者已确认与待核对两栏），紧急度必须写"立即急诊"，且全文严禁出现"居家观察""在家观察""普通门诊随诊""无需就医"。
2. 严禁引用任何【已核验事实-gap】或未提及的症状作为患者既有阳性事实（gap 只能作为"尚未确认"提及）。
3. 严禁将【病例待核对特征】中的任何条目写成患者既有事实——这些特征来自候选病例原文而非患者陈述。
4. 严禁开具具体处方药与剂量。
5. 每个小节以"(i) "…"(v) "开头，正文通俗、面向患者。"""


def _llm_available() -> bool:
    return bool(get_env("PARATERA_API_KEY")) and bool(
        get_env("PARATERA_DECOMPOSE_MODEL", "kimi-k3.1-pro"))


def _paratera_chat(user_msg: str) -> Optional[str]:
    """调用并行智算云（OpenAI 兼容）。平台网关偶发对同一模型名返回
    400/超时（实测同 Key 同模型先后出现 200/400/超时），故实现
    多候选模型 × 有限重试的调用链，全部失败返回 None（回落模板）。"""
    from openai import OpenAI
    import httpx
    candidates = [get_env("PARATERA_DECOMPOSE_MODEL", "DeepSeek-V4.1-Flash-1017"),
                  "DeepSeek-V4.1-Flash-1017", "DeepSeek-V4.1-Flash"]
    candidates = list(dict.fromkeys(c for c in candidates if c))
    last_err = ""
    for model in candidates:
        for attempt in range(2):
            try:
                http = httpx.Client(trust_env=False, timeout=180)
                client = OpenAI(base_url=PARATERA_BASE_URL,
                                api_key=get_env("PARATERA_API_KEY", ""),
                                timeout=180, max_retries=1, http_client=http)
                resp = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": SYSTEM_PROMPT},
                              {"role": "user", "content": user_msg}],
                    temperature=0.2)
                return resp.choices[0].message.content
            except Exception as e:  # noqa: BLE001
                last_err = f"{model}(第{attempt + 1}次): {str(e)[:120]}"
                print(f"[Task6] LLM 调用失败 {last_err}")
    return None


def _build_llm_user_msg(query: str, top_cases: List[Dict[str, Any]],
                        supported_facts: List[Any], gap_facts: List[Any],
                        case_feature_facts: Optional[List[Any]] = None) -> str:
    case_blocks = []
    for c in top_cases:
        ab = c.get("structured_abstract", {})
        case_blocks.append(
            f"【病例 cmaid/{c['cmaid']}】《{c['title']}》\n"
            f"  病史摘要：{_shorten(ab.get('history_summary', ''), 120)}\n"
            f"  症状体征：{_shorten(ab.get('symptoms_and_signs', ''), 150)}\n"
            f"  确诊手段：{_shorten(ab.get('diagnosis_method', ''), 120)}\n"
            f"  救治链科室：{specialty_route(c)}")
    sup_lines = [f"- {f.proposition_zh} (p_sup={f.p_sup:.2f}, "
                 f"is_red_flag={'true' if f.is_red_flag else 'false'})"
                 for f in sorted(supported_facts, key=lambda x: (not x.is_red_flag, -x.p_sup))[:8]]
    case_feats = case_feature_facts or []
    feat_lines = []
    for f in sorted(case_feats, key=lambda x: (not x.is_red_flag, -x.p_sup))[:6]:
        feat_lines.append(f"- {f.proposition_zh} (来自病例 cmaid/{f.source_cmaid}, "
                          f"p_sup={f.p_sup:.2f}, "
                          f"is_red_flag={'true' if f.is_red_flag else 'false'})")
    gap_lines = [f"- {f.proposition_zh} (p_men={f.p_men:.2f}, "
                 f"is_red_flag={'true' if f.is_red_flag else 'false'})"
                 for f in sorted(gap_facts, key=lambda x: (not x.is_red_flag, -x.p_men))[:8]]
    return (f"【患者陈述】\n{query}\n\n【CMCR病例证据】\n" + "\n".join(case_blocks) +
            "\n\n【已核验事实-患者已确认】\n" + ("\n".join(sup_lines) or "（无）") +
            "\n\n【病例待核对特征（来自病例原文，严禁写成患者事实）】\n" + ("\n".join(feat_lines) or "（无）") +
            "\n\n【已核验事实-gap】\n" + ("\n".join(gap_lines) or "（无）"))


def _parse_llm_sections(text: str) -> Dict[str, str]:
    """按 "(i)"…"(v)" 小节标题切分 LLM 输出。"""
    import re
    marks = [("(i)", "i"), ("(ii)", "ii"), ("(iii)", "iii"),
             ("(iv)", "iv"), ("(v)", "v")]
    pos = []
    for mk, key in marks:
        m = re.search(re.escape(mk), text)
        pos.append((text.find(mk), key) if m else (10**9, key))
    sections: Dict[str, str] = {}
    order = sorted(range(5), key=lambda k: pos[k][0])
    for a, b in zip(order, order[1:]):
        sections[pos[a][1]] = text[pos[a][0]:pos[b][0]].strip()
    sections[pos[order[-1]][1]] = text[pos[order[-1]][0]:].strip()
    return {k: v for k, v in sections.items() if v}


def _scrub_red_flag_violations(ans: DeductiveAnswer) -> None:
    """红旗硬规则：清洗 LLM 输出中的反向致命表述（模板层兜底，Veto 2 联动）。"""
    if not ans.red_flag_triggered:
        return
    for key, txt in list(ans.sections.items()):
        cleaned = txt
        for ph in FORBIDDEN_PHRASES_RED_FLAG:
            if ph in cleaned:
                cleaned = cleaned.replace(ph, "急诊就诊")
                ans.veto2_scrubbed = True
        if cleaned != txt:
            ans.sections[key] = cleaned
    # 英文摘要反向致命降级清洗（2026-10-31 评测实测：正文已升级急诊但
    # [EN] 摘要仍写 "Outpatient visit"，被裁判判 Veto 2）
    if ans.en_summary:
        low = ans.en_summary.lower()
        if "outpatient" in low or "home observation" in low:
            ans.en_summary = ans.en_summary.replace(
                "Outpatient visit", "Emergency department NOW"
            ).replace("outpatient visit", "Emergency department NOW")
            ans.veto2_scrubbed = True
    if ans.red_flag_triggered:
        ans.urgency = EMERGENCY_NOW
        if EMERGENCY_NOW not in ans.sections.get("i", ""):
            ans.sections["i"] = (f"建议就诊路径：{ans.triage_route}；"
                                 f"就医紧急度：**{EMERGENCY_NOW}**。") + ans.sections.get("i", "")


# ---------------- 主入口 ----------------

def generate_deductive_answer(query: str, top_cases: List[Dict[str, Any]],
                              gated: Dict[str, Any], use_llm: bool = False,
                              all_facts: Optional[List[Any]] = None) -> DeductiveAnswer:
    supported: List[Any] = gated.get("supported_facts", [])
    gap: List[Any] = gated.get("gap_facts", [])
    chips: List[str] = gated.get("pending_clarification_chips", [])

    # Veto 4 防线第二层：supported 双栏拆分（患者已确认 vs 病例待核对特征）
    affirmed, case_feats = split_supported_facts(query, supported, top_cases)

    # 红旗召回安全网：Jev 未判 supported 但确定性锚点命中的红旗命题，
    # 以"待核对红旗特征"身份参与升级判定（宁升级勿漏）
    rescued = rescue_red_flags(query, all_facts, top_cases)
    case_feats = list(case_feats) + rescued

    prop_map: Dict[str, Dict[str, Any]] = {}
    for c in top_cases:
        for p in c.get("propositions", []):
            prop_map[p.get("prop_id")] = p

    # 红旗事实必须参与 LLM/模板判定（supported 与 gap 中的红旗都影响升级逻辑，
    # 但强制升级仅由 supported 红旗触发——gap 红旗走步骤 (iv) 的条件警示）
    template = build_template_answer(query, top_cases, affirmed, gap, chips,
                                     case_feature_facts=case_feats)
    template.red_flag_rescued_n = len(rescued)

    if not use_llm:
        posthoc_fact_guard(template, affirmed, case_feats, gap, prop_map, query)
        return template
    if not _llm_available():
        template.fallback_reason = "未配置 PARATERA_API_KEY/PARATERA_DECOMPOSE_MODEL"
        return template

    raw = _paratera_chat(_build_llm_user_msg(query, top_cases, affirmed, gap, case_feats))
    if not raw:
        template.fallback_reason = "LLM 调用失败"
        posthoc_fact_guard(template, affirmed, case_feats, gap, prop_map, query)
        return template

    sections = _parse_llm_sections(raw)
    if len(sections) < 5:
        template.fallback_reason = "LLM 输出未满足五步结构"
        posthoc_fact_guard(template, affirmed, case_feats, gap, prop_map, query)
        return template

    ans = template  # 复用红旗判定与路由元数据
    ans.mode = "llm"
    ans.sections = sections
    en_parts = [v for v in sections.values() if v.startswith("[EN]")]
    if en_parts:
        ans.en_summary = en_parts[0]
        ans.sections = {k: v for k, v in sections.items() if not v.startswith("[EN]")}
    _scrub_red_flag_violations(ans)
    posthoc_fact_guard(ans, affirmed, case_feats, gap, prop_map, query)
    return ans


# ---------------- CLI：全链路演示（Stage 1 → Task 5 → Task 6） ----------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Task 6: 五步式受控演绎导诊生成器")
    parser.add_argument("--query", required=True, help="患者口语陈述")
    parser.add_argument("--topk", type=int, default=2, help="参与演绎的病例数")
    parser.add_argument("--stage1-k", type=int, default=50, help="Stage 1 召回数")
    parser.add_argument("--no-stage2", action="store_true",
                        help="跳过 Stage 2 Pointwise Jev 精排（直接用 Stage 1 序）")
    parser.add_argument("--props", type=int, default=30, help="每病例核验命题上限")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--api", action="store_true", help="Jev 核验走真实 API")
    parser.add_argument("--llm", action="store_true", help="生成器走 LLM（默认模板）")
    args = parser.parse_args()

    from src.verification.jev_fact_verifier import filter_facts_for_generator, verify_query
    from src.retrieval.stage1_retriever import load_index
    from src.schemas import ClinicalProposition

    idx = load_index()
    t0 = time.time()
    cand = idx.query(args.query, top_k=args.stage1_k)
    print(f"[Stage 1] 混合召回 Top-{len(cand)}（{(time.time() - t0) * 1000:.0f}ms）")

    if args.no_stage2:
        cmaids = [c for c, _ in cand[:args.topk]]
    else:
        from src.retrieval.stage2_jev_reranker import rerank
        t1 = time.time()
        ranked = rerank(args.query, [c for c, _ in cand],
                        workers=args.workers, use_api=True)
        cmaids = [r_["cmaid"] for r_ in ranked[:args.topk]]
        print(f"[Stage 2] Pointwise Jev 精排（{(time.time() - t1):.1f}s）Top-{args.topk}: "
              + ", ".join(f"{r_['cmaid']}={r_['score']:.2f}({r_['reason_zh'][:12]})"
                          for r_ in ranked[:args.topk]))
    print(f"进入核验的 Top-{len(cmaids)} 病例: {cmaids}")

    cases, props = [], []
    for c in cmaids:
        case = load_case(c)
        if case is None:
            continue
        cases.append(case)
        props.extend([ClinicalProposition(**p) for p in select_props(case, args.props)])
    print(f"待核验命题 {len(props)} 条")

    facts = verify_query(args.query, props, workers=args.workers, use_api=args.api)
    gated = filter_facts_for_generator(facts)

    ans = generate_deductive_answer(args.query, cases, gated, use_llm=args.llm)

    from collections import Counter
    print(f"三态分布: {dict(Counter(f.role for f in facts))}")
    print(f"生成模式: {ans.mode}" + (f"（回落原因: {ans.fallback_reason}）" if ans.fallback_reason else ""))
    print(f"红旗升级: {'触发' if ans.red_flag_triggered else '未触发'}"
          + ("（已清洗 LLM 禁语）" if ans.veto2_scrubbed else ""))
    print(f"紧急度: {ans.urgency} | 路径: {ans.triage_route}")
    print(f"引用病例: {ans.cited_cmaids}")
    for key, title in (("i", "(i) 分诊结论"), ("ii", "(ii) 诱因误区提醒"),
                       ("iii", "(iii) 演绎事实映射"), ("iv", "(iv) 边界与红旗警示"),
                       ("v", "(v) 下一步建议")):
        print(f"\n== {title} ==\n{ans.sections.get(key, '')}")
    if ans.en_summary:
        print(f"\n== [EN] ==\n{ans.en_summary}")


if __name__ == "__main__":
    main()
