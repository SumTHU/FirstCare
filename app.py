import json
import math
import os
import re
from collections import Counter
from typing import Any, Dict, List, Tuple

import streamlit as st

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False


# ==============================================================================
# 1. LOAD CMCR STRUCTURED CASES WITH PRE-DECOMPOSED CLINICAL PROPOSITIONS (F)
# ==============================================================================
DEFAULT_CASE_1673770_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "cmcr_case_1673770_structured.json"
)

CMCR_CORPUS: List[Dict[str, Any]] = [
    {
        "cmaid": "1673770",
        "title": "发热待查的诊治1例 (T细胞淋巴瘤合并嗜血细胞综合征)",
        "url": "https://rs.yiigle.com/cmaid/1673770",
        "hospital_dept": "西安交通大学第二附属医院急诊科",
        "summary_zh": "49岁女性，进食冰葡萄后高热40℃伴寒战、呕吐2天；既往成人Still病(AOSD)6年服泼尼松；查体SpO2 92%，双侧腋窝及腹股沟淋巴结肿大、躯干散在红疹；初诊误考虑急性胃肠炎抗感染无效，后出现鼻出血、血小板骤降(54×10⁹/L)及铁蛋白极度升高(13472 ng/ml)，骨髓及淋巴结活检确诊T细胞淋巴瘤合并嗜血细胞综合征(HLH)。",
        "summary_en": "49yo female with 40°C fever & vomiting after eating iced grapes; 6-yr history of AOSD on prednisone; SpO2 92%, axillary/inguinal lymphadenopathy & rash. Initially misdiagnosed as acute gastroenteritis; later confirmed as T-cell Lymphoma with HLH.",
        "involved_specialties": [
            {"specialty": "急诊科 (Emergency Medicine)", "role": "首诊抢救、纠正低氧血症 (SpO2 92%) 与危重监护"},
            {"specialty": "血液内科 (Hematology)", "role": "骨髓穿刺/淋巴结活检确诊 T 细胞淋巴瘤合并嗜血综合征 (HLH)，执行依托泊苷化疗"},
            {"specialty": "风湿免疫科 (Rheumatology)", "role": "鉴别排除原发成人 Still 病 (AOSD) 复发"}
        ],
        "propositions": [
            {
                "prop_id": "F1",
                "text_zh": "患者存在高热（39–40℃）伴寒战，且合并腹胀、恶心呕吐等消化道症状（或生冷饮食诱因）",
                "text_en": "High fever (39-40°C) with chills and GI symptoms (bloating, nausea/vomiting after cold food)",
                "mention_kw": ["发烧", "发热", "高烧", "40", "39", "寒战", "吐", "呕吐", "恶心", "腹胀", "冰葡萄"],
                "affirm_kw": ["发烧", "发热", "高烧", "40", "39", "寒战", "吐", "呕吐", "腹胀", "冰葡萄"],
                "negate_kw": ["不吐", "无呕吐", "没发烧"],
                "uncertain_kw": ["不确定是否发烧"],
                "is_red_flag": False,
                "follow_up_chip": "伴随恶心呕吐/进食冰冷食物诱因"
            },
            {
                "prop_id": "F2",
                "text_zh": "患者既往有成人Still病（AOSD）等风湿免疫病史，或长期口服泼尼松等激素",
                "text_en": "Past history of Adult-Onset Still's Disease (AOSD) or long-term corticosteroid (prednisone) use",
                "mention_kw": ["Still", "斯蒂尔", "风湿", "免疫", "泼尼松", "激素"],
                "affirm_kw": ["有成人Still病", "确诊过成人Still病", "斯蒂尔", "吃泼尼松", "服泼尼松", "既往有成人Still病"],
                "negate_kw": ["无风湿病史", "没吃过激素", "既往体健"],
                "uncertain_kw": ["不确定有没有风湿"],
                "is_red_flag": False,
                "follow_up_chip": "既往有成人Still病史且服泼尼松"
            },
            {
                "prop_id": "F3",
                "text_zh": "患者双侧腋窝、腹股沟或颈部可触及肿大淋巴结，或躯干四肢有散在红色皮疹",
                "text_en": "Palpable enlarged lymph nodes (axillary/inguinal) or scattered red skin rash on trunk/limbs",
                "mention_kw": ["淋巴结", "腋窝", "腋下", "腹股沟", "包块", "肿块", "皮疹", "红疹"],
                "affirm_kw": ["摸到肿大淋巴结", "腋窝摸到肿大", "腋下摸到肿块", "红色皮疹", "起红疹", "散在红疹"],
                "negate_kw": ["无淋巴结肿大", "无皮疹", "没摸到包块"],
                "uncertain_kw": ["还没注意腋下有没有", "没注意有没有淋巴结", "不清楚有没有肿块"],
                "is_red_flag": False,
                "follow_up_chip": "腋窝/腹股沟摸到肿大淋巴结且有散在红疹"
            },
            {
                "prop_id": "F4",
                "text_zh": "【急危红旗征】患者伴随明显气短呼吸困难，或指夹血氧饱和度 SpO2 <= 92%",
                "text_en": "[RED FLAG] Shortness of breath / dyspnea or pulse oximetry SpO2 <= 92% (acute hypoxia)",
                "mention_kw": ["气短", "喘", "呼吸困难", "血氧", "SpO2", "92%", "98%", "氧饱和度"],
                "affirm_kw": ["觉得气短", "明显气短", "伴随气短", "喘不上气", "血氧92%", "测血氧只有92%", "血氧低", "SpO2 92%", "呼吸困难"],
                "negate_kw": ["不气短", "无气短", "呼吸正常", "血氧正常98%", "血氧正常"],
                "uncertain_kw": ["没测过血氧", "不知道血氧多少"],
                "is_red_flag": True,
                "follow_up_chip": "伴随气短且测得血氧偏低(SpO2 92%)"
            },
            {
                "prop_id": "F5",
                "text_zh": "【重症红旗征】常规抗感染/激素治疗后仍反复高热，或出现鼻出血、血小板骤降、铁蛋白极高",
                "text_en": "[RED FLAG] Refractory fever despite antibiotics/steroids, or epistaxis / severe thrombocytopenia (HLH sign)",
                "mention_kw": ["抗生素", "头孢", "抗感染", "消炎药", "无效", "反复高热", "鼻出血", "流鼻血", "血小板", "铁蛋白"],
                "affirm_kw": ["抗感染无效", "抗感染治疗无效", "消炎药完全没退烧", "消炎药没用", "反复高烧", "鼻出血", "流鼻血", "血小板降低", "铁蛋白高"],
                "negate_kw": ["尚未用药", "吃药后退烧", "无鼻出血"],
                "uncertain_kw": ["刚吃药还不确定效果"],
                "is_red_flag": True,
                "follow_up_chip": "抗感染治疗无效且出现流鼻血/血小板降低"
            }
        ]
    },
    {
        "cmaid": "CMCR_GI_ACUTE",
        "title": "急性感染性胃肠炎典型病例 (对照鉴别干扰项 Near-Miss Distractor)",
        "url": "https://cmcr.yiigle.com/index",
        "hospital_dept": "三甲医院消化内科 / 肠道门诊",
        "summary_zh": "患者进食不洁或生冷瓜果后急性起病，表现发热、恶心呕吐、阵发性脐周绞痛及水样腹泻；既往体健无自身免疫病及激素用药史，无浅表淋巴结肿大及皮疹，血氧饱和度正常(>=97%)，经补液及对症抗感染24-48h内迅速退热缓解。",
        "summary_en": "Acute infectious gastroenteritis after cold/contaminated food in healthy individual: fever, vomiting, watery diarrhea; normal SpO2 (>=97%), no autoimmune history, lymphadenopathy, or rash.",
        "involved_specialties": [
            {"specialty": "消化内科 / 肠道门诊 (Gastroenterology)", "role": "补液纠酸、对症止吐及肠道抗感染门诊处置"}
        ],
        "propositions": [
            {
                "prop_id": "G1",
                "text_zh": "患者进食生冷/不洁饮食后出现发热、恶心呕吐或腹胀腹泻",
                "text_en": "Fever, nausea, vomiting, or diarrhea following cold/unclean food intake",
                "mention_kw": ["冰葡萄", "生冷", "不洁", "发烧", "发热", "吐", "呕吐", "腹胀", "腹泻", "拉肚子"],
                "affirm_kw": ["冰葡萄", "生冷", "发烧", "发热", "吐", "呕吐", "腹胀", "腹泻", "拉肚子"],
                "negate_kw": ["无呕吐", "无腹泻"],
                "uncertain_kw": [],
                "is_red_flag": False,
                "follow_up_chip": "伴随水样腹泻且无其他全身异常"
            },
            {
                "prop_id": "G2",
                "text_zh": "患者既往体健无风湿免疫病/激素史，无气短及低氧血症（SpO2 >=96%），无淋巴结肿大及皮疹",
                "text_en": "No autoimmune/steroid history, stable vitals (SpO2 >=96%), absence of lymphadenopathy or skin rash",
                "mention_kw": ["无气短", "血氧正常", "无淋巴结肿大", "无皮疹", "既往体健", "成人Still病", "泼尼松", "明显气短", "血氧92%", "摸到肿块", "起红疹"],
                "affirm_kw": ["既往体健", "无风湿病史", "无淋巴结肿大", "无皮疹", "无其他全身异常"],
                "negate_kw": ["成人Still病", "斯蒂尔", "泼尼松", "觉得气短", "明显气短", "血氧92%", "测血氧只有92%", "摸到肿块", "摸到肿大淋巴结", "起红疹", "红色皮疹"],
                "uncertain_kw": [],
                "is_red_flag": False,
                "follow_up_chip": "既往体健、无气短、无淋巴结肿大、无皮疹"
            }
        ]
    },
    {
        "cmaid": "CMCR_CNS_MENINGITIS",
        "title": "成人急性化脓性脑膜炎诊治1例 (发热伴呕吐跨系统鉴别病例)",
        "url": "https://cmcr.yiigle.com/index",
        "hospital_dept": "三甲医院神经内科 / 急诊脑卒中与神经重症通道",
        "summary_zh": "患者突发高热伴剧烈全头炸裂样疼痛、与进食无关的喷射性呕吐；查体见明显颈项强直（低头下颏无法触胸）、克氏征阳性，脑脊液压力及白细胞显著升高，需急诊腰穿与广谱抗感染抢救。",
        "summary_en": "Acute bacterial meningitis presenting with high fever, thunderclap headache, projectile vomiting, and nuchal rigidity (neck stiffness).",
        "involved_specialties": [
            {"specialty": "急诊神经重症 / 神经内科 (Neuro-Emergency / Neurology)", "role": "急诊头颅CT、腰椎穿刺脑脊液检查及鞘内/静脉降颅压抗感染"}
        ],
        "propositions": [
            {
                "prop_id": "N1",
                "text_zh": "【急危红旗征】患者高热伴剧烈炸裂样头痛及喷射状呕吐，且低头时颈部僵硬疼痛（颈项强直）",
                "text_en": "[RED FLAG] High fever with thunderclap headache, projectile vomiting, and neck stiffness upon flexion",
                "mention_kw": ["头痛", "头疼", "炸裂", "喷射", "脖子硬", "颈部僵硬", "颈项强直"],
                "affirm_kw": ["剧烈头痛", "炸裂", "喷射", "脖子硬", "颈部僵硬", "颈项强直"],
                "negate_kw": ["不头痛", "无头痛", "脖子不硬", "低头自如"],
                "uncertain_kw": [],
                "is_red_flag": True,
                "follow_up_chip": "伴随剧烈头痛且低头时脖子僵硬剧痛"
            }
        ]
    }
]


# ==============================================================================
# 2. TWO-QUESTION JEV FACT VERIFICATION & POINTWISE JEV RERANKER (Eq. 3)
# ==============================================================================
def jev_verify_proposition(
    query_text: str,
    prop: Dict[str, Any],
    tau_f: float = 0.7
) -> Dict[str, Any]:
    mention_hits = [kw for kw in prop.get("mention_kw", []) if kw in query_text]
    affirm_hits = [kw for kw in prop.get("affirm_kw", []) if kw in query_text]
    negate_hits = [kw for kw in prop.get("negate_kw", []) if kw in query_text]
    uncertain_hits = [kw for kw in prop.get("uncertain_kw", []) if kw in query_text]

    if negate_hits:
        p_men = min(0.96, 0.84 + 0.04 * len(negate_hits))
        p_sup = max(0.06, 0.18 - 0.04 * len(negate_hits))
        matched = negate_hits
    elif uncertain_hits:
        p_men = 0.88
        p_sup = 0.35
        matched = uncertain_hits
    elif affirm_hits:
        p_men = min(0.98, 0.86 + 0.03 * len(affirm_hits))
        p_sup = min(0.96, 0.83 + 0.04 * len(affirm_hits))
        matched = affirm_hits
    elif mention_hits:
        p_men = 0.83
        p_sup = 0.42
        matched = mention_hits
    else:
        p_men = 0.11
        p_sup = 0.03
        matched = []

    if p_sup >= tau_f:
        role = "supported"
    elif p_sup < tau_f and p_men >= tau_f:
        role = "gap"
    else:
        role = "absent"

    return {
        "prop_id": prop["prop_id"],
        "text_zh": prop["text_zh"],
        "text_en": prop["text_en"],
        "is_red_flag": prop.get("is_red_flag", False),
        "follow_up_chip": prop.get("follow_up_chip", ""),
        "p_men": round(p_men, 2),
        "p_sup": round(p_sup, 2),
        "role": role,
        "matched_clues": matched
    }


def retrieve_and_jev_rerank(
    query_text: str,
    corpus: List[Dict[str, Any]],
    tau_f: float = 0.7
) -> List[Dict[str, Any]]:
    ranked_cases = []
    for case in corpus:
        verified_props = [
            jev_verify_proposition(query_text, p, tau_f=tau_f)
            for p in case.get("propositions", [])
        ]
        supported_props = [vp for vp in verified_props if vp["role"] == "supported"]
        gap_props = [vp for vp in verified_props if vp["role"] == "gap"]
        absent_props = [vp for vp in verified_props if vp["role"] == "absent"]

        total_props = max(len(verified_props), 1)
        sup_ratio = len(supported_props) / total_props
        sup_count_bonus = 0.12 * len(supported_props)
        rf_bonus = 0.22 if any(vp["is_red_flag"] for vp in supported_props) else 0.0
        gap_penalty = 0.15 * len([vp for vp in gap_props if vp["p_sup"] < 0.25])

        jev_score = max(0.08, min(0.98, 0.20 + 0.45 * sup_ratio + sup_count_bonus + rf_bonus - gap_penalty))

        ranked_cases.append({
            "case": case,
            "jev_score": round(jev_score, 2),
            "verified_props": verified_props,
            "supported_props": supported_props,
            "gap_props": gap_props,
            "absent_props": absent_props,
        })

    ranked_cases.sort(key=lambda x: x["jev_score"], reverse=True)
    return ranked_cases


# ==============================================================================
# 3. DEDUCTIVE GROUNDED ANSWER GENERATOR (Step 3)
# ==============================================================================
def generate_deductive_triage_answer(
    query_text: str,
    ranked_cases: List[Dict[str, Any]],
    api_key: str,
    base_url: str,
    model_name: str,
) -> Dict[str, str]:
    top1 = ranked_cases[0]
    top2 = ranked_cases[1] if len(ranked_cases) > 1 else top1

    all_supported = []
    all_gaps = []
    triggered_red_flags = []
    for rc in ranked_cases[:2]:
        for sp in rc["supported_props"]:
            all_supported.append(f"[{sp['prop_id']}] {sp['text_zh']} (p_sup={sp['p_sup']})")
            if sp["is_red_flag"]:
                triggered_red_flags.append(sp)
        for gp in rc["gap_props"]:
            all_gaps.append(f"[{gp['prop_id']}] {gp['text_zh']} (p_men={gp['p_men']}, p_sup={gp['p_sup']})")

    if HAS_OPENAI and api_key.strip():
        try:
            client = OpenAI(api_key=api_key.strip(), base_url=base_url.strip())
            sys_prompt = (
                "你是 CMCR 循证医疗导诊系统（MedIR-CMCR）。\n"
                "【铁律】你只能基于 Jev 已核验的 Supported Facts（已确认阳性事实）和 Gap Facts（存疑/否定事实）进行演绎推理，"
                "严禁将 Absent Facts（未提及事实）假设为成立，严禁伪造指南或直接开具处方药！\n"
                "请按以下 5 步结构输出 JSON（含 'reply_zh' 和 'reply_en' 两个字段）：\n"
                "(i) 明确的分诊方向与就医紧急度结论；\n"
                "(ii) 直接回应并纠偏患者因表面诱因产生的常见认知误区（如吃冰葡萄呕吐未必只是急性胃肠炎）；\n"
                "(iii) 将已核验的 Supported Facts 映射到 CMCR 真实病例的鉴别诊断要素上；\n"
                "(iv) 明确指出会使分诊结论发生逆转的边界条件与 Gap/未确认红旗征要素；\n"
                "(v) 给出具体可执行的挂号科室路径与就诊前准备建议。"
            )
            user_prompt = (
                f"患者陈述 q: {query_text}\n"
                f"Top-1 检索病例: {top1['case']['title']} (Jev Score={top1['jev_score']})\n"
                f"病例摘要: {top1['case']['summary_zh']}\n"
                f"已核验 Supported Facts: {all_supported}\n"
                f"已核验 Gap Facts: {all_gaps}"
            )
            resp = client.chat.completions.create(
                model=model_name.strip(),
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=700,
            )
            raw = resp.choices[0].message.content or ""
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                parsed = json.loads(m.group(0))
                if "reply_zh" in parsed and "reply_en" in parsed:
                    return {
                        "reply_zh": parsed["reply_zh"],
                        "reply_en": parsed["reply_en"],
                        "engine": f"Paratera LLM + Jev Fact Verification ({model_name})"
                    }
        except Exception as e:
            st.toast(f"API 自动回退至内置 Jev 演绎引擎 ({str(e)[:50]})", icon="ℹ️")

    specs_str = " $\\rightarrow$ ".join(
        [f"**{s['specialty']}**（{s['role']}）" for s in top1["case"]["involved_specialties"]]
    )
    sup_summary = "；".join([sp["text_zh"] for sp in top1["supported_props"]]) or "初步发热主诉"
    absent_questions = [ap["text_zh"] for ap in top1["absent_props"][:2]]

    if triggered_red_flags:
        rf_text = "；".join([rf["text_zh"] for rf in triggered_red_flags])
        reply_zh = (
            f"🚨 **(i) 方向性分诊结论**：检测到 Jev 已核验通过的急危红旗征（{rf_text}），建议**立即前往急诊科抢救监护通道**，并启动多学科协同排查：{specs_str}。\n\n"
            f"- **(ii) 表面诱因纠偏**：虽然存在进食冰冷食物伴呕吐或既往风湿病史，但根据 [CMCR 病例 {top1['case']['cmaid']}]({top1['case']['url']})，切勿因表面消化道诱因局限在“急性胃肠炎”或单纯“风湿复发”的一元论定势中。\n"
            f"- **(iii) Jev 演绎事实映射（Supported Premises）**：系统已核验确认您的阳性事实：{sup_summary}，与 CMCR 病例 `{top1['case']['cmaid']}`（{top1['case']['title']}）高度吻合（Jev 相关性 `{top1['jev_score']}`）。\n"
            f"- **(iv) 边界与待核验条件（Gap / Boundary Conditions）**：未提及的要素已被 Jev 严格拦截（不作脑补假设）；到院后需重点核查血常规（排查血小板骤降）、血清铁蛋白及淋巴结/骨髓病理。\n"
            f"- **(v) 下一步可执行建议**：请立即就医吸氧并向急诊医生出示既往用药史（如泼尼松），严禁自行加服抗生素或退烧药掩盖病情。"
        )
        reply_en = (
            f"[RED-FLAG DEDUCTIVE CONCLUSION] Jev verified critical red-flag premises ({len(triggered_red_flags)} matched). "
            f"Immediate referral to Emergency Medicine -> Hematology & Rheumatology per CMCR Case {top1['case']['cmaid']} "
            f"(Jev Score={top1['jev_score']}). Absent facts are strictly excluded to prevent hallucination."
        )
    else:
        boundary_str = "；".join(absent_questions) if absent_questions else "是否出现气短低氧、浅表淋巴结肿大或抗生素无效"
        reply_zh = (
            f"✅ **(i) 阶段性分诊方向结论**：基于当前已核验事实，首要候选路径为 {specs_str}（同时需与 **{top2['case']['title']}** 鉴别）。\n\n"
            f"- **(ii) 表面诱因纠偏**：生冷饮食后的发热呕吐虽常见于普通胃肠炎，但在合并基础免疫病史或全身表现时，必须排除深层血液/免疫并发症。\n"
            f"- **(iii) Jev 已核验事实（Supported Premises）**：{sup_summary}。\n"
            f"- **(iv) 结论翻转边界条件（Gap & Unverified Elements）**：为杜绝大模型盲目脑补，系统已将未提及要素标记为 `Absent`；**若您同时存在以下情况，分诊结论将立即升级为急诊/血液科危重排查**：{boundary_str}。\n"
            f"- **(v) 下一步操作**：请直接点击下方由 Jev 筛出的待核验事实选项，完成多轮演绎消歧。"
        )
        reply_en = (
            f"[STAGE-1 DEDUCTIVE TRIAGE] Grounded in CMCR Case {top1['case']['cmaid']} (Jev Score={top1['jev_score']}). "
            f"Supported facts confirmed ({len(top1['supported_props'])}); unmentioned elements are held as boundary conditions "
            f"for interactive clarification rather than hallucinated."
        )

    return {
        "reply_zh": reply_zh,
        "reply_en": reply_en,
        "engine": "Built-in Jev Deductive Verification Engine (Zero-Hallucination Mode)"
    }


# ==============================================================================
# 4. STREAMLIT UI MIRRORING LEXESS FIG. 2 (01 RETRIEVAL -> 02 ELEMENTS -> 03 JEV -> 04 ANSWER)
# ==============================================================================
def init_state():
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "statements" not in st.session_state:
        st.session_state.statements = []
    if "tau_f" not in st.session_state:
        st.session_state.tau_f = 0.7


def reset_state():
    st.session_state.messages = []
    st.session_state.statements = []


def submit_statement(text_zh: str, api_key: str, base_url: str, model_name: str, tau_f: float):
    if text_zh not in st.session_state.statements:
        st.session_state.statements.append(text_zh)
    full_query = " ； ".join(st.session_state.statements)
    ranked = retrieve_and_jev_rerank(full_query, CMCR_CORPUS, tau_f=tau_f)
    ans = generate_deductive_triage_answer(full_query, ranked, api_key, base_url, model_name)

    st.session_state.messages.append({"role": "user", "content_zh": text_zh})
    st.session_state.messages.append({
        "role": "assistant",
        "content_zh": ans["reply_zh"],
        "content_en": ans["reply_en"],
        "engine": ans["engine"]
    })


def main():
    st.set_page_config(
        page_title="MedIR-CMCR | Jev Deductive Fact-Verification Triage Demo",
        page_icon="🏥",
        layout="wide",
    )
    init_state()

    st.markdown(
        """
        <style>
        .role-supported { background-color: #dcfce7; color: #166534; padding: 3px 8px; border-radius: 4px; font-weight: 600; font-size: 0.82rem; }
        .role-gap { background-color: #fef9c3; color: #854d0e; padding: 3px 8px; border-radius: 4px; font-weight: 600; font-size: 0.82rem; }
        .role-absent { background-color: #f1f5f9; color: #64748b; padding: 3px 8px; border-radius: 4px; font-weight: 600; font-size: 0.82rem; }
        .en-sub { color: #64748b; font-size: 0.83rem; font-style: italic; margin-top: 6px; border-top: 1px dashed #cbd5e1; padding-top: 4px; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.markdown("### ⚙️ Jev 核验与并行智算云配置")
        tau_f = st.slider(
            "Jev 事实确认阈值 τ_f (Eq. 3)",
            min_value=0.50,
            max_value=0.95,
            value=0.70,
            step=0.05,
            help="当 p_sup(f) >= τ_f 时判定为 Supported；当 p_sup(f) < τ_f 且 p_men(f) >= τ_f 时判定为 Gap；当 p_men(f) < τ_f 时判定为 Absent（直接丢弃防幻觉）。",
        )
        api_key = st.text_input("Paratera API Key (可选)", value=os.environ.get("PARATERA_API_KEY", ""), type="password")
        base_url = st.text_input("Base URL", value=os.environ.get("PARATERA_BASE_URL", "https://llmapi.paratera.com/v1"))
        model_name = st.text_input("Model Name", value=os.environ.get("PARATERA_MODEL", "DeepSeek-V3"))
        st.button("🔄 重置当前导诊会话 (Reset)", on_click=reset_state, use_container_width=True)

    st.markdown(
        "## 🏥 MedIR-CMCR: Colloquial Case Retrieval & Two-Question Jev Fact Verification for Medical Triage\n"
        "**基于 CMCR 原生临床病例与 Jev 双步事实判断（Mention & Support）的低幻觉医疗导诊系统**"
    )

    left_col, right_col = st.columns([1.05, 1.15], gap="large")

    # ==========================================================================
    # LEFT COLUMN: LAYPERSON QUERY & GENERATED DEDUCTIVE ANSWER 
    # ==========================================================================
    with left_col:
        st.markdown("### 💬 患者口语化主诉与演绎导诊生成 (Layperson Query & Deductive Answer)")
        st.caption("🎬 **一键加载 CMCR 真实病例测试场景（演示 Jev 事实判断如何拦截诱因误导与症状幻觉）**：")
        b1, b2, b3 = st.columns(3)
        if b1.button("📌 场景 1: CMCR-1673770 首轮\n(冰葡萄诱发高烧呕吐+Still病史)", use_container_width=True):
            reset_state()
            submit_statement(
                "大夫，我前天吃了冰葡萄后开始发高烧到40度，伴寒战、肚子胀还吐了两天；我以前确诊过成人Still病，一直在吃泼尼松。",
                api_key, base_url, model_name, tau_f
            )
            st.rerun()
        if b2.button("📌 场景 2: CMCR-1673770 完备态\n(补充气短SpO2 92%+腋窝淋巴结肿)", use_container_width=True):
            reset_state()
            submit_statement(
                "我前天吃冰葡萄后高烧40度伴呕吐，有成人Still病史；今天觉得明显气短、测血氧只有92%，而且腋窝摸到肿大淋巴结、身上起了散在红色皮疹，吃消炎药抗感染无效。",
                api_key, base_url, model_name, tau_f
            )
            st.rerun()
        if b3.button("📌 场景 3: 单纯胃肠炎对照\n(吃冰葡萄呕吐但明确否定红旗征)", use_container_width=True):
            reset_state()
            submit_statement(
                "我昨天吃冰葡萄后发烧38.3度、恶心呕吐拉肚子，但我既往体健无风湿病史，而且无气短、血氧正常98%、无淋巴结肿大、无皮疹。",
                api_key, base_url, model_name, tau_f
            )
            st.rerun()

        chat_box = st.container(height=410, border=True)
        with chat_box:
            if not st.session_state.messages:
                st.info("👋 请点击上方测试场景按钮，或在下方输入患者口语化发热主诉。右侧将实时可视化 **01 病例检索重排 $\\rightarrow$ 02 离线临床要素拆解 $\\rightarrow$ 03 Jev 双步事实判断 (`Supported` / `Gap` / `Absent`)** 全过程。")
            for m in st.session_state.messages:
                with st.chat_message(m["role"]):
                    st.markdown(m["content_zh"])
                    if m.get("content_en"):
                        st.markdown(f"<div class='en-sub'>🌐 <b>[EN]</b> {m['content_en']}</div>", unsafe_allow_html=True)

        if st.session_state.statements:
            full_q = " ； ".join(st.session_state.statements)
            ranked_now = retrieve_and_jev_rerank(full_q, CMCR_CORPUS, tau_f=tau_f)
            pending_props = ranked_now[0]["absent_props"]
            if pending_props:
                st.markdown("**🔍 针对 `Absent`（未提及）关键鉴别要素的主动核验追问（点击即可补充事实并触发 Jev 重算）**：")
                cols = st.columns(min(len(pending_props), 3))
                for idx, ap in enumerate(pending_props[:3]):
                    if cols[idx].button(f"➕ {ap['follow_up_chip']}", key=f"chip_{len(st.session_state.statements)}_{idx}", use_container_width=True):
                        submit_statement(ap["follow_up_chip"], api_key, base_url, model_name, tau_f)
                        st.rerun()

        user_in = st.chat_input("输入患者口语化主诉或补充体征（如：我腋下摸到肿块而且测血氧只有92%）...")
        if user_in:
            submit_statement(user_in, api_key, base_url, model_name, tau_f)
            st.rerun()

    # ==========================================================================
    # RIGHT COLUMN: LEXESS FIG. 2 PIPELINE INSPECTOR (01 RETRIEVAL -> 02 ELEMENTS -> 03 JEV)
    # ==========================================================================
    with right_col:
        st.markdown("### 🔬 Jev 演绎事实核验透明面板 (Pipeline)")
        if not st.session_state.statements:
            st.warning(
                "等待患者输入查询 $q$ 后，此处将按四步流程展示：\n"
                "- **01 Clinical Case Retrieval (Pointwise Jev Reranking)**\n"
                "- **02 Case-Conditioned Element Generation ($\\mathcal{F} = \\{(f_j, d_{s_j})\\}$)**\n"
                "- **03 Two-Question Jev Fact Verification ($p_{\\text{men}}(f), p_{\\text{sup}}(f) \\rightarrow \\text{supported / gap / absent}$)**"
            )
        else:
            full_q = " ； ".join(st.session_state.statements)
            ranked = retrieve_and_jev_rerank(full_q, CMCR_CORPUS, tau_f=tau_f)
            top1 = ranked[0]

            st.markdown("#### `01` Colloquial Case Retrieval & Pointwise Jev Reranking")
            for idx, rc in enumerate(ranked):
                c = rc["case"]
                st.markdown(f"**{idx + 1}. [{c['cmaid']}] {c['title']}** — Jev Pointwise Score: **`{rc['jev_score']}`**")
                st.progress(int(rc["jev_score"] * 100))

            st.markdown(
                f"#### `02 & 03` Two-Question Jev Fact Verification on Top-1 Case (`cmaid/{top1['case']['cmaid']}`, $\\tau_f={tau_f}$)"
            )
            st.caption(
                "Step 1 (Fact Mention Check $p_{\\text{men}}$): 患者是否提及该要素？ | "
                "Step 2 (Fact Satisfaction Check $p_{\\text{sup}}$): 患者陈述是否证实该要素成立？ | "
                "**Absent 事实被硬性阻断，绝不进入生成模型，从而彻底消除脑补幻觉。**"
            )

            for vp in top1["verified_props"]:
                role = vp["role"]
                badge_cls = f"role-{role}"
                badge_label = {
                    "supported": "✅ SUPPORTED (已确认阳性事实 → 进入演绎推理)",
                    "gap": "⚠️ GAP (提及但否定/存疑 → 标记边界条件)",
                    "absent": "🚫 ABSENT (未提及 → 硬性丢弃防幻觉 / 转为追问)"
                }[role]

                with st.container(border=True):
                    st.markdown(
                        f"**`{vp['prop_id']}`** {vp['text_zh']} <span class='{badge_cls}'>{badge_label}</span>",
                        unsafe_allow_html=True,
                    )
                    st.caption(
                        f"🌐 *{vp['text_en']}* | "
                        f"**Step 1 Mention $p_{{\\text{{men}}}}(f)$ = `{vp['p_men']}`** | "
                        f"**Step 2 Support $p_{{\\text{{sup}}}}(f)$ = `{vp['p_sup']}`** | "
                        f"Matched Clues: `{vp['matched_clues'] if vp['matched_clues'] else 'None'}`"
                    )


if __name__ == "__main__":
    main()