# -*- coding: utf-8 -*-
"""Task 4.1：Stage 1 粗召回器（Retrieve-then-Rerank 第一阶段）。

双模式：
* 轻量级混合召回（默认，无 GPU / HF Spaces CPU 容器）：字符级
  Unigram/Bigram/Trigram 的 BM25 与 TF-IDF 余弦相似度混合打分，
  从语料库 C 毫秒级返回 Top-K（默认 K=50）候选病例；
* 双编码器密集召回（GPU 可用时）：Qwen/Qwen3-Embedding-4B，
  Last-token pooling + l2 归一化余弦相似度；LoRA 微调见
  train_embedding_lora.py（温度缩放对比交叉熵，τ=0.05，N=7 难负样本）。

用法：
    python -m src.retrieval.stage1_retriever --build              # 建索引
    python -m src.retrieval.stage1_retriever --query "你好……"     # 单条查询演示
    python -m src.retrieval.stage1_retriever --eval-recall 50     # 口语化样本 Recall@50 抽检
"""

import argparse
import json
import math
import pickle
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from src.config import CACHE_DIR, COLLOQUIAL_DIR, STRUCTURED_CASES_DIR

INDEX_PATH = CACHE_DIR / "stage1_index.pkl"

# 病例文档表征：标题 + 主诉/现病史 + 鉴别诊断（检索语料 C 的标准视图）
_DOC_KEYS = ("patient_general_information", "diagnosis_and_differential")
_DOC_CHAR_LIMIT = 800

_PUNCT_RE = re.compile(r"[\s，。、；：？！（）《》“”‘’\-—·…,.:;?!\(\)\[\]/\\\"'`~@#$%^&*+=<>|]")


def load_corpus() -> List[Dict[str, Any]]:
    """加载全部结构化病例为检索文档（cmaid + text + category）。"""
    from src.data_pipeline.proposition_generator import load_category_index
    cat_index = load_category_index()
    docs = []
    for p in sorted(STRUCTURED_CASES_DIR.glob("case_*_structured.json")):
        case = json.loads(p.read_text(encoding="utf-8"))
        cmaid = case.get("cmaid") or re.search(r"case_(\d+)_structured", p.name).group(1)
        parts = [str(case.get("title", ""))]
        for k in _DOC_KEYS:
            t = re.sub(r"\s+", " ", str(case.get("clinical_record", {}).get(k, "")))
            if t:
                parts.append(t[:_DOC_CHAR_LIMIT])
        docs.append({"cmaid": cmaid, "text": " ".join(parts),
                     "category": cat_index.get(cmaid, "")})
    return docs


def _char_ngrams(text: str, sizes: Tuple[int, ...] = (1, 2, 3)) -> List[str]:
    """字符级 n-gram 分词（中文无空格分词的轻量替代）。"""
    text = _PUNCT_RE.sub("", text)
    grams: List[str] = []
    for n in sizes:
        grams.extend(text[i:i + n] for i in range(len(text) - n + 1))
    return grams


class HybridRetriever:
    """BM25 + TF-IDF 余弦混合召回器。"""

    def __init__(self) -> None:
        self.cmaids: List[str] = []
        self.categories: List[str] = []
        self.bm25_idf: Dict[str, float] = {}
        self.bm25_tf: List[Counter] = []       # 每文档 n-gram 词频
        self.doc_len: List[int] = []
        self.tfidf_matrix: Optional[np.ndarray] = None  # l2 归一化文档向量
        self.vocab: Dict[str, int] = {}
        self._avgdl = 0.0
        self.k1, self.b = 1.5, 0.75

    # ---------- 索引 ----------
    def fit(self, docs: List[Dict[str, Any]]) -> "HybridRetriever":
        doc_tokens = [_char_ngrams(d["text"]) for d in docs]
        self.cmaids = [d["cmaid"] for d in docs]
        self.categories = [d.get("category", "") for d in docs]
        self.doc_len = [len(t) for t in doc_tokens]
        self._avgdl = sum(self.doc_len) / len(self.doc_len)
        self.bm25_tf = [Counter(t) for t in doc_tokens]

        df: Counter = Counter()
        tf_counts = Counter()
        for tf in self.bm25_tf:
            df.update(tf.keys())
            tf_counts.update(tf.keys())
        n_docs = len(docs)
        self.bm25_idf = {g: math.log(1 + (n_docs - c + 0.5) / (c + 0.5))
                         for g, c in df.items()}
        self.vocab = {g: i for i, g in enumerate(tf_counts)}

        # TF-IDF 矩阵（l2 归一化），1341 例 × n-gram 词表可控
        self.tfidf_matrix = np.zeros((n_docs, len(self.vocab)), dtype=np.float32)
        for i, tf in enumerate(self.bm25_tf):
            for g, c in tf.items():
                j = self.vocab[g]
                self.tfidf_matrix[i, j] = (1 + math.log(c)) * self.bm25_idf[g]
        norms = np.linalg.norm(self.tfidf_matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self.tfidf_matrix /= norms
        return self

    # ---------- 查询 ----------
    def query(self, text: str, top_k: int = 50,
              weights: Tuple[float, float] = (0.5, 0.5)) -> List[Tuple[str, float]]:
        """返回 [(cmaid, score)]，score = w_bm25 * bm25_norm + w_tfidf * cosine。"""
        q_tokens = [g for g in _char_ngrams(text) if g in self.bm25_idf]
        if not q_tokens:
            return []
        q_tf = Counter(q_tokens)

        # BM25 打分（仅遍历命中的 n-gram）
        bm25 = np.zeros(len(self.cmaids), dtype=np.float32)
        for g, qf in q_tf.items():
            postings = self.bm25_idf.get(g)
            if postings is None:
                continue
            idf = postings
            for i, tf in enumerate(self.bm25_tf):
                f = tf.get(g, 0)
                if f:
                    bm25[i] += idf * f * (self.k1 + 1) / (
                        f + self.k1 * (1 - self.b + self.b * self.doc_len[i] / self._avgdl))
        if bm25.max() > 0:
            bm25 /= bm25.max()

        # TF-IDF 余弦
        q_vec = np.zeros((1, len(self.vocab)), dtype=np.float32)
        for g, qf in q_tf.items():
            j = self.vocab.get(g)
            if j is not None:
                q_vec[0, j] = (1 + math.log(qf)) * self.bm25_idf[g]
        qn = np.linalg.norm(q_vec)
        if qn > 0:
            q_vec /= qn
        cos = (self.tfidf_matrix @ q_vec.T).ravel()

        score = weights[0] * bm25 + weights[1] * cos
        order = np.argsort(-score)[:top_k]
        return [(self.cmaids[i], float(score[i])) for i in order if score[i] > 0]


class DenseRetriever:
    """双编码器密集召回（需 GPU + transformers + FlagEmbedding/sentence-transformers）。

    Qwen3-Embedding-4B：Last-token pooling + l2 归一化，
    s(q,d) = Emb(q)^T Emb(d)。LoRA 微调见 train_embedding_lora.py。
    """

    MODEL_ID = "Qwen/Qwen3-Embedding-4B"

    def __init__(self) -> None:
        try:
            import torch  # noqa: F401
            from transformers import AutoModel, AutoTokenizer
        except ImportError as exc:  # noqa: F401
            raise RuntimeError("密集召回模式需要 GPU 环境：pip install torch transformers") from exc
        self.torch = __import__("torch")
        self.tokenizer = AutoTokenizer.from_pretrained(self.MODEL_ID, padding_side="left")
        self.model = AutoModel.from_pretrained(
            self.MODEL_ID, dtype="auto", device_map="auto")

    def _last_token_pool(self, last_hidden, attention_mask):
        seq_lens = attention_mask.sum(dim=1) - 1
        return last_hidden[self.torch.arange(last_hidden.size(0)), seq_lens]

    def encode(self, texts: List[str], is_query: bool = True) -> "np.ndarray":
        prefix = "Instruct: 医疗导诊病例检索\nQuery: " if is_query else ""
        batch = self.tokenizer([prefix + t for t in texts], padding=True,
                               truncation=True, max_length=1024, return_tensors="pt").to(self.model.device)
        with self.torch.no_grad():
            out = self.model(**batch)
        emb = self._last_token_pool(out.last_hidden_state, batch["attention_mask"])
        emb = self.torch.nn.functional.normalize(emb, p=2, dim=1)
        return emb.cpu().numpy()


_STATE_KEYS = ("cmaids", "categories", "bm25_idf", "bm25_tf", "doc_len",
               "vocab", "_avgdl", "k1", "b")


def build_index() -> HybridRetriever:
    """构建并持久化混合召回索引（按值序列化，避免 pickle 模块路径问题）。"""
    docs = load_corpus()
    t0 = time.time()
    r = HybridRetriever().fit(docs)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    state = {k: getattr(r, k) for k in _STATE_KEYS}
    state["tfidf_matrix"] = r.tfidf_matrix
    with open(INDEX_PATH, "wb") as f:
        pickle.dump(state, f, protocol=4)
    n_grams = len(r.vocab)
    print(f"索引构建完成：{len(docs)} 例文档 | 词表 {n_grams} 个 n-gram "
          f"| 耗时 {time.time() - t0:.1f}s | 缓存: {INDEX_PATH}")
    return r


def load_index() -> HybridRetriever:
    if not INDEX_PATH.exists():
        return build_index()
    with open(INDEX_PATH, "rb") as f:
        state = pickle.load(f)
    r = HybridRetriever()
    for k, v in state.items():
        setattr(r, k, v)
    return r


def eval_recall(top_k: int = 50, limit: int = 200) -> float:
    """用口语化样本抽检 Recall@K（患者查询能否召回其金标准病例）。"""
    recs = json.loads((COLLOQUIAL_DIR / "colloquial_cases.json").read_text(encoding="utf-8"))["records"]
    r = load_index()
    by_cmaid = {c: i for i, c in enumerate(r.cmaids)}
    hits, total = 0, 0
    per_cat: Dict[str, List[int]] = {}
    for rec in recs[:limit]:
        q = rec["patient_query_zh"]
        gold = rec["cmaid"]
        got = [c for c, _ in r.query(q, top_k=top_k)]
        h = int(gold in got)
        hits += h
        total += 1
        per_cat.setdefault(rec["category"], []).append(h)
    print(f"Recall@{top_k}（前 {total} 条口语化样本）: {hits / total:.3f}")
    for cat, hs in sorted(per_cat.items()):
        print(f"  {cat or '(无类别)'}: {sum(hs) / len(hs):.3f} ({len(hs)} 条)")
    return hits / total


def main() -> None:
    parser = argparse.ArgumentParser(description="Task 4.1 Stage 1 粗召回器")
    parser.add_argument("--build", action="store_true", help="构建并缓存索引")
    parser.add_argument("--query", default=None, help="单条查询演示")
    parser.add_argument("--top-k", type=int, default=50)
    parser.add_argument("--eval-recall", type=int, default=None,
                        help="用口语化样本抽检 Recall@K（传 K 值）")
    args = parser.parse_args()
    if args.eval_recall:
        eval_recall(top_k=args.eval_recall)
        return
    r = build_index() if args.build else (load_index() if INDEX_PATH.exists() else build_index())
    if args.query:
        t0 = time.time()
        res = r.query(args.query, top_k=args.top_k)
        ms = (time.time() - t0) * 1000
        print(f"耗时 {ms:.0f}ms | Top-{min(args.top_k, len(res))}：")
        for cmaid, s in res[:10]:
            print(f"  {cmaid}  {s:.4f}  [{r.categories[r.cmaids.index(cmaid)]}]")
    elif not args.build:
        print("索引已就绪（--build 重建 / --query 查询 / --eval-recall 抽检）")


if __name__ == "__main__":
    main()
