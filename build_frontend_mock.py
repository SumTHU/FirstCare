# -*- coding: utf-8 -*-
"""构建「模拟UI（真实管线对齐版）」单文件前端演示页。

与 build_frontend_real.py 同构：读取 data/structured_cases/ 全部结构化病例，
裁剪为前端体积可控的语料（与后端 Stage 1 检索文档视图完全一致），注入
frontend_mock_template.html 的语料占位符，输出 medir_triage_ui.html。

模拟UI的 JS 逻辑层与 src/ 真实后端逐行对齐（Stage 1 混合召回 → Stage 2
Pointwise 精排 → Task 5 双问题核验 → Task 6 五步演绎生成），多轮语义与
app.py 一致（累积陈述重跑全管线）。

用法：
    python build_frontend_mock.py
"""

from pathlib import Path

from build_frontend_real import CASES_DIR, slim_case

ROOT = Path(__file__).resolve().parent
TEMPLATE_PATH = ROOT / "frontend_mock_template.html"
OUT_PATH = ROOT / "medir_triage_ui.html"

PLACEHOLDER = "/*__CORPUS__*/null"
PRESET_PLACEHOLDER = "/*__PRESET_API__*/null"


def main() -> None:
    files = sorted(CASES_DIR.glob("case_*_structured.json"))
    if not files:
        raise SystemExit(f"未找到结构化病例: {CASES_DIR}")
    cases = [slim_case(p) for p in files]

    import json
    corpus_json = json.dumps({"cases": cases}, ensure_ascii=False, separators=(",", ":"))

    # 真实 API 预跑结果（若存在则注入；不存在时前端回落离线确定性模式）
    preset_slim_path = ROOT / "data" / "cache" / "demo_preset_slim.json"
    if preset_slim_path.exists():
        preset_json = preset_slim_path.read_text(encoding="utf-8").strip()
    else:
        preset_json = "null"

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    if PLACEHOLDER not in template:
        raise SystemExit("模板中未找到语料占位符")
    html = template.replace(PLACEHOLDER, corpus_json, 1)
    if PRESET_PLACEHOLDER in html:
        html = html.replace(PRESET_PLACEHOLDER, preset_json, 1)
    else:
        print("⚠️ 模板中未找到 PRESET_API 占位符，跳过真实 API 注入")
    OUT_PATH.write_text(html, encoding="utf-8")
    n_props = sum(len(c["ps"]) for c in cases)
    print(f"病例 {len(cases)} 例 | 嵌入命题 {n_props} 条 | "
          f"真实 API 预跑注入: {'是' if preset_json != 'null' else '否'} | "
          f"输出 {OUT_PATH.name}（{OUT_PATH.stat().st_size / 1e6:.1f} MB）")


if __name__ == "__main__":
    main()
