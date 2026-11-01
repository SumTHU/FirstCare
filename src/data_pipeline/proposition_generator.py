# -*- coding: utf-8 -*-
"""Task 2: 离线查询无关临床事实命题拆解器（公式 2）。

将每篇 CMCR 结构化病例 d_s 拆解为 M 条（M<=100）单句、通俗、可独立核验的
原子临床命题 F = {(f_j, d_{s_j})}，写回 data/structured_cases/case_*_structured.json
的 propositions 字段（查询无关 → 可一次性离线运行并缓存）。

双模式：
1. API 模式：调用 Paratera OpenAI 兼容接口（PARATERA_DECOMPOSE_MODEL，默认
   DeepSeek-R1），System Prompt 严格采用计划书 Task 2 内置版本；
2. 确定性回退模式（无 Key）：从 clinical_record 槽位 + 生命体征 + 红旗候选
   规则化生成命题，零外部依赖，保证流水线可离线跑通。

运行：python -m src.data_pipeline.proposition_generator [--limit N] [--category X]
     [--force] [--workers N] [--no-api]
"""

import argparse
import json
import re
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.config import (  # noqa: E402
    PARATERA_BASE_URL,
    STRUCTURED_CASES_DIR,
    get_env,
    load_env,
)
from src.schemas import ClinicalProposition  # noqa: E402

# ---------------------------------------------------------------- 常量

ELEMENT_CATEGORIES = ("核心症状与诱因", "既往病史", "阳性查体", "急危红旗征", "治疗反应")

MAX_PROPS = 100  # 计划书 Task 2: M <= 100

# 计划书 Task 2 红旗征举例（正则，命中即 is_red_flag=True）
RED_FLAG_PATTERNS = [
    r"血氧饱和度|SpO2|指氧", r"休克", r"抽搐|惊厥", r"喷射(性)?呕吐",
    r"胸痛|胸闷", r"呼吸困难|喘不上气|气促", r"意识(改变|障碍|不清|丧失)|昏迷|谵妄|嗜睡",
    r"剧烈头痛|霹雳样|爆炸性|雷击样", r"咯血|呕血|便血", r"鼻出血|鼻衄",
    r"低血压|血压(下降|偏低)|收缩压.{0,6}(8[0-9]|7[0-9]|6[0-9])\s*mmHg",
    r"高热.{0,8}(40|41)(\.[0-9])?\s*℃", r"寒战", r"皮疹|瘀斑|紫癜",
    r"淋巴结(肿大)?", r"颈项强直|脑膜刺激", r"呼吸(衰竭|窘迫)|ARDS",
    r"嗜血(细胞)?(综合征|现象)?|噬血", r"多器官(功能)?(衰竭|障碍)", r"酸中毒",
    r"电解质(紊乱|严重)", r"腹膜刺激|板状腹", r"皮下气肿|纵隔气肿",
]

# 六类症候群 → 该类病例核心主诉命题必为红旗
CATEGORY_CORE_RED_FLAG = {
    "red_flag_chest_pain": "胸痛",
    "red_flag_dyspnea": "呼吸困难",
    "red_flag_consciousness": "意识",
    "red_flag_headache": "头痛",
}

SYSTEM_PROMPT = """你是一名严谨的临床医学知识工程专家。给定一篇《中国临床案例成果数据库 (CMCR)》的结构化病例报告 d_s，请将其拆解为决定该病例鉴别诊断、排除误诊定势及触发急诊预警所需的原子化临床事实命题集合 F = {f_1, f_2, ..., f_m}。
要求：
1. 每条命题 f_j 必须改写为单句、面向普通患者可核验的陈述句（包含中文 proposition_zh 与英文 proposition_en）；
2. 必须覆盖 5 个维度：(a) 核心症状与起病诱因；(b) 关键既往史与用药史；(c) 特征性体表查体（如浅表淋巴结、特征性皮疹）；(d) 危及生命的急诊红旗征（如 SpO2 <= 92%、休克、抽搐、喷射呕吐、胸痛、呼吸困难、意识改变、剧烈头痛）；(e) 常规经验性治疗反应（如抗生素无效）；
3. 为每条命题配套生成 mention_keywords、affirm_keywords、negate_keywords、uncertain_keywords 以及当该命题未被患者提及时用于主动追问的 clarification_question_zh 和快捷按钮短语 follow_up_chip；
4. 不要合并不同维度的复合命题，仅对完全同义的命题去重。输出严格符合 ClinicalProposition 列表的 JSON 格式。
语言规范（必须严格遵守）：
5. proposition_zh、clarification_question_zh、follow_up_chip 及所有 *_keywords 必须为纯中文，禁止夹杂任何英文单词或英文缩写；医学计量单位（℃、mmHg、%）和个别公认医学缩写（如 CT、ICU、SpO2）允许保留，其余英文一律翻译为中文（如"出汗"而非 accompanying sweating）；
6. 仅 proposition_en 字段使用英文。"""

# 输出 JSON schema 说明（附在 user 消息尾部，约束 API 返回结构）
_JSON_SPEC = """{"propositions":[{"element_category":"核心症状与诱因|既往病史|阳性查体|急危红旗征|治疗反应","proposition_zh":"...","proposition_en":"...","mention_keywords":["..."],"affirm_keywords":["..."],"negate_keywords":["..."],"uncertain_keywords":["..."],"is_red_flag":false,"clarification_question_zh":"...","follow_up_chip":"..."}]}"""


# ---------------------------------------------------------------- 工具函数

def _has_red_flag(text: str) -> bool:
    return any(re.search(p, text) for p in RED_FLAG_PATTERNS)


def _mk_prop(source_cmaid: str, category: str, zh: str, *,
             en: str = "", mention: Optional[List[str]] = None,
             affirm: Optional[List[str]] = None, negate: Optional[List[str]] = None,
             uncertain: Optional[List[str]] = None, red_flag: Optional[bool] = None,
             clarify: str = "", chip: str = "") -> Optional[Dict[str, Any]]:
    """构造一条命题 dict；空/超长/非单句强校验，失败返回 None。"""
    zh = re.sub(r"\s+", "", (zh or "").strip().strip("。．."))
    if not (8 <= len(zh) <= 120):
        return None
    # 单句约束：句中出现句号/问号/分号视为复合命题 → 截断到第一个句界
    for sep in ("。", "；", ";", "？", "?", "！", "!"):
        if sep in zh:
            zh = zh.split(sep)[0]
    if len(zh) < 8:
        return None
    if red_flag is None:
        red_flag = _has_red_flag(zh)
    return {
        "element_category": category if category in ELEMENT_CATEGORIES else "核心症状与诱因",
        "proposition_zh": zh,
        "proposition_en": (en or "").strip(),
        "mention_keywords": list(mention or []),
        "affirm_keywords": list(affirm or []),
        "negate_keywords": list(negate or []),
        "uncertain_keywords": list(uncertain or []),
        "is_red_flag": bool(red_flag),
        "clarification_question_zh": clarify.strip(),
        "follow_up_chip": chip.strip()[:12],
    }


def _first_sentence(text: str, max_len: int = 100) -> str:
    text = re.sub(r"\s+", "", text or "")
    m = re.match(r"(.{8,%d}?[。；;])" % max_len, text)
    if m:
        return m.group(1).rstrip("。；;")
    return text[:max_len]


def _split_sentences(text: str) -> List[str]:
    parts = re.split(r"[。；;！？\n]", re.sub(r"\s+", "", text or ""))
    return [p for p in (s.strip("，,、：:（）() ") for s in parts) if len(p) >= 8]


def _ask_template(zh: str) -> str:
    """从命题生成追问话术与胶囊短语。"""
    m = re.search(r"(?:患者|病人)?(?:出现|伴有|存在|有)?(.{2,18}?)(?:症状|表现|情况|体征)?(?:达|为|超过|最高)?", zh)
    core = m.group(1) if m and len(m.group(1)) >= 2 else zh[:12]
    return f"请问您（或患者）是否出现过「{core}」的情况？", core[:10]


# ---------------------------------------------------------------- 确定性回退拆解

def decompose_deterministic(case: Dict[str, Any], category: str) -> List[Dict[str, Any]]:
    """无 API Key 时的规则化拆解：槽位 → 命题。仅产自病例原文，零幻觉。"""
    cr = case.get("clinical_record", {})
    vitals = case.get("extracted_vital_signs", {})
    props: List[Dict[str, Any]] = []
    gen = case.get("cmaid")

    def add(category_, zh, **kw):
        p = _mk_prop(gen, category_, zh, **kw)
        if p and p["proposition_zh"] not in {q["proposition_zh"] for q in props}:
            props.append(p)

    general = cr.get("patient_general_information", "")
    # (a) 核心症状与起病诱因：取一般资料前两句（含主诉与诱因）
    for i, sent in enumerate(_split_sentences(general)[:3]):
        clarify, chip = _ask_template(sent)
        add("核心症状与诱因", sent, mention=[re.sub(r"[^\u4e00-\u9fa5]", "", sent)[:6]],
            affirm=[w for w in ("发热", "腹痛", "胸痛", "呼吸困难", "呕吐", "腹泻", "头痛", "咳嗽") if w in sent],
            negate=[w + "的" for w in ("无", "否认", "未见") if w in sent] or None,
            clarify=clarify, chip=chip)

    # (b) 既往史：诊断与鉴别/讨论段中的既往史线索由一般资料承接，规则扫描全文
    full_text = " ".join(str(v) for v in cr.values())
    for kw in ("高血压", "糖尿病", "冠心病", "肝炎", "结核", "肾病", "肿瘤", "手术史", "药物过敏"):
        if kw in full_text:
            clarify, chip = _ask_template(f"既往患有{kw}相关疾病")
            add("既往病史", f"患者既往有{kw}相关病史", mention=[kw], affirm=[kw],
                negate=["否认" + kw, "无" + kw], uncertain=["不详"],
                clarify=clarify, chip=kw)

    # (c) 阳性查体：体表查体特征词
    for kw in ("淋巴结肿大", "皮疹", "瘀斑", "紫癜", "巩膜黄染", "腹部压痛", "反跳痛",
               "腹膜刺激征", " Murphy", "肺部湿啰音", "下肢水肿"):
        if kw.strip() in full_text:
            clarify, chip = _ask_template(f"查体发现{kw.strip()}")
            add("阳性查体", f"医生查体时发现{kw.strip()}", mention=[kw.strip()],
                affirm=[kw.strip()], negate=["未见" + kw.strip()[:4]],
                clarify=clarify, chip=kw.strip()[:10])

    # (d) 急危红旗征：数值生命体征 + 红旗候选词
    if vitals.get("spo2_percent") is not None and float(vitals["spo2_percent"]) <= 92:
        clarify, chip = _ask_template("血氧饱和度下降至92%及以下")
        add("急危红旗征", f"血氧饱和度降至{vitals['spo2_percent']}%", mention=["血氧", "指氧"],
            affirm=["血氧低", "喘"], red_flag=True, clarify=clarify, chip="血氧/呼吸")
    if vitals.get("systolic_bp_mmhg") is not None and int(vitals["systolic_bp_mmhg"]) < 90:
        add("急危红旗征", f"收缩压低至{vitals['systolic_bp_mmhg']}mmHg，接近休克血压",
            mention=["血压"], affirm=["头晕", "乏力"], red_flag=True,
            clarify="请问最近有没有测量过血压？结果大概是多少？", chip="血压")
    if vitals.get("temperature_c") is not None and float(vitals["temperature_c"]) >= 39.5:
        add("核心症状与诱因", f"体温最高达{vitals['temperature_c']}℃", mention=["体温", "发热"],
            affirm=["发热", "烧"], clarify="发烧时最高体温是多少度？", chip="最高体温")
    for rf in case.get("red_flag_candidates", []):
        clarify, chip = _ask_template(f"出现{rf}表现")
        add("急危红旗征", f"病例中出现{rf}这一危急表现", mention=[rf], affirm=[rf],
            red_flag=True, clarify=clarify, chip=rf[:10])
    # 症候群类别核心主诉强制红旗
    core_kw = CATEGORY_CORE_RED_FLAG.get(category)
    if core_kw:
        for p in props:
            if p["element_category"] == "核心症状与诱因" and core_kw in p["proposition_zh"]:
                p["is_red_flag"] = True

    # (e) 治疗反应：治疗/转归槽位首句
    for slot in ("treatment_course_and_regimen", "clinical_outcome_and_follow_up"):
        sent = _first_sentence(cr.get(slot, ""))
        if sent and re.search(r"给予|治疗|方案|抗生素|抗感染|退热|好转|无效", sent):
            add("治疗反应", sent, mention=["治疗", "用药"], affirm=["有效", "好转"],
                negate=["无效"], uncertain=["不明确"])

    # (f) 最终诊断保真命题（供 Stage 3 导诊与 Jev 支撑）
    dx_text = cr.get("diagnosis_and_differential", "")
    m = re.search(r"(?:考虑诊断|最终诊断|临床诊断|出院诊断|确定诊断)[：:]([^\n]{4,60})", dx_text)
    if m:
        for dx in re.split(r"[、，,]", m.group(1))[:3]:
            dx = dx.strip(" 。；;，")
            if len(dx) >= 3:
                add("核心症状与诱因", f"医生最终诊断考虑{dx}，诊断依据来自相关检查",
                    mention=[dx[:4]], affirm=[dx[:4]], red_flag=_has_red_flag(dx))
    return props[:MAX_PROPS]


# ---------------------------------------------------------------- API 拆解

def _build_case_digest(case: Dict[str, Any]) -> str:
    """将结构化病例压缩为 LLM 输入文本（槽位原文，控制 token）。"""
    cr = case.get("clinical_record", {})
    parts = [f"标题：{case.get('title', '')}"]
    ab = case.get("structured_abstract") or {}
    if ab:
        parts.append("结构化摘要：" + json.dumps(ab, ensure_ascii=False))
    for k, v in cr.items():
        t = re.sub(r"\s+", " ", str(v))[:1800]
        parts.append(f"[{k}] {t}")
    vs = case.get("extracted_vital_signs") or {}
    if vs:
        parts.append("生命体征：" + json.dumps(vs, ensure_ascii=False))
    return "\n".join(parts)[:9000]


def decompose_via_api(case: Dict[str, Any], env: Dict[str, str], timeout: int = 600) -> Tuple[
        Optional[List[Dict[str, Any]]], str]:
    """调用 Paratera OpenAI 兼容接口拆解。返回 (props|None, err_msg)。"""
    try:
        from openai import OpenAI  # noqa: F401
    except ImportError:
        return None, "openai 库未安装"
    api_key = get_env("PARATERA_API_KEY", "")
    model = get_env("PARATERA_DECOMPOSE_MODEL", "kimi-k3.1-pro")
    if not api_key:
        return None, "未配置 PARATERA_API_KEY"
    import httpx
    from openai import OpenAI
    # 沙箱/系统代理(127.0.0.1)会挂起长 POST，强制直连
    http = httpx.Client(trust_env=False, timeout=timeout)
    client = OpenAI(base_url=get_env("PARATERA_BASE_URL_OVERRIDE") or PARATERA_BASE_URL,
                    api_key=api_key, timeout=timeout, max_retries=1, http_client=http)
    user_msg = _build_case_digest(case) + f"\n\n请输出符合以下 JSON 结构的对象（不要输出其他内容）：\n{_JSON_SPEC}"
    try:
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": SYSTEM_PROMPT},
                          {"role": "user", "content": user_msg}],
                temperature=0.2,
            )
        except Exception as temp_exc:  # noqa: BLE001
            # Kimi-K3.1 等推理模型不支持 temperature 参数，去掉后重试一次
            if "temperature" in str(temp_exc).lower():
                resp = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": SYSTEM_PROMPT},
                              {"role": "user", "content": user_msg}],
                )
            else:
                raise
        msg = resp.choices[0].message
        raw = msg.content or getattr(msg, "reasoning_content", "") or ""
    except Exception as exc:  # noqa: BLE001
        return None, f"API 调用失败: {exc}"
    return _parse_llm_json(raw)


def _parse_llm_json(raw: str) -> Tuple[Optional[List[Dict[str, Any]]], str]:
    """从 LLM 返回中鲁棒提取 propositions JSON。"""
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S).strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    m = re.search(r"\{.*\}", raw, flags=re.S)
    if not m:
        return None, "返回中未找到 JSON 对象"
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None, "JSON 解析失败"
    items = data.get("propositions") if isinstance(data, dict) else data
    if not isinstance(items, list) or not items:
        return None, "propositions 为空"
    return items, ""


# 中文字段允许保留的公认医学缩写（其余英文一律剔除）
_ZH_ALLOWED_ABBR = {"CT", "ICU", "MRI", "PET", "ECG", "SPO2", "CRRT", "ECMO",
                    "PICC", "NICU", "CCU", "ERCP", "CRP", "PCT", "WBC", "NPCR"}


def _strip_english(zh: str) -> str:
    """中文字段保险清洗：剔除英文单词（放行公认医学缩写），清理残留空格。"""
    if not zh:
        return zh
    def repl(m: "re.Match[str]") -> str:
        w = m.group(0)
        return w if w.upper() in _ZH_ALLOWED_ABBR else ""
    out = re.sub(r"[A-Za-z][A-Za-z0-9]*", repl, zh)
    out = re.sub(r"\s+([，。、：；,.;:)）]|$)", r"\1", out)   # 去标点前空格
    out = re.sub(r"(\()[\s，、]+", r"\1", out)                # 去括号内残留
    out = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", out)  # 去中文间空格
    out = re.sub(r"\s{2,}", " ", out)
    return out.strip(" ，,")


def normalize_props(raw_items: List[Dict[str, Any]], source_cmaid: str) -> List[Dict[str, Any]]:
    """LLM 原始命题 → 中文字段去英文 → 契约校验、去重、截断。"""
    props: List[Dict[str, Any]] = []
    seen: set = set()
    for it in raw_items[:MAX_PROPS * 2]:
        if not isinstance(it, dict):
            continue
        p = _mk_prop(
            source_cmaid,
            _strip_english(str(it.get("element_category", ""))),
            _strip_english(str(it.get("proposition_zh", ""))),
            en=str(it.get("proposition_en", "") or ""),
            mention=[_strip_english(str(x)) for x in (it.get("mention_keywords") or [])][:6],
            affirm=[_strip_english(str(x)) for x in (it.get("affirm_keywords") or [])][:6],
            negate=[_strip_english(str(x)) for x in (it.get("negate_keywords") or [])][:6],
            uncertain=[_strip_english(str(x)) for x in (it.get("uncertain_keywords") or [])][:6],
            red_flag=it.get("is_red_flag"),
            clarify=_strip_english(str(it.get("clarification_question_zh", "") or "")),
            chip=_strip_english(str(it.get("follow_up_chip", "") or "")),
        )
        if not p:
            continue
        # 红旗双保险：LLM 漏标时按模式补
        if not p["is_red_flag"] and _has_red_flag(p["proposition_zh"]):
            p["is_red_flag"] = True
        if p["proposition_zh"] in seen:
            continue
        seen.add(p["proposition_zh"])
        props.append(p)
        if len(props) >= MAX_PROPS:
            break
    return props


# ---------------------------------------------------------------- 批处理主流程

def load_category_index() -> Dict[str, str]:
    idx_path = STRUCTURED_CASES_DIR.parent / "raw_cmcr" / "case_pool_index.json"
    if not idx_path.exists():
        return {}
    data = json.loads(idx_path.read_text(encoding="utf-8"))
    return {c["cmaid"]: c["category"] for c in data.get("cases", [])}


def process_case(case_path: Path, env: Dict[str, str], use_api: bool,
                 cat_index: Dict[str, str]) -> Dict[str, Any]:
    """处理单例：拆解 + 校验 + 写回。返回统计记录。"""
    case = json.loads(case_path.read_text(encoding="utf-8"))
    cmaid = case.get("cmaid") or re.search(r"case_(\d+)_structured", case_path.name).group(1)
    category = cat_index.get(cmaid, "")
    rec: Dict[str, Any] = {"cmaid": cmaid, "mode": None, "n_props": 0,
                           "n_red_flag": 0, "error": None}

    api_props, err = (decompose_via_api(case, env) if use_api else (None, "API 模式关闭"))
    if api_props:
        props = normalize_props(api_props, cmaid)
        rec["mode"] = "api"
        if len(props) < 5:  # API 产出过少 → 回退兜底
            props = decompose_deterministic(case, category)
            rec["mode"] = "api+fallback"
    else:
        if use_api:
            rec["error"] = f"API 失败回退: {err}"
        props = decompose_deterministic(case, category)
        rec["mode"] = "deterministic"

    # Pydantic 契约校验后写回
    validated = []
    for p in props:
        p["prop_id"] = f"F_{cmaid}_{len(validated) + 1:02d}"
        p["source_cmaid"] = cmaid
        try:
            validated.append(ClinicalProposition(**p).model_dump())
        except Exception:  # noqa: BLE001
            continue
    case["propositions"] = validated
    case_path.write_text(json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8")
    rec["n_props"] = len(validated)
    rec["n_red_flag"] = sum(1 for p in validated if p["is_red_flag"])
    return rec


def main() -> None:
    parser = argparse.ArgumentParser(description="Task 2 命题拆解器")
    parser.add_argument("--limit", type=int, default=None, help="仅处理前 N 例（试跑）")
    parser.add_argument("--category", default=None,
                        help="仅处理指定症候群类别（fever/abdominal_pain/red_flag_*）")
    parser.add_argument("--force", action="store_true", help="已有命题的病例也重跑")
    parser.add_argument("--workers", type=int, default=4, help="API 模式并发数")
    parser.add_argument("--no-api", action="store_true", help="强制确定性回退模式")
    args = parser.parse_args()

    env = load_env()
    use_api = (not args.no_api) and bool(get_env("PARATERA_API_KEY", ""))
    if use_api:
        try:
            import openai  # noqa: F401
        except ImportError:
            print("[提示] openai 库未安装，自动切换确定性回退模式")
            use_api = False

    cat_index = load_category_index()
    paths = sorted(STRUCTURED_CASES_DIR.glob("case_*_structured.json"))
    if args.category:
        paths = [p for p in paths if cat_index.get(
            re.search(r"case_(\d+)_structured", p.name).group(1), "") == args.category]
    if not args.force:
        paths = [p for p in paths if "propositions" not in json.loads(
            p.read_text(encoding="utf-8")) or not json.loads(
            p.read_text(encoding="utf-8")).get("propositions")]
    if args.limit:
        paths = paths[:args.limit]
    if not paths:
        print("没有需要处理的病例（全部已缓存？可用 --force 重跑）")
        return

    print(f"=== Task 2 命题拆解 ===\n模式: {'API(' + get_env('PARATERA_DECOMPOSE_MODEL', 'DeepSeek-R1') + ')' if use_api else '确定性回退'}"
          f" | 待处理: {len(paths)} 例 | 并发: {args.workers if use_api else 1}")
    t0 = time.time()
    records: List[Dict[str, Any]] = []
    if use_api:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(process_case, p, env, True, cat_index): p for p in paths}
            for i, fut in enumerate(as_completed(futures), 1):
                rec = fut.result()
                records.append(rec)
                if i % 20 == 0 or i == len(paths):
                    print(f"  进度 {i}/{len(paths)} | 最近: {rec['cmaid']} "
                          f"props={rec['n_props']} red={rec['n_red_flag']} {rec['error'] or ''}")
    else:
        for i, p in enumerate(paths, 1):
            rec = process_case(p, env, False, cat_index)
            records.append(rec)
            if i % 50 == 0 or i == len(paths):
                print(f"  进度 {i}/{len(paths)} | 最近: {rec['cmaid']} "
                      f"props={rec['n_props']} red={rec['n_red_flag']}")

    # 汇总报告
    n_api = sum(1 for r in records if r["mode"].startswith("api"))
    n_err = sum(1 for r in records if r["error"])
    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "api" if use_api else "deterministic",
        "total": len(records),
        "api_used": n_api,
        "fallback": len(records) - n_api,
        "errors": n_err,
        "total_props": sum(r["n_props"] for r in records),
        "total_red_flag": sum(r["n_red_flag"] for r in records),
        "avg_props": round(sum(r["n_props"] for r in records) / max(len(records), 1), 1),
        "over_limit": [r["cmaid"] for r in records if r["n_props"] > MAX_PROPS],
        "records": records,
    }
    out = STRUCTURED_CASES_DIR / "proposition_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== 完成 === 耗时 {time.time() - t0:.0f}s | 命题总数 {report['total_props']} "
          f"(平均 {report['avg_props']}/例) | 红旗命题 {report['total_red_flag']} "
          f"| API {n_api} / 回退 {report['fallback']} / 错误 {n_err}")
    print(f"报告: {out}")


if __name__ == "__main__":
    main()
