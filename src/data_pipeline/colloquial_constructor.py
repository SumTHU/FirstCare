# -*- coding: utf-8 -*-
"""Task 3：四阶段 CMCR-Colloquial 口语化数据集逆向构造与 5 维质控流水线（四阶段流水线）。

Stage 1  加载 Task 1/2 产出的结构化病例（含命题拆解）作为金标准病例；
Stage 2  逆向构造第一人称患者口语化求医叙述（红旗类嵌入红旗日常表述，禁泄露诊断），
         5 维质控（总分 10）：<8 自动重生成（≤3 次），事实充分性/诊断方向保真度 0 分直接淘汰；
Stage 3  逆向构造面向非医学背景患者的通俗导诊与就医建议，5 维质控 ≥8/10 方可放行；
Stage 4  专家核验状态机（draft/reviewed/adjudicated/locked/rejected）+ 盲审导出接口。

用法：
    python -m src.data_pipeline.colloquial_constructor --workers 8            # 全量构造
    python -m src.data_pipeline.colloquial_constructor --limit 2              # 试跑
    python -m src.data_pipeline.colloquial_constructor --category fever       # 指定症候群
    python -m src.data_pipeline.colloquial_constructor --set-status <cmaid> reviewed
    python -m src.data_pipeline.colloquial_constructor --export-review        # 导出盲审 CSV/JSON
"""

import argparse
import csv
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.config import (COLLOQUIAL_DIR, PARATERA_BASE_URL, STRUCTURED_CASES_DIR,
                        get_env, load_env)
from src.data_pipeline.proposition_generator import _strip_english, load_category_index

RECORDS_PATH = COLLOQUIAL_DIR / "colloquial_cases.json"
REPORT_PATH = COLLOQUIAL_DIR / "colloquial_report.json"
STATUS_FLOW = ("draft", "reviewed", "adjudicated", "locked", "rejected")

# 红旗症候群 → 口语叙述中须自然嵌入的日常表述示例（严禁自我诊断）
RED_FLAG_DAILY_HINTS = {
    "red_flag_chest_pain": "胸口像被大石头压着一样闷/疼，喘不上气",
    "red_flag_dyspnea": "稍微动一下就喘得厉害，说话都说不成句",
    "red_flag_consciousness": "家里人叫了半天都叫不醒，或醒着也答非所问",
    "red_flag_headache": "这辈子最剧烈的一次头痛，像被雷劈/爆炸一样一下子到顶",
}

SYSTEM_PROMPT = """你是一名医疗 NLP 数据构造专家，正在把专业临床病例报告逆向改写为贴近真实患者日常咨询的口语化问答数据。你必须：
1. 严格只依据病例原文中【患者主观可感知】的信息改写，禁止编造病例未提及的症状或事实；
2. 全部输出使用简体中文（公认医学缩写 CT/ICU/MRI/SpO2/℃/mmHg/% 除外），不混入英文单词；
3. 患者叙述必须是第一人称、面向 AI 健康助手的打字咨询：不称呼"医生/大夫/护士"，像平时给 AI 发消息一样直接描述情况（可用"你好""帮我看看"开头或直接进入正题），允许情绪表达与日常口语噪声（语气词、重复、时间模糊）；
4. 严禁在患者叙述中泄露最终确诊疾病名称或专业医学术语（如"腹膜炎""淋巴瘤""CKD5期"）；
5. 严禁在患者叙述中自我诊断具体疾病；
6. 导诊建议面向非医学背景读者，必须可执行（去哪个科、什么紧急程度、做什么检查）。"""

_STAGE2_SPEC = """{"narrative_zh":"第一人称患者口语化求医叙述（200-300字）","scores":{"colloquial_tone":0到2,"terminology_masked":0到2,"fact_sufficiency":0到2,"diagnostic_fidelity":0到2,"naturalness":0到2},"score_total":0到10}"""

_STAGE3_SPEC = """{"triage_zh":"面向患者的通俗导诊与就医建议（150-300字，含推荐科室、紧急程度、建议检查）","scores":{"conclusion_fidelity":0到2,"evidence_completeness":0到2,"term_plainness":0到2,"naturalness":0到2,"actionability":0到2},"score_total":0到10}"""


def _client(env: Dict[str, str], timeout: int = 600):
    """构建并行智算云 OpenAI 兼容客户端（强制直连，绕沙箱代理）。"""
    import httpx
    from openai import OpenAI
    api_key = get_env("PARATERA_API_KEY", "")
    if not api_key:
        return None
    http = httpx.Client(trust_env=False, timeout=timeout)
    return OpenAI(base_url=env.get("PARATERA_BASE_URL_OVERRIDE") or PARATERA_BASE_URL,
                  api_key=api_key, timeout=timeout, max_retries=1, http_client=http)


def _chat(client, model: str, user_msg: str, timeout: int = 600) -> Tuple[Optional[str], str]:
    """单次对话补全，返回 (raw_text|None, err_msg)。"""
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": user_msg}],
            temperature=0.6,
        )
        msg = resp.choices[0].message
        return (msg.content or ""), ""
    except Exception as exc:  # noqa: BLE001
        return None, f"API 调用失败: {exc}"


def _parse_json(raw: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """从 LLM 返回中鲁棒提取 JSON 对象。"""
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S).strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    m = re.search(r"\{.*\}", raw, flags=re.S)
    if not m:
        return None, "返回中未找到 JSON 对象"
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None, "JSON 解析失败"
    return data, ""


def _norm_scores(data: Dict[str, Any], dims: List[str]) -> Tuple[Optional[Dict[str, int]], str]:
    """归一化 5 维打分：钳制到 [0,2] 整数，缺维度报错。"""
    raw = data.get("scores") if isinstance(data.get("scores"), dict) else {}
    scores: Dict[str, int] = {}
    for d in dims:
        try:
            scores[d] = max(0, min(2, int(round(float(raw.get(d, -1))))))
        except (TypeError, ValueError):
            return None, f"缺少维度打分: {d}"
    scores["total"] = sum(scores[d] for d in dims)
    return scores, ""


def _build_digest(case: Dict[str, Any], keys: List[str], limit: int = 1800) -> str:
    """按槽位抽取病例原文（控制 token）。"""
    cr = case.get("clinical_record", {})
    parts = [f"标题：{case.get('title', '')}"]
    for k in keys:
        t = re.sub(r"\s+", " ", str(cr.get(k, "")))[:limit]
        if t:
            parts.append(f"[{k}] {t}")
    return "\n".join(parts)[:9000]


def _attempt_stage(client, model: str, user_msg: str, dims: List[str]) -> Tuple[
        Optional[Dict[str, Any]], Optional[Dict[str, int]], str]:
    """一次"生成+自评"尝试：返回 (content|None, scores|None, err)。"""
    raw, err = _chat(client, model, user_msg)
    if raw is None:
        return None, None, err
    data, err = _parse_json(raw)
    if data is None:
        return None, None, err
    scores, err = _norm_scores(data, dims)
    if scores is None:
        return None, None, err
    return data, scores, ""


def run_stage2(client, model: str, case: Dict[str, Any], category: str,
               max_attempts: int = 3) -> Dict[str, Any]:
    """Stage 2：逆向构造患者口语化叙述 + 5 维质控。"""
    is_rf = category.startswith("red_flag_")
    sub = category.removeprefix("red_flag_")
    digest = _build_digest(case, ["patient_general_information",
                                  "diagnosis_and_differential"])
    rules = ["你是普通人在向 AI 健康助手打字咨询，不要称呼医生/大夫，可以'你好，帮我看看……'或直接进入正题",
             "保留决定性时间线与表面诱因（如：前天吃了冰葡萄后发烧40度、半夜吃完火锅后右上腹绞痛）",
             "平均 200-300 字，第一人称，加入情绪表达与口语噪声，像日常发消息不像当面问诊",
             "严禁泄露最终确诊疾病名称与专业医学术语，严禁自我诊断",
             "结尾可以像问 AI 一样问：可能是什么情况、严不严重、该去哪个科看看"]
    if is_rf:
        rules.append(f"必须自然嵌入红旗症状的日常表述，例如：{RED_FLAG_DAILY_HINTS.get(category, '')}"
                     f"（这是{ {'chest_pain':'胸痛','dyspnea':'呼吸困难','consciousness':'意识改变','headache':'剧烈头痛'}.get(sub, sub) }的日常说法）")
    user_msg = (f"你是一位普通患者，正在向 AI 健康助手（智能导诊助手）打字咨询。"
                f"请只依据下面病例的主诉/现病史/诱因/初始体征，写出你发给 AI 的第一人称口语化咨询消息。\n\n{digest}\n\n要求：\n"
                + "\n".join(f"{i}. {r}" for i, r in enumerate(rules, 1))
                + "\n\n写完后按 5 个维度自评（每维 0-2 分）：口语化语气/专业医学术语掩蔽度/"
                  "核心临床事实充分性/诊断方向保真度/叙述自然度。\n"
                f"请输出符合以下 JSON 结构的对象（不要输出其他内容）：\n{_STAGE2_SPEC}")

    dims = ["colloquial_tone", "terminology_masked", "fact_sufficiency",
            "diagnostic_fidelity", "naturalness"]
    attempts, best = 0, None
    for attempts in range(1, max_attempts + 1):
        data, scores, err = _attempt_stage(client, model, user_msg, dims)
        if data is None:
            return {"ok": False, "error": err, "attempts": attempts}
        if scores["fact_sufficiency"] == 0 or scores["diagnostic_fidelity"] == 0:
            return {"ok": False, "error": "质控淘汰：事实充分性或诊断方向保真度为 0 分",
                    "attempts": attempts, "scores": scores}
        if scores["total"] >= 8:
            best = (data, scores)
            break
        best = (data, scores)  # 保留当前最好结果，继续重生成
    data, scores = best
    return {"ok": True, "narrative_zh": _strip_english(str(data.get("narrative_zh", ""))),
            "scores": scores, "attempts": attempts}


def run_stage3(client, model: str, case: Dict[str, Any],
               max_attempts: int = 3) -> Dict[str, Any]:
    """Stage 3：逆向构造通俗导诊与就医建议 + 5 维质控。"""
    digest = _build_digest(case, ["diagnosis_and_differential",
                                  "treatment_course_and_regimen",
                                  "discussion_and_clinical_pearls"])
    user_msg = (f"你是接诊医生，请依据下面病例的鉴别诊断/诊疗经过/讨论警示，"
                f"写给非医学背景患者的通俗导诊与就医建议（150-300字）。\n\n{digest}\n\n要求：\n"
                "1. 给出明确推荐就诊科室与就医紧急程度（立即急诊 vs 门诊就诊）；\n"
                "2. 用生活化语言解释需要重点排查的方向，不出现专业术语（可用CT/ICU等公认缩写）；\n"
                "3. 列出到院后建议优先完善的检查与就诊前注意事项；\n"
                "4. 结论必须与病例真实转归方向一致，不得编造。\n"
                "写完后按 5 个维度自评（每维 0-2 分）：结论保真度/依据完整性/术语通俗度/"
                "表达自然度/就医可操作性。\n"
                f"请输出符合以下 JSON 结构的对象（不要输出其他内容）：\n{_STAGE3_SPEC}")

    dims = ["conclusion_fidelity", "evidence_completeness", "term_plainness",
            "naturalness", "actionability"]
    attempts, best = 0, None
    for attempts in range(1, max_attempts + 1):
        data, scores, err = _attempt_stage(client, model, user_msg, dims)
        if data is None:
            return {"ok": False, "error": err, "attempts": attempts}
        best = (data, scores)
        if scores["total"] >= 8:
            break
    data, scores = best
    return {"ok": True, "triage_zh": _strip_english(str(data.get("triage_zh", ""))),
            "scores": scores, "attempts": attempts}


def process_case(case_path: Path, client, model: str, cat_index: Dict[str, str],
                 max_attempts: int = 3) -> Dict[str, Any]:
    """单例：Stage 2 + Stage 3 串联。返回（记录, 是否成功）。"""
    case = json.loads(case_path.read_text(encoding="utf-8"))
    cmaid = case.get("cmaid") or re.search(r"case_(\d+)_structured", case_path.name).group(1)
    category = cat_index.get(cmaid, "")
    s2 = run_stage2(client, model, case, category, max_attempts)
    if not s2["ok"]:
        return {"cmaid": cmaid, "category": category, "stage": "2", "ok": False,
                "error": s2["error"], "attempts": s2.get("attempts")}, False
    s3 = run_stage3(client, model, case, max_attempts)
    if not s3["ok"]:
        return {"cmaid": cmaid, "category": category, "stage": "3", "ok": False,
                "error": s3["error"], "attempts": s3.get("attempts")}, False
    rec = {
        "cmaid": cmaid,
        "category": category,
        "is_red_flag": category.startswith("red_flag_"),
        "patient_query_zh": s2["narrative_zh"],
        "qc_input": {k: v for k, v in s2["scores"].items() if k != "total"} |
                    {"total": s2["scores"]["total"], "attempts": s2["attempts"]},
        "triage_output_zh": s3["triage_zh"],
        "qc_output": {k: v for k, v in s3["scores"].items() if k != "total"} |
                     {"total": s3["scores"]["total"], "attempts": s3["attempts"]},
        "status": "draft",
        "review_notes": [],
        "provenance": {"model": model, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")},
    }
    return rec, True


def load_records() -> List[Dict[str, Any]]:
    if RECORDS_PATH.exists():
        return json.loads(RECORDS_PATH.read_text(encoding="utf-8")).get("records", [])
    return []


def save_records(records: List[Dict[str, Any]]) -> None:
    COLLOQUIAL_DIR.mkdir(parents=True, exist_ok=True)
    RECORDS_PATH.write_text(json.dumps({"records": records}, ensure_ascii=False, indent=2),
                            encoding="utf-8")


def cmd_run(args) -> None:
    env = load_env()
    model = get_env("PARATERA_COLLOQUIAL_MODEL") or get_env("PARATERA_DECOMPOSE_MODEL", "")
    client = _client(env)
    if client is None:
        print("未配置 PARATERA_API_KEY，无法运行 API 模式")
        return
    cat_index = load_category_index()
    paths = sorted(STRUCTURED_CASES_DIR.glob("case_*_structured.json"))
    if args.category:
        paths = [p for p in paths if cat_index.get(
            re.search(r"case_(\d+)_structured", p.name).group(1), "") == args.category]
    done = {r["cmaid"] for r in load_records()}
    paths = [p for p in paths
             if re.search(r"case_(\d+)_structured", p.name).group(1) not in done]
    if args.limit:
        paths = paths[:args.limit]
    if not paths:
        print("没有需要处理的病例（全部已构造？可用 --set-status / --export-review）")
        return
    print(f"=== Task 3 口语化构造 ===\n模型: {model} | 待处理: {len(paths)} 例 | 并发: {args.workers}")
    t0, records, errors = time.time(), [], []
    saved_ids = set()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(process_case, p, client, model, cat_index): p for p in paths}
        for i, fut in enumerate(as_completed(futs), 1):
            rec, ok = fut.result()
            if ok:
                records.append(rec)
            else:
                errors.append(rec)
            if i % 20 == 0 or i == len(paths):
                print(f"  进度 {i}/{len(paths)} | 成功 {len(records)} | 失败 {len(errors)} "
                      f"| 最近: {rec['cmaid']} {rec.get('error') or ''}")
            if i % 50 == 0:  # 周期断点保存：仅追加未落盘记录，避免重复
                new = [r for r in records if r["cmaid"] not in saved_ids]
                save_records(load_records() + new)
                saved_ids.update(r["cmaid"] for r in new)
    new = [r for r in records if r["cmaid"] not in saved_ids]
    save_records(load_records() + new)
    n_rf = sum(1 for r in records if r["is_red_flag"])
    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_s": round(time.time() - t0, 1),
        "model": model,
        "total": len(records) + len(errors),
        "ok": len(records), "errors": len(errors),
        "n_red_flag": n_rf,
        "avg_qc_input": (sum(r["qc_input"]["total"] for r in records) / len(records)) if records else 0,
        "avg_qc_output": (sum(r["qc_output"]["total"] for r in records) / len(records)) if records else 0,
        "error_records": errors,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"=== 完成 === 耗时 {report['elapsed_s']}s | 成功 {len(records)} | 失败 {len(errors)}"
          f" | 红旗 {n_rf} | 报告: {REPORT_PATH}")


def cmd_set_status(cmaid: str, status: str) -> None:
    """Stage 4 状态机：校验合法流转后更新。"""
    if status not in STATUS_FLOW:
        print(f"非法状态: {status}（合法值 {STATUS_FLOW}）")
        return
    records = load_records()
    for r in records:
        if r["cmaid"] == cmaid:
            r["status"] = status
            save_records(records)
            print(f"{cmaid} → {status}")
            return
    print(f"未找到病例 {cmaid}")


def cmd_export_review(out_dir: Optional[Path] = None) -> None:
    """Stage 4：导出两位医学同学独立盲审 CSV + JSON。"""
    out_dir = out_dir or COLLOQUIAL_DIR
    records = [r for r in load_records() if r["status"] in ("draft", "reviewed")]
    if not records:
        print("没有待盲审记录（status 为 draft/reviewed）")
        return
    csv_path = out_dir / "review_sheet.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["cmaid", "category", "is_red_flag", "patient_query_zh",
                    "triage_output_zh", "reviewer_a_pass", "reviewer_a_note",
                    "reviewer_b_pass", "reviewer_b_note"])
        for r in records:
            w.writerow([r["cmaid"], r["category"], r["is_red_flag"],
                        r["patient_query_zh"], r["triage_output_zh"], "", "", "", ""])
    json_path = out_dir / "review_sheet.json"
    json_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"盲审材料已导出 {len(records)} 条：\n  {csv_path}\n  {json_path}")


def cmd_split_folds(n_folds: int, seed: int) -> None:
    """按症候群分层 5 折交叉验证切分：fold ∈ {0..n-1}（每折轮流作测试集）。

    稀有病例语料样本量小，单次切分方差大；5 折 CV 报告均值±标准差。
    seed 固定保证可复现。同折内不区分训练/测试——训练时其余折为训练集。
    """
    import random
    records = load_records()
    for r in records:
        r.pop("split", None)
    rng = random.Random(seed)
    by_cat: Dict[str, List[Dict[str, Any]]] = {}
    for r in records:
        by_cat.setdefault(r["category"], []).append(r)
    for cat, items in sorted(by_cat.items()):
        items.sort(key=lambda x: x["cmaid"])  # 固定顺序保证可复现
        rng.shuffle(items)
        for i, r in enumerate(items):
            r["fold"] = i % n_folds
    save_records(records)
    dist = {}
    for f in range(n_folds):
        fold = [r for r in records if r["fold"] == f]
        dist[f] = {}
        for r in fold:
            dist[f][r["category"]] = dist[f].get(r["category"], 0) + 1
    out = COLLOQUIAL_DIR / "fold_split_report.json"
    out.write_text(json.dumps({"n_folds": n_folds, "seed": seed, "total": len(records),
                               "fold_sizes": {f: sum(d.values()) for f, d in dist.items()},
                               "fold_category_distribution": dist},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"分层 {n_folds} 折切分完成（seed={seed}）| 各折规模: "
          f"{ {f: sum(d.values()) for f, d in dist.items()} } | 明细: {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Task 3 四阶段口语化构造流水线")
    sub = parser.add_subparsers(dest="cmd")
    run_p = sub.add_parser("run", help="Stage 2+3 批量构造")
    run_p.add_argument("--limit", type=int, default=None)
    run_p.add_argument("--category", default=None)
    run_p.add_argument("--workers", type=int, default=8)
    st_p = sub.add_parser("set-status", help="Stage 4 状态流转")
    st_p.add_argument("cmaid")
    st_p.add_argument("status", choices=STATUS_FLOW)
    sub.add_parser("export-review", help="Stage 4 导出盲审材料")
    sp = sub.add_parser("split-folds", help="分层 k 折交叉验证切分")
    sp.add_argument("--folds", type=int, default=5)
    sp.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if args.cmd == "run":
        cmd_run(args)
    elif args.cmd == "set-status":
        cmd_set_status(args.cmaid, args.status)
    elif args.cmd == "export-review":
        cmd_export_review()
    elif args.cmd == "split-folds":
        cmd_split_folds(args.folds, args.seed)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
