# -*- coding: utf-8 -*-
"""方案 D：Hybrid ⊕ Dense 融合验证（零训练成本）。

动机：fold 0 实测 Recall@50 = 混合召回 0.948 / 基座 dense 0.467 / LoRA 微调 0.237。
dense 单独超越 hybrid 的天花板仅 +5.2%；本脚本验证另一条路线——
dense 与 hybrid 的**排名融合（RRF）**能否利用二者的互补信号
将 Recall@50 推过 0.948。若成立，"轻量 dense 作为互补召回"即成立，
LoRA 微调（方案 A/B/C）降级为锦上添花。

评测内容（全部基于已有产物，不训练）：
1. hybrid 单独（Stage 1 混合召回，Top-100 排名）
2. dense 单独——基座零样本 Qwen3-Embedding-0.6B
3. dense 单独——fold 0 LoRA 适配器（若存在 data/cache/lora_folds/fold_0）
4. RRF 融合：score = w_h/(60+rank_h) + w_d/(60+rank_d)，权重网格
5. 互补性诊断：hybrid 漏检（gold 不在 hybrid Top-50）中被 dense Top-50 找回的比例
   与融合 Recall@50 的 oracle 上限（gold ∈ hybrid∪dense Top-50）

协议：与 train_embedding_lora.py 一致的分层 5 折（seed=42），默认全部 5 折留出集。

用法：
    python -m experiments.evaluate_fusion              # 全 5 折 + fold0 适配器
    python -m experiments.evaluate_fusion --folds 0    # 仅 fold 0（最快）
    python -m experiments.evaluate_fusion --skip-adapter  # 跳过适配器编码（最快）
"""

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np

from src.config import CACHE_DIR, COLLOQUIAL_DIR

RESULTS_PATH = CACHE_DIR / "lora_folds" / "fusion_results.json"
RRF_K = 60
HYBRID_TOPK = 100          # hybrid 排名覆盖范围（之外记惩罚排名）
PENUMBRA_RANK = 1000.0     # 未进 hybrid Top-100 的文档排名惩罚值
QUERY_PREFIX = "Instruct: 医疗导诊病例检索\nQuery: "
WEIGHT_GRID = [(0.5, 0.5), (0.6, 0.4), (0.7, 0.3), (0.8, 0.2), (0.9, 0.1)]


def last_token_pool(last_hidden, attention_mask):
    import torch
    seq_lens = attention_mask.sum(dim=1) - 1
    idx = torch.arange(last_hidden.size(0), device=last_hidden.device)
    return last_hidden[idx, seq_lens]


def encode(model, tokenizer, texts, device, batch=16, max_length=512):
    import torch
    model.eval()
    embs = []
    with torch.no_grad():
        for i in range(0, len(texts), batch):
            enc = tokenizer(texts[i:i + batch], padding=True, truncation=True,
                            max_length=max_length, return_tensors="pt").to(device)
            out = model(**enc)
            e = last_token_pool(out.last_hidden_state, enc["attention_mask"])
            embs.append(torch.nn.functional.normalize(e, p=2, dim=1).float().cpu().numpy())
    return np.concatenate(embs, axis=0)


def metrics_from_ranks(gold_ranks: np.ndarray, ks=(10, 50)) -> dict:
    """gold_ranks: 每查询 gold 的 1-based 排名；0/负数 = 未命中。"""
    r = np.where(gold_ranks > 0, gold_ranks, 10 ** 9)
    out = {f"recall@{k}": float(np.mean(r <= k)) for k in ks}
    out["mrr"] = float(np.mean(1.0 / r))
    return out


def ranks_from_sims(sims: np.ndarray, doc_pos, golds) -> np.ndarray:
    """dense 相似度矩阵 → 每查询 gold 的 1-based 全库排名。"""
    order = np.argsort(-sims, axis=1)
    ranks = np.zeros(len(golds), dtype=np.int64)
    for qi, g in enumerate(golds):
        j = doc_pos.get(g)
        if j is not None:
            ranks[qi] = int(np.where(order[qi] == j)[0][0]) + 1
    return ranks


def rrf_scores(rank_h: np.ndarray, rank_d: np.ndarray, w_h: float, w_d: float) -> np.ndarray:
    """rank_h: hybrid 排名矩阵（未进 Top-100 处为 PENUMBRA_RANK）；
    rank_d: dense 全库 1-based 排名矩阵。"""
    h = np.where((rank_h > 0) & (rank_h < PENUMBRA_RANK), rank_h, PENUMBRA_RANK)
    d = np.where(rank_d > 0, rank_d, PENUMBRA_RANK)
    return w_h / (RRF_K + h) + w_d / (RRF_K + d)


def main() -> None:
    ap = argparse.ArgumentParser(description="方案 D：Hybrid ⊕ Dense RRF 融合验证")
    ap.add_argument("--model", default="Qwen/Qwen3-Embedding-0.6B")
    ap.add_argument("--folds", type=int, nargs="*", default=[0, 1, 2, 3, 4])
    ap.add_argument("--max-len", type=int, default=512)
    ap.add_argument("--skip-adapter", action="store_true",
                    help="跳过 fold 0 适配器的 dense 编码（更快）")
    ap.add_argument("--out", default=str(RESULTS_PATH), help="结果 JSON 输出路径")
    args = ap.parse_args()

    import torch
    if not torch.cuda.is_available():
        raise RuntimeError("未检测到 CUDA GPU；CPU 上编码 1341 文档过慢，请改用 GPU 环境")
    device = "cuda"
    print(f"GPU: {torch.cuda.get_device_name(0)}", flush=True)

    from src.retrieval.stage1_retriever import load_corpus, load_index
    from transformers import AutoModel, AutoTokenizer

    recs = json.loads((COLLOQUIAL_DIR / "colloquial_cases.json")
                      .read_text(encoding="utf-8"))["records"]

    corpus = load_corpus()
    doc_cmaids = [d["cmaid"] for d in corpus]
    doc_pos = {c: i for i, c in enumerate(doc_cmaids)}
    doc_texts = [d["text"] for d in corpus]

    hybrid = load_index()
    tokenizer = AutoTokenizer.from_pretrained(args.model, padding_side="left")

    # --- 模型加载：基座常驻；fold 0 适配器（若存在且未跳过）常驻 ---
    base_m = AutoModel.from_pretrained(args.model, dtype=torch.bfloat16,
                                       attn_implementation="sdpa").to(device)
    adapter_dir = CACHE_DIR / "lora_folds" / "fold_0"
    adapter_m = None
    if (not args.skip_adapter) and adapter_dir.exists():
        from peft import PeftModel
        print("挂载 fold 0 适配器……", flush=True)
        adapter_m = PeftModel.from_pretrained(
            AutoModel.from_pretrained(args.model, dtype=torch.bfloat16,
                                      attn_implementation="sdpa").to(device),
            str(adapter_dir))

    print(f"编码 {len(doc_texts)} 篇文档（基座）……", flush=True)
    t0 = time.time()
    emb_doc_base = encode(base_m, tokenizer, doc_texts, device, max_length=args.max_len)
    print(f"  完成（{(time.time()-t0):.0f}s）", flush=True)
    emb_doc_adapter = None
    if adapter_m is not None:
        print(f"编码 {len(doc_texts)} 篇文档（fold 0 适配器）……", flush=True)
        t0 = time.time()
        emb_doc_adapter = encode(adapter_m, tokenizer, doc_texts, device, max_length=args.max_len)
        print(f"  完成（{(time.time()-t0):.0f}s）", flush=True)

    per_fold = []
    for fold in args.folds:
        eval_recs = [r for r in recs if r["fold"] == fold]
        golds = [r["cmaid"] for r in eval_recs]
        queries = [r["patient_query_zh"] for r in eval_recs]
        print(f"\n===== Fold {fold}（{len(eval_recs)} 条留出查询）=====", flush=True)

        # --- hybrid 排名矩阵（Top-100 内真实排名，之外 PENUMBRA_RANK） ---
        t0 = time.time()
        rank_h = np.full((len(queries), len(doc_cmaids)), PENUMBRA_RANK, dtype=np.float32)
        for qi, q in enumerate(queries):
            for r_, (c, _s) in enumerate(hybrid.query(q, top_k=HYBRID_TOPK), start=1):
                j = doc_pos.get(c)
                if j is not None:
                    rank_h[qi, j] = float(r_)
        print(f"  hybrid Top-{HYBRID_TOPK} 排名完成（{(time.time()-t0):.0f}s）", flush=True)
        gold_rank_h = np.array([rank_h[qi, doc_pos[g]] for qi, g in enumerate(golds)],
                               dtype=np.float32)
        m_hybrid = metrics_from_ranks(np.where(gold_rank_h < PENUMBRA_RANK, gold_rank_h, 0))

        # --- dense 基座 ---
        emb_q = encode(base_m, tokenizer, [QUERY_PREFIX + q for q in queries],
                       device, batch=32, max_length=args.max_len)
        sims = emb_q @ emb_doc_base.T
        rank_d = np.argsort(np.argsort(-sims, axis=1), axis=1) + 1
        gold_rank_d = np.array([rank_d[qi, doc_pos[g]] for qi, g in enumerate(golds)])
        m_base = metrics_from_ranks(gold_rank_d)
        print(f"  hybrid 单独:   recall@50={m_hybrid['recall@50']:.3f} mrr={m_hybrid['mrr']:.3f}")
        print(f"  dense 基座:    recall@50={m_base['recall@50']:.3f} mrr={m_base['mrr']:.3f}", flush=True)

        rows = {"fold": fold, "n_eval": len(eval_recs),
                "hybrid_only": m_hybrid, "dense_base": m_base,
                "dense_fold0_adapter": None, "rrf_base": [], "rrf_adapter": [],
                "complement": None}

        def rrf_eval(rank_d_mat, sink):
            best = None
            for w_h, w_d in WEIGHT_GRID:
                s = rrf_scores(rank_h, rank_d_mat, w_h, w_d)
                gr = np.array([int(np.where(np.argsort(-s[qi]) == doc_pos[g])[0][0]) + 1
                               if g in doc_pos else 0 for qi, g in enumerate(golds)])
                m = metrics_from_ranks(gr)
                sink.append({"w_hybrid": w_h, "w_dense": w_d, **m})
                if best is None or m["recall@50"] > best["recall@50"]:
                    best = {"w_hybrid": w_h, "w_dense": w_d, **m}
            return best

        best_base = rrf_eval(rank_d, rows["rrf_base"])
        print(f"  RRF(基座) 最优: w={best_base['w_hybrid']}/{best_base['w_dense']} "
              f"recall@50={best_base['recall@50']:.3f} mrr={best_base['mrr']:.3f}", flush=True)

        gold_rank_d_ad = None
        if adapter_m is not None and emb_doc_adapter is not None:
            emb_q_ad = encode(adapter_m, tokenizer, [QUERY_PREFIX + q for q in queries],
                              device, batch=32, max_length=args.max_len)
            sims_ad = emb_q_ad @ emb_doc_adapter.T
            rank_d_ad = np.argsort(np.argsort(-sims_ad, axis=1), axis=1) + 1
            gold_rank_d_ad = np.array([rank_d_ad[qi, doc_pos[g]]
                                       for qi, g in enumerate(golds)])
            m_ad = metrics_from_ranks(gold_rank_d_ad)
            rows["dense_fold0_adapter"] = m_ad
            print(f"  dense 适配器:  recall@50={m_ad['recall@50']:.3f} mrr={m_ad['mrr']:.3f}", flush=True)
            best_ad = rrf_eval(rank_d_ad, rows["rrf_adapter"])
            print(f"  RRF(适配器) 最优: w={best_ad['w_hybrid']}/{best_ad['w_dense']} "
                  f"recall@50={best_ad['recall@50']:.3f} mrr={best_ad['mrr']:.3f}", flush=True)

        # --- 互补性诊断 ---
        in_h50 = (gold_rank_h > 0) & (gold_rank_h <= 50)
        in_d50 = (gold_rank_d > 0) & (gold_rank_d <= 50)
        missed_by_h = ~in_h50
        rescued = int(np.sum(missed_by_h & in_d50))
        oracle = float(np.mean(in_h50 | in_d50))
        comp = {"hybrid_missed": int(np.sum(missed_by_h)),
                "rescued_by_dense_top50": rescued,
                "rescue_rate": round(rescued / max(int(np.sum(missed_by_h)), 1), 3),
                "oracle_fusion_recall@50": round(oracle, 4)}
        if gold_rank_d_ad is not None:
            in_ad50 = (gold_rank_d_ad > 0) & (gold_rank_d_ad <= 50)
            comp["rescued_by_adapter_top50"] = int(np.sum(missed_by_h & in_ad50))
            comp["oracle_fusion_recall@50_with_adapter"] = round(
                float(np.mean(in_h50 | in_ad50)), 4)
        rows["complement"] = comp
        print(f"  互补性: hybrid 漏检 {comp['hybrid_missed']} 条，"
              f"dense 基座 Top-50 找回 {rescued} 条（{comp['rescue_rate']:.0%}），"
              f"融合 oracle 上限 recall@50={oracle:.3f}", flush=True)

        per_fold.append(rows)

    del base_m, adapter_m
    torch.cuda.empty_cache()

    def agg(key):
        vals = []
        for r in per_fold:
            v = r.get(key)
            if isinstance(v, dict) and "recall@50" in v:
                vals.append(v["recall@50"])
            elif isinstance(v, list) and v:
                vals.append(max(x["recall@50"] for x in v))
        return round(float(np.mean(vals)), 4) if vals else None

    summary = {
        "hybrid_only_recall@50": agg("hybrid_only"),
        "dense_base_recall@50": agg("dense_base"),
        "rrf_base_best_recall@50": agg("rrf_base"),
        "dense_adapter_recall@50": agg("dense_fold0_adapter"),
        "rrf_adapter_best_recall@50": agg("rrf_adapter"),
        "oracle_fusion_recall@50": round(float(np.mean(
            [r["complement"]["oracle_fusion_recall@50"] for r in per_fold])), 4),
    }
    print("\n===== 融合验证汇总（均值）=====")
    for k, v in summary.items():
        print(f"  {k}: {v}")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"model": args.model, "max_len": args.max_len,
                                          "rrf_k": RRF_K,
                                          "weight_grid": WEIGHT_GRID,
                                          "summary": summary, "per_fold": per_fold},
                                         ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果文件: {args.out}")


if __name__ == "__main__":
    main()
