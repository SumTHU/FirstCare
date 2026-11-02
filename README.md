# FirstCare: A Low-Hallucination, Grounded Medical Triage Demonstration System

English | [简体中文](README.zh-CN.md)

FirstCare is a grounded, low-hallucination medical triage demonstration system built on the **Chinese Clinical Case Repository (CMCR)** and the **Jev two-step fact verification pipeline (Mention & Support)**.
Developed for the **ECIR 2027 Demo Track** submission.

## System Components

| Module | Location | Description |
|---|---|---|
| Data pipeline | `src/data_pipeline/` | CMCR native 11-section extraction (`jats_parser.py`, `cmcr_native_extractor.py`), colloquial QA construction (`colloquial_constructor.py`), atomic proposition generation (`proposition_generator.py`) |
| Hybrid retrieval + reranking | `src/retrieval/` | Character-level 1/2/3-gram BM25 + TF-IDF (equal-weight) hybrid recall of Top-50 candidates, followed by pointwise Jev discriminative reranking returning Top-k (k=10) |
| Two-question fact verification | `src/verification/` | For each candidate proposition, a Mention check (p_men) and a Support check (p_sup); three-state partition (supported / gap / absent) at threshold τ_f = 0.7 |
| Grounded generation | `src/generation/` | Absent propositions are physically removed and exported as follow-up chips; a supported red-flag proposition triggers a hard "seek emergency care now" escalation gate; five-step structured output (template + LLM dual mode) |
| Browser demo | `medir_triage_ui_real.html` | **Single file, no server required**: embeds the full 1,341-case corpus and 36,715 propositions; the browser-side JS is a line-by-line port of the real pipeline |
| Frontend–backend parity tests | `compare_backend_frontend.py`, `smoke_test_pipeline.py` | The browser-side JS pipeline matches the Python backend bit-exactly across multiple scenarios (top-2 / three-state counts / urgency / citations / chips all PASS) |
| End-to-end evaluation | `experiments/evaluate_pipeline.py` | End-to-end scoring on 30 stratified samples; LaTeX results table in `out/table2_pipeline_eval.tex` (caption includes the LLM-judge self-preference statement) |

## Data Scale (measured)

1,543 CMCR source cases → 1,341 structured cases → 43,271 atomic propositions (23.0% red-flag, 9,968) → 1,339 colloquial QA pairs.

> ⚠️ All 1,339 colloquial QA pairs are currently in `draft` status and the expert double-blind review has **not** been performed; they must **not** be described as expert-verified.

## Quick Start

```bash
# 1. Frontend-backend parity test (verifies the browser pipeline against the Python backend)
python compare_backend_frontend.py

# 2. Pipeline smoke test
python smoke_test_pipeline.py

# 3. End-to-end evaluation (requires LLM API configuration, see .env.example)
python experiments/evaluate_pipeline.py
```

For the browser demo, simply open `medir_triage_ui_real.html` in a browser — no dependencies required.

## Demo Video

`data/video/demo_final_subtitled.mp4` (~6 MB, subtitled): a complete walkthrough of colloquial query → hybrid retrieval → two-question fact verification → multi-turn clarification → red-flag escalation → grounded generation.
Production scripts: `auto_record.py` (automated recording), `make_narration.py` (narration TTS), `compose_video.py` (stitching), `burn_subtitles.py` (subtitle burning).

## Citation & Limitations

The end-to-end evaluation uses only 30 stratified samples, and the LLM judge shares the same model pool as the generator, posing a self-preference risk.
When citing any evaluation number, the qualification in the caption of `out/table2_pipeline_eval.tex` must be preserved; results must not be presented as prove/SOTA claims.

## License

Released under the [MIT License](LICENSE).
