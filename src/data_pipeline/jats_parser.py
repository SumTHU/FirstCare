# -*- coding: utf-8 -*-
"""JATS XML → CMCR 结构化病例库解析器（Task 1 升级版）。

输入：data/raw_cmcr/case_<cmaid>_article.xml（CMCR SSR 页面内嵌的完整 JATS 全文）
输出：data/structured_cases/case_<cmaid>_structured.json（对齐标杆 cmcr_case_1673770_structured.json）
     data/structured_cases/parser_report.json（批量解析质量与完整性报告）

设计原则：
- 零预设科室：科室仅从摘要「推荐阅读人群」与正文救治链派生；
- 防幻觉完整性校验：缺【主诉/最终诊断/鉴别诊断】任一核心小节 → status=incomplete，
  不入结构化病例库（仅记录在 report），与计划书 Task 1 的 ValueError 约束等价但批量安全；
- 逐字保留原生小节树（native_sections），启发式映射（clinical_record）只作索引层，绝不改写原文。
"""

import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.config import RAW_CMCR_DIR, STRUCTURED_CASES_DIR
from src.schemas import CMCRNativeCase

# 红旗征候选关键词（仅作标记候选，最终由医学同学核验）
RED_FLAG_CANDIDATE_KEYWORDS = [
    "休克", "嗜血", "低氧", "血氧饱和度", "鼻衄", "鼻出血", "抽搐",
    "意识障碍", "昏迷", "粒细胞缺乏", "血小板减少", "呼吸衰竭", "多器官功能",
]

VITAL_PATTERNS = {
    "temperature_c": re.compile(r"体温[^0-9。；;]{0,8}(\d{2}(?:\.\d+)?)\s*℃"),
    "spo2_percent": re.compile(r"血氧饱和度[^0-9。；;]{0,8}(\d{2}(?:\.\d+)?)\s*[%％]"),
    "heart_rate_bpm": re.compile(r"心率[^0-9。；;]{0,8}(\d{2,3})\s*次/min"),
    "respiratory_rate_bpm": re.compile(r"呼吸[^0-9。；;]{0,8}(\d{1,3})\s*次/min"),
    "blood_pressure": re.compile(r"血压[^0-9。；;]{0,8}(\d{2,3})\s*/\s*(\d{2,3})\s*mmHg"),
}

_ABSTRACT_SECTION_MAP = {
    "病史摘要": "history_summary",
    "症状体征": "symptoms_and_signs",
    "诊断方法": "diagnosis_method",
    "治疗方法": "treatment_method",
    "临床转归": "clinical_outcome",
    "推荐阅读人群": "recommended_readers",
}

# 正文小节标题关键词 → clinical_record 槽位
_SECTION_SLOT_MAP = [
    ("一般资料", "patient_general_information"),
    ("检查", "auxiliary_examinations"),
    ("诊断与鉴别诊断", "diagnosis_and_differential"),
    ("鉴别诊断", "differential_diagnosis"),
    ("治疗结果", "clinical_outcome_and_follow_up"),
    ("随访", "clinical_outcome_and_follow_up"),
    ("转归", "clinical_outcome_and_follow_up"),
    ("治疗", "treatment_course_and_regimen"),
    ("讨论", "discussion_and_clinical_pearls"),
    ("诊疗经过", "treatment_course_and_regimen"),
]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _element_text(elem: Optional[ET.Element]) -> str:
    """递归取元素全部文本（含内联标签），并压缩空白。"""
    if elem is None:
        return ""
    return _clean("".join(elem.itertext()))


def _sec_to_tree(sec: ET.Element) -> Dict[str, Any]:
    """把 <sec> 递归转成 {title, text, children} 树。"""
    title = _element_text(sec.find("title"))
    parts: List[str] = []
    for child in sec:
        if child.tag == "title":
            continue
        if child.tag == "sec":
            continue
        t = _element_text(child)
        if t:
            parts.append(t)
    children = [_sec_to_tree(sub) for sub in sec.findall("sec")]
    return {"title": title, "text": " ".join(parts), "children": children}


def _flatten_secs(secs: List[Dict[str, Any]], depth: int = 0) -> List[Tuple[str, str, int]]:
    """展开 (标题, 文本, 层级) 列表，便于关键词槽位匹配。"""
    out: List[Tuple[str, str, int]] = []
    for s in secs:
        out.append((s["title"], s["text"], depth))
        out.extend(_flatten_secs(s["children"], depth + 1))
    return out


def _extract_vitals(full_text: str) -> Dict[str, Any]:
    vitals: Dict[str, Any] = {}
    m = VITAL_PATTERNS["blood_pressure"].search(full_text)
    if m:
        vitals["systolic_bp_mmhg"] = int(m.group(1))
        vitals["diastolic_bp_mmhg"] = int(m.group(2))
    for key in ("temperature_c", "spo2_percent", "heart_rate_bpm", "respiratory_rate_bpm"):
        m = VITAL_PATTERNS[key].search(full_text)
        if m:
            val = float(m.group(1))
            vitals[key] = int(val) if val == int(val) else val
    return vitals


def _extract_final_diagnoses(diag_text: str) -> List[str]:
    """从诊断小节抽取最终诊断列表（启发式，原文保留在 native_sections）。"""
    diagnoses: List[str] = []
    for m in re.finditer(
        r"(?:考虑诊断|最终诊断|临床诊断|确定诊断|入院诊断|出院诊断|明确诊断|诊断为|确诊为|诊断)[：:]?\s*(?:为)?\s*([^\n。；;]{2,80})",
        diag_text,
    ):
        seg = m.group(1)
        for piece in re.split(r"[、，,；;]", seg):
            piece = piece.strip(" 。；;，,")
            if piece and piece not in diagnoses:
                diagnoses.append(piece)
    return diagnoses[:20]


def parse_case(xml_path: Path) -> Optional[Dict[str, Any]]:
    """解析单个 JATS XML 为标杆兼容的结构化病例 dict（未经 schema 校验的原始产物）。"""
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError as exc:
        print(f"  [XML解析失败] {xml_path.name}: {exc}")
        return None

    front = root.find("front")
    am = front.find("article-meta") if front is not None else None
    if am is None:
        return None

    def article_id(pid_type: str) -> str:
        el = am.find(f"article-id[@pub-id-type='{pid_type}']")
        return (el.text or "").strip() if el is not None else ""

    cmaid = article_id("cma-id") or re.search(r"case_(\d+)_article", xml_path.name).group(1)
    doi = article_id("doi") or None

    title = _element_text(am.find("title-group/article-title"))
    title_en = _element_text(am.find("title-group/trans-article-title"))

    authors: List[str] = []
    for contrib in am.findall("contrib-group/contrib"):
        name = contrib.find("name")
        if name is None:
            sn = contrib.find("string-name")
            if sn is not None and _element_text(sn):
                authors.append(_element_text(sn))
            continue
        surname = _element_text(name.find("surname"))
        given = _element_text(name.find("given-names"))
        full = (surname + given).strip()
        if full:
            authors.append(full)

    keywords = [_element_text(k) for k in am.findall("kwd-group/kwd") if _element_text(k)]

    pub_year = None
    for pd in am.findall("pub-date"):
        y = _element_text(pd.find("year"))
        if y:
            pub_year = y
            break

    # ---- 结构化摘要 ----
    structured_abstract: Dict[str, str] = {}
    recommended_specialties: List[Dict[str, str]] = []
    ab = am.find("abstract")
    if ab is not None:
        for sec in ab.findall("sec"):
            t = _element_text(sec.find("title"))
            body = " ".join(_element_text(p) for p in sec.findall("p"))
            key = _ABSTRACT_SECTION_MAP.get(t, t or "abstract")
            structured_abstract[key] = body
            if t == "推荐阅读人群" and body:
                for sp in re.split(r"[；;，,、\s]+", body):
                    sp = sp.strip(" 。；;，,")
                    if sp:
                        recommended_specialties.append(
                            {"specialty": sp, "role": "病例推荐阅读人群（多学科救治链参考）"}
                        )

    # ---- 正文原生小节树 + 槽位映射 ----
    body = root.find("body")
    native_secs = [_sec_to_tree(s) for s in body.findall("sec")] if body is not None else []
    flat = _flatten_secs(native_secs)

    clinical_record: Dict[str, Any] = {}
    for sec_title, sec_text, _depth in flat:
        if not sec_title:
            continue
        for kw, slot in _SECTION_SLOT_MAP:
            if kw in sec_title and slot not in clinical_record:
                clinical_record[slot] = sec_text
                break

    # 诊断与鉴别诊断细分
    diag_text = clinical_record.get("diagnosis_and_differential", "") or ""
    diff_sec_text = clinical_record.get("differential_diagnosis", "")
    final_diagnoses = _extract_final_diagnoses(diag_text)

    full_text = _element_text(root.find("body"))
    abstract_text = " ".join(structured_abstract.values())
    search_text = abstract_text + " " + full_text

    # 主诉（一般资料小节优先，回退全文）；兼容「主诉：」「主诉为“…”」等措辞
    general_text = clinical_record.get("patient_general_information", "")
    chief_m = re.search(r"主诉[：:]?(?:为)?\s*[\"““]?([^\n。\"”]{2,60})[\"”]?", general_text)
    if not chief_m:
        chief_m = re.search(r"(?:因|以)[\"“]?([^\"”，。；;]{2,40})[\"”]?(?:为|的)[^。]{0,10}(?:就诊|入院|来院|收入)", general_text)
    if not chief_m:
        chief_m = re.search(r"主诉[：:]?(?:为)?\s*[\"““]?([^\n。\"”]{2,60})[\"”]?", search_text)
    chief_complaint = chief_m.group(1).strip() if chief_m else ""

    vitals = _extract_vitals(search_text)
    red_flags = [kw for kw in RED_FLAG_CANDIDATE_KEYWORDS if kw in search_text]

    spo2 = vitals.get("spo2_percent")
    if spo2 is not None and spo2 <= 92.0 and "低氧" not in red_flags:
        red_flags.append("低氧")

    # ---- 防幻觉完整性校验 ----
    # 主诉允许隐含在结构化摘要「病史摘要」中（CMCR 大量病例无显式"主诉"标签）
    has_chief = bool(chief_complaint) or ("主诉" in search_text) or bool(
        structured_abstract.get("history_summary")
    )
    # 最终诊断允许多种措辞（诊断为/确诊为/考虑诊断/明确诊断等）
    has_final_dx = bool(final_diagnoses) or bool(
        re.search(r"确诊为|诊断为|考虑诊断|明确诊断|最终诊断|临床诊断|确定诊断", search_text)
    )
    has_diff = bool(diff_sec_text) or ("鉴别诊断" in search_text) or ("鉴别" in diag_text)
    missing = [name for name, ok in [
        ("主诉", has_chief), ("最终诊断", has_final_dx), ("鉴别诊断", has_diff),
    ] if not ok]
    status = "ok" if not missing else "incomplete"

    is_oa = ("CC-BY-NC-ND" in (full_text + abstract_text)) or any(
        "开放" in (structured_abstract.get(k) or "") for k in structured_abstract
    )

    return {
        "cmaid": cmaid,
        "doi": doi,
        "title": title,
        "title_en": title_en,
        "url": f"https://rs.yiigle.com/cmaid/{cmaid}",
        "authors": authors,
        "keywords": keywords,
        "pub_year": pub_year,
        "is_open_access": is_oa,
        "structured_abstract": structured_abstract,
        "clinical_record": clinical_record,
        "native_sections": native_secs,
        "extracted_vital_signs": vitals,
        "red_flag_candidates": red_flags,
        "involved_specialties": recommended_specialties,
        "final_diagnoses": final_diagnoses,
        "chief_complaint": chief_complaint,
        "completeness": {
            "status": status,
            "missing_core_sections": missing,
            "has_chief_complaint": has_chief,
            "has_final_diagnosis": has_final_dx,
            "has_differential_diagnosis": has_diff,
        },
        "provenance": {
            "source_xml": xml_path.name,
            "parsed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "parser": "src/data_pipeline/jats_parser.py v1",
        },
    }


def to_native_case(parsed: Dict[str, Any]) -> CMCRNativeCase:
    """把解析产物收敛为 Pydantic 契约（计划书 §3.1 CMCRNativeCase）。"""
    return CMCRNativeCase(
        cmaid=parsed["cmaid"],
        doi=parsed.get("doi"),
        title=parsed["title"],
        url=parsed["url"],
        reporting_institution="中国临床案例成果数据库 (CMCR) 收录病例",
        reporting_department="",
        authors=parsed.get("authors", []),
        keywords=parsed.get("keywords", []),
        pub_year=parsed.get("pub_year"),
        is_open_access=bool(parsed.get("is_open_access")),
        structured_abstract=parsed.get("structured_abstract", {}),
        clinical_record=parsed.get("clinical_record", {}),
        native_sections=parsed.get("native_sections", []),
        extracted_vital_signs=parsed.get("extracted_vital_signs", {}),
        red_flag_candidates=parsed.get("red_flag_candidates", []),
        involved_specialties=parsed.get("involved_specialties", []),
        propositions=[],  # Task 2 (proposition_generator) 填充
        completeness=parsed.get("completeness", {}),
        provenance=parsed.get("provenance", {}),
    )


def batch_parse(max_files: Optional[int] = None) -> Dict[str, Any]:
    STRUCTURED_CASES_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(RAW_CMCR_DIR.glob("case_*_article.xml"))
    if max_files:
        files = files[:max_files]
    report = {"total": len(files), "ok": 0, "incomplete": 0, "failed": 0,
              "rejected": [], "ok_cases": [], "failed_files": [],
              "vital_signs_extracted": 0, "red_flag_cases": 0}
    for f in files:
        parsed = parse_case(f)
        if parsed is None:
            report["failed"] += 1
            report["failed_files"].append(f.name)
            continue
        comp = parsed["completeness"]
        if comp["status"] != "ok":
            report["incomplete"] += 1
            report["rejected"].append({"cmaid": parsed["cmaid"], "title": parsed["title"],
                                       "missing": comp["missing_core_sections"]})
            continue
        case = to_native_case(parsed)
        out_path = STRUCTURED_CASES_DIR / f"case_{case.cmaid}_structured.json"
        out_path.write_text(
            json.dumps(json.loads(case.model_dump_json()), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        report["ok"] += 1
        report["ok_cases"].append(case.cmaid)
        if case.extracted_vital_signs:
            report["vital_signs_extracted"] += 1
        if case.red_flag_candidates:
            report["red_flag_cases"] += 1
    report_path = STRUCTURED_CASES_DIR / "parser_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="JATS XML → CMCR 结构化病例库批量解析")
    parser.add_argument("--max-files", type=int, default=None, help="限制解析文件数（调试用）")
    args = parser.parse_args()
    rep = batch_parse(max_files=args.max_files)
    print("=== JATS 批量解析报告 ===")
    print(f"输入 XML: {rep['total']} | 成功入库: {rep['ok']} | 完整性不足淘汰: {rep['incomplete']} | 解析失败: {rep['failed']}")
    print(f"提取到生命体征: {rep['vital_signs_extracted']} 例 | 含红旗征候选: {rep['red_flag_cases']} 例")
    if rep["rejected"]:
        print("淘汰样例(前5):", json.dumps(rep["rejected"][:5], ensure_ascii=False))
    if rep["failed_files"]:
        print("失败文件(前5):", rep["failed_files"][:5])
    print(f"输出目录: {STRUCTURED_CASES_DIR}")
