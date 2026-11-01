# -*- coding: utf-8 -*-
"""构建「真实管线」单文件前端演示页。

读取 data/structured_cases/ 全部结构化病例，裁剪为前端体积可控的语料
（检索文档视图与后端 Stage 1 完全一致：标题 + 主诉现病史[:800] + 鉴别诊断[:800]；
命题保留前 PROP_CAP 条及全部核验关键词），注入 frontend_real_template.html
的语料占位符，输出 medir_triage_ui_real.html。

用法：
    python build_frontend_real.py
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent
CASES_DIR = ROOT / "data" / "structured_cases"
TEMPLATE_PATH = ROOT / "frontend_real_template.html"
OUT_PATH = ROOT / "medir_triage_ui_real.html"

DOC_CHAR_LIMIT = 800    # 与 src/retrieval/stage1_retriever.py::_DOC_CHAR_LIMIT 一致
PROP_CAP = 30           # 与 src/generation/deductive_generator.select_props 默认 cap 一致（红旗命题优先全量）
KW_CAP = 10             # 每类核验关键词保留条数
TITLE_LIMIT = 120
FIELD_LIMIT = 60


def _clean(text: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def slim_case(path: Path) -> Dict[str, Any]:
    case = json.loads(path.read_text(encoding="utf-8"))
    m = re.search(r"case_(\d+)_structured", path.name)
    cmaid = str(case.get("cmaid") or (m.group(1) if m else ""))
    cr = case.get("clinical_record", {})
    ab = case.get("structured_abstract", {})

    props: List[Dict[str, Any]] = []
    # 对齐后端 select_props：红旗命题全量入选（不受 cap 裁剪），其余按原序补足
    raw_props = case.get("propositions", [])
    rf_props = [pr for pr in raw_props if pr.get("is_red_flag")]
    rest_props = [pr for pr in raw_props if not pr.get("is_red_flag")]
    for pr in (rf_props + rest_props[:max(PROP_CAP - len(rf_props), 0)]):
        props.append({
            "id": pr.get("prop_id", ""),
            "zh": pr.get("proposition_zh", ""),
            "en": pr.get("proposition_en") or "",
            "cat": pr.get("element_category", ""),
            "rf": bool(pr.get("is_red_flag", False)),
            "chip": pr.get("follow_up_chip", ""),
            "mk": [k for k in pr.get("mention_keywords", []) if k][:KW_CAP],
            "ak": [k for k in pr.get("affirm_keywords", []) if k][:KW_CAP],
            "nk": [k for k in pr.get("negate_keywords", []) if k][:KW_CAP],
            "uk": [k for k in pr.get("uncertain_keywords", []) if k][:KW_CAP],
        })

    return {
        "c": cmaid,
        "t": _clean(case.get("title", ""), TITLE_LIMIT),
        "u": case.get("url", ""),
        "dept": _clean(case.get("reporting_department", ""), FIELD_LIMIT),
        "sp": [s.get("specialty", "") for s in case.get("involved_specialties", [])
               if s.get("specialty")][:4],
        "gi": _clean(cr.get("patient_general_information", ""), DOC_CHAR_LIMIT),
        "dd": _clean(cr.get("diagnosis_and_differential", ""), DOC_CHAR_LIMIT),
        "h": _clean(ab.get("history_summary", ""), 110),
        "ss": _clean(ab.get("symptoms_and_signs", ""), 150),
        "dg": _clean(ab.get("diagnosis_method", ""), 110),
        "ps": props,
    }


def main() -> None:
    files = sorted(CASES_DIR.glob("case_*_structured.json"))
    if not files:
        raise SystemExit(f"未找到结构化病例: {CASES_DIR}")
    cases = [slim_case(p) for p in files]
    corpus_json = json.dumps({"cases": cases}, ensure_ascii=False, separators=(",", ":"))

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    placeholder = "/*__CORPUS__*/null"
    if placeholder not in template:
        raise SystemExit("模板中未找到语料占位符")
    html = template.replace(placeholder, corpus_json, 1)
    OUT_PATH.write_text(html, encoding="utf-8")
    n_props = sum(len(c["ps"]) for c in cases)
    print(f"病例 {len(cases)} 例 | 嵌入命题 {n_props} 条 | "
          f"输出 {OUT_PATH.name}（{OUT_PATH.stat().st_size / 1e6:.1f} MB）")


if __name__ == "__main__":
    main()
