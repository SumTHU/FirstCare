# -*- coding: utf-8 -*-
"""Task 4.1（GPU）：双编码器 Embedding 模型 LoRA 微调 + 分层 5 折交叉验证。

对应计划书规格（模型规格因显存约束调整）：
* 架构：Qwen3-Embedding 系列，Last-token pooling + l2 归一化余弦相似度
  - 训练默认 Qwen/Qwen3-Embedding-0.6B（RTX 3070 Ti 8GB 约束；4B 需 ≥24GB 或 QLoRA）
* LoRA：{q, k, v, o}_proj，r=16, alpha=32, dropout=0.05
* 损失：温度缩放对比交叉熵（公式 1），τ=0.05，每正样本配 N=7 难负样本
* 数据隔离：分层 5 折（seed=42），每折训练集 = 其余 4 折，评测集 = 留出折；
  难负样本由 Stage 1 混合召回在**训练折范围外挖掘**（不含留出折病例亦可，
  但负例仅作对比项、不参与评测，故统一在全库挖掘非金标准难负样本）
* 对照基线：同折划分下的 Stage 1 混合召回（无训练参数）

输出：data/cache/lora_folds/ 下逐折适配器与汇总指标（均值±标准差）。

用法（GPU 环境）：
    pip install torch --index-url https://download.pytorch.org/whl/cu124
    pip install transformers peft accelerate
    python -m src.retrieval.train_embedding_lora            # 5 折全流程

路线决策（2026-10-29，方案 A）：Stage 1 主线为混合召回——fold 0 实测在 naive
超参（lr=2e-4、纯难负、2 epoch）下微调发生灾难性遗忘（Recall@50：混合 0.948 /
基座 dense 0.467 / 微调后 0.237，详见技术报告 5.3 节）。本脚本降级为**方案 B
可选增强**，修正超参后先 200 条子集快扫，取得正提升再上全量 5 折：

    HF_ENDPOINT=https://hf-mirror.com python -u -m src.retrieval.train_embedding_lora \
        --folds 1 --epochs 1 --batch-size 1 --checkpoint --lr 2e-5 --limit 200

--eval-only 可复用 data/cache/lora_folds/fold_<n> 已训适配器仅做评测。
"""

import argparse
import json
import os
import random
import time
from pathlib import Path
from typing import Dict, List

# 必须在 torch 初始化 CUDA 前设置：减少显存碎片
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

from src.config import CACHE_DIR, COLLOQUIAL_DIR

N_FOLDS_DEFAULT = 5
LORA_R, LORA_ALPHA, LORA_DROPOUT = 16, 32, 0.05
TAU = 0.05
N_HARD_NEG = 7
MAX_LEN = 1024
QUERY_PREFIX = "Instruct: 医疗导诊病例检索\nQuery: "
DOC_BATCH = 16

RESULTS_PATH = CACHE_DIR / "lora_folds" / "cv_results.json"


def load_fold_records() -> List[Dict]:
    recs = json.loads((COLLOQUIAL_DIR / "colloquial_cases.json").read_text(encoding="utf-8"))["records"]
    if not all("fold" in r for r in recs):
        raise RuntimeError("口语化记录缺少 fold 字段，请先运行 split-folds")
    return recs


def mine_pairs_for_fold(recs: List[Dict], fold: int, limit: int) -> List[Dict]:
    """为指定折挖掘训练三元组（训练集 = 其余折；难负样本来自混合召回）。"""
    from src.retrieval.stage1_retriever import load_index
    train_recs = [r for r in recs if r["fold"] != fold][:limit]
    r = load_index()
    pairs = []
    for rec in train_recs:
        q, gold = rec["patient_query_zh"], rec["cmaid"]
        cands = [c for c, _ in r.query(q, top_k=N_HARD_NEG + 1)]
        negs = [c for c in cands if c != gold][:N_HARD_NEG]
        if len(negs) < N_HARD_NEG:
            pool = [c for c in r.cmaids if c != gold and c not in negs]
            random.shuffle(pool)
            negs += pool[:N_HARD_NEG - len(negs)]
        pairs.append({"query": q, "gold": gold, "hard_negatives": negs})
    return pairs


def last_token_pool(last_hidden, attention_mask):
    import torch
    seq_lens = attention_mask.sum(dim=1) - 1
    idx = torch.arange(last_hidden.size(0), device=last_hidden.device)
    return last_hidden[idx, seq_lens]


def encode(model, tokenizer, texts: List[str], device, prefix: str = "", batch: int = DOC_BATCH,
           max_length: int = 1024):
    """批量编码 → l2 归一化矩阵 [n, d]（numpy）。"""
    import numpy as np
    import torch
    model.eval()
    embs = []
    with torch.no_grad():
        for i in range(0, len(texts), batch):
            enc = tokenizer([prefix + t for t in texts[i:i + batch]], padding=True,
                            truncation=True, max_length=max_length, return_tensors="pt").to(device)
            out = model(**enc)
            e = last_token_pool(out.last_hidden_state, enc["attention_mask"])
            embs.append(torch.nn.functional.normalize(e, p=2, dim=1).float().cpu().numpy())
    return np.concatenate(embs, axis=0)


def eval_recall_mrr(emb_query, emb_doc, doc_cmaids, eval_recs, ks=(10, 50)):
    """按查询检索全库，计算 Recall@k 与 MRR。"""
    import numpy as np
    sims = emb_query @ emb_doc.T  # [n_q, n_d]
    order = np.argsort(-sims, axis=1)
    res = {f"recall@{k}": [] for k in ks}
    res["mrr"] = []
    rank_of_gold = {c: i for i, c in enumerate(doc_cmaids)}
    for qi, rec in enumerate(eval_recs):
        gold = rec["cmaid"]
        ranked = [doc_cmaids[j] for j in order[qi]]
        rank = ranked.index(gold) + 1 if gold in ranked else None
        for k in ks:
            res[f"recall@{k}"].append(float(rank is not None and rank <= k))
        res["mrr"].append(1.0 / rank if rank else 0.0)
    return {m: sum(v) / len(v) for m, v in res.items()}


def train_one_fold(fold: int, pairs: List[Dict], doc_text: Dict[str, str],
                   model_id: str, args, out_dir: Path) -> None:
    """单折 LoRA 训练，保存适配器。"""
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModel, AutoTokenizer

    device = "cuda"
    tokenizer = AutoTokenizer.from_pretrained(model_id, padding_side="left")
    model = AutoModel.from_pretrained(model_id, dtype=torch.bfloat16,
                                      attn_implementation="sdpa").to(device)
    lora = LoraConfig(r=LORA_R, lora_alpha=LORA_ALPHA, lora_dropout=LORA_DROPOUT,
                      target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
                      task_type="FEATURE_EXTRACTION")
    # autocast_adapter_dtype=False：适配器保持 bf16，避免 PEFT 每层前向复制 fp32 隐状态
    # （8GB 显存下该副本是 OOM/溢出的主因；LoRA bf16 训练稳定性足够）
    model = get_peft_model(model, lora, autocast_adapter_dtype=False)
    # 梯度检查点：仅在显存不够时开启（--checkpoint），激活重计算代价 +33% 时间
    if args.checkpoint:
        model.gradient_checkpointing_enable()
        model.enable_input_require_grads()
    model.print_trainable_parameters()

    idx = list(range(len(pairs)))
    random.shuffle(idx)
    bs = args.batch_size
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=args.lr)
    model.train()
    t0, step = time.time(), 0
    for epoch in range(args.epochs):
        random.shuffle(idx)
        for s in range(0, len(idx), bs):
            batch = [pairs[i] for i in idx[s:s + bs]]
            queries = [QUERY_PREFIX + p["query"] for p in batch]
            positives = [doc_text[p["gold"]] for p in batch]
            negatives = [doc_text[c] for p in batch for c in p["hard_negatives"]]
            texts = queries + positives + negatives
            import torch as _t
            enc = tokenizer(texts, padding=True, truncation=True,
                            max_length=args.max_len, return_tensors="pt").to(device)
            out = model(**enc)
            emb = _t.nn.functional.normalize(
                last_token_pool(out.last_hidden_state, enc["attention_mask"]), p=2, dim=1)
            m = len(batch)
            q_emb, p_emb, n_emb = emb[:m], emb[m:2 * m], emb[2 * m:]
            pos_sim = (q_emb * p_emb).sum(-1) / TAU                      # [m]
            neg_sim = _t.einsum("bd,bnd->bn", q_emb, n_emb.view(m, N_HARD_NEG, -1)) / TAU  # [m, N]
            logits = _t.cat([pos_sim.unsqueeze(1), neg_sim], dim=1)      # [m, 1+N]
            labels = _t.zeros(m, dtype=_t.long, device=device)
            loss = _t.nn.functional.cross_entropy(logits, labels)
            loss.backward()
            optimizer.step()
            optimizer.zero_grad()
            step += 1
            if step % 20 == 0:
                print(f"    fold{fold} epoch{epoch + 1} step{step} loss={loss.item():.4f} "
                      f"({time.time() - t0:.0f}s)", flush=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(out_dir))
    del model
    torch.cuda.empty_cache()


def main() -> None:
    parser = argparse.ArgumentParser(description="Qwen3-Embedding LoRA 微调（分层 5 折 CV）")
    parser.add_argument("--model", default="Qwen/Qwen3-Embedding-0.6B")
    parser.add_argument("--folds", type=int, default=N_FOLDS_DEFAULT)
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--max-len", type=int, default=512,
                        help="序列截断长度（512 足以覆盖查询+文档关键段，且显存/耗时减半）")
    parser.add_argument("--checkpoint", action="store_true",
                        help="开启梯度检查点（显存不足时使用，约 +33%% 训练时间）")
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--limit", type=int, default=0, help="每折训练对上限（0=全部）")
    parser.add_argument("--eval-k", type=int, nargs="*", default=[10, 50])
    parser.add_argument("--eval-only", action="store_true",
                        help="跳过训练，直接用已保存的折适配器评测")
    parser.add_argument("--skip-baseline", action="store_true")
    parser.add_argument("--folds-to-run", type=int, nargs="*", default=None,
                        help="只跑指定折（断点续跑）")
    args = parser.parse_args()

    import torch
    if not torch.cuda.is_available():
        raise RuntimeError("未检测到 CUDA GPU；CPU 环境请使用 Stage 1 混合召回模式")
    print(f"GPU: {torch.cuda.get_device_name(0)} | 显存 {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f}GB")

    from src.retrieval.stage1_retriever import load_corpus, load_index
    recs = load_fold_records()
    corpus = load_corpus()
    doc_text = {d["cmaid"]: d["text"] for d in corpus}
    doc_cmaids = [d["cmaid"] for d in corpus]
    hybrid = load_index()
    results = []
    if RESULTS_PATH.exists():  # 断点续跑：跳过已完成折
        results = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))["results"]
    done_folds = {r["fold"] for r in results}
    folds = args.folds_to_run or [f for f in range(args.folds) if f not in done_folds]

    for fold in folds:
        print(f"\n===== Fold {fold}/{args.folds} =====", flush=True)
        eval_recs = [r for r in recs if r["fold"] == fold]
        if args.eval_only:
            pairs = []  # 仅评测：复用已训练适配器，跳过挖掘
        else:
            pairs = mine_pairs_for_fold(recs, fold, args.limit or 10 ** 9)
        print(f"训练对 {len(pairs)} | 留出评测 {len(eval_recs)} 条", flush=True)

        # 混合召回基线（同折）
        if not args.skip_baseline:
            hits, mrr = 0.0, 0.0
            for rec in eval_recs:
                ranked = [c for c, _ in hybrid.query(rec["patient_query_zh"], top_k=max(args.eval_k))]
                rank = ranked.index(rec["cmaid"]) + 1 if rec["cmaid"] in ranked else None
                hits += float(rank is not None and rank <= 50)
                mrr += 1.0 / rank if rank else 0.0
            base = {"recall@50": hits / len(eval_recs), "mrr": mrr / len(eval_recs)}
            print(f"  混合召回基线: recall@50={base['recall@50']:.3f} mrr={base['mrr']:.3f}", flush=True)
        else:
            base = None

        adapter_dir = CACHE_DIR / "lora_folds" / f"fold_{fold}"
        if args.eval_only:
            if not adapter_dir.exists():
                raise FileNotFoundError(f"--eval-only 需要已训练的适配器：{adapter_dir}")
            print(f"  [eval-only] 复用已有适配器: {adapter_dir}", flush=True)
        else:
            train_one_fold(fold, pairs, doc_text, args.model, args, adapter_dir)

        # 挂载适配器评测留出折
        from peft import PeftModel
        from transformers import AutoModel, AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(args.model, padding_side="left")
        base_model = AutoModel.from_pretrained(args.model, dtype=torch.bfloat16,
                                               attn_implementation="sdpa").to("cuda")
        model = PeftModel.from_pretrained(base_model, str(adapter_dir))
        emb_doc = encode(model, tokenizer, list(doc_text.values()), "cuda", max_length=args.max_len)
        emb_q = encode(model, tokenizer, [QUERY_PREFIX + r["patient_query_zh"] for r in eval_recs],
                       "cuda", batch=32, max_length=args.max_len)
        metrics = eval_recall_mrr(emb_q, emb_doc, doc_cmaids, eval_recs, ks=args.eval_k)
        print(f"  微调后: recall@50={metrics['recall@50']:.3f} mrr={metrics['mrr']:.3f}", flush=True)
        results.append({"fold": fold, "n_train": len(pairs), "n_eval": len(eval_recs),
                        "baseline_hybrid": base, "finetuned": metrics})
        del model, base_model
        torch.cuda.empty_cache()
        RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULTS_PATH.write_text(json.dumps({"model": args.model, "epochs": args.epochs,
                                            "tau": TAU, "n_hard_neg": N_HARD_NEG,
                                            "results": results}, ensure_ascii=False, indent=2),
                                encoding="utf-8")

    # 汇总
    import statistics as st
    summary = {}
    for m in ("recall@10", "recall@50", "mrr"):
        vals = [r["finetuned"][m] for r in results if m in r["finetuned"]]
        if vals:
            summary[m] = {"mean": round(st.mean(vals), 4), "std": round(st.stdev(vals), 4) if len(vals) > 1 else 0.0}
    bvals = [r["baseline_hybrid"]["recall@50"] for r in results if r.get("baseline_hybrid")]
    if bvals:
        summary["baseline_recall@50"] = {"mean": round(st.mean(bvals), 4),
                                         "std": round(st.stdev(bvals), 4) if len(bvals) > 1 else 0.0}
    print("\n===== 5 折汇总（均值±标准差）=====")
    for m, v in summary.items():
        print(f"  {m}: {v['mean']:.4f} ± {v['std']:.4f}")
    print(f"结果文件: {RESULTS_PATH}")


if __name__ == "__main__":
    main()
