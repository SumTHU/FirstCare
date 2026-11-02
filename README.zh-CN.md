# FirstCare：低幻觉普惠医疗导诊演示系统

[English](README.md) | 简体中文

基于中国临床案例成果数据库（CMCR）与 Jev 双步事实核验（Mention & Support）的接地（grounded）医疗分诊系统。
面向 **ECIR 2027 Demo Track** 投稿开发。

## 系统组成

| 模块 | 位置 | 说明 |
|---|---|---|
| 数据管线 | `src/data_pipeline/` | CMCR 原生 11 章节抽取（`jats_parser.py`、`cmcr_native_extractor.py`）、口语化问答构造（`colloquial_constructor.py`）、原子命题生成（`proposition_generator.py`） |
| 混合检索 + 精排 | `src/retrieval/` | 字符级 1/2/3-gram 的 BM25 + TF-IDF 等权混合召回 Top-50，Jev 判别式逐点重排返回 Top-k（k=10） |
| 双问题事实核验 | `src/verification/` | 对每条候选命题执行 Mention（p_men）与 Support（p_sup）检查，τ_f=0.7 三态划分 supported / gap / absent |
| 接地生成 | `src/generation/` | absent 命题物理剔除并导出为追问 chips；红旗命题 supported 即触发"立即急诊"升级硬闸门；五步式结构化输出（模板 + LLM 双模式） |
| 浏览器 Demo | `medir_triage_ui_real.html` | **单文件、无需服务端**：内嵌 1,341 例语料与 36,715 条命题，浏览器端逐行移植真实管线 |
| 前后端对拍 | `compare_backend_frontend.py`、`smoke_test_pipeline.py` | 浏览器端 JS 管线与 Python 后端多场景逐位一致（top-2 / 三态计数 / 紧急度 / 引用 / chips 全 PASS） |
| 端到端评测 | `experiments/evaluate_pipeline.py` | 30 条分层抽样端到端评分，LaTeX 结果表见 `out/table2_pipeline_eval.tex`（caption 含 LLM-judge 自我偏好声明） |

## 数据规模（实测）

1,543 例 CMCR 源病例 → 1,341 例结构化病例 → 43,271 条原子命题（红旗占 23.0%，9,968 条）→ 1,339 条口语化问答。

> ⚠️ 1,339 条口语问答当前均为 `draft` 状态，专家双盲审核尚未执行，**不得表述为 expert-verified**。

## 快速开始

```bash
# 1. 前后端对拍（验证浏览器端管线与 Python 后端一致）
python compare_backend_frontend.py

# 2. 管线冒烟测试
python smoke_test_pipeline.py

# 3. 端到端评测（需配置 LLM API，见 .env.example）
python experiments/evaluate_pipeline.py
```

浏览器 Demo 直接用浏览器打开 `medir_triage_ui_real.html` 即可，无需安装依赖。

## 在线演示与演示视频

- **交互式在线演示**：<https://sumthu.github.io/FirstCare/medir_triage_ui_real.html>（GitHub Pages 托管，点开即用）
- **演示视频**：<https://sumthu.github.io/FirstCare/>（落地页内嵌播放器），或原始文件 `data/video/demo_final_subtitled.mp4`（约 6MB，含字幕）：完整演示口语查询 → 混合检索 → 双问题事实核验 → 多轮澄清 → 红旗升级 → 接地生成的全流程。
- 录制与合成脚本：`auto_record.py`（自动录制）、`make_narration.py`（旁白 TTS）、`compose_video.py`（拼接）、`burn_subtitles.py`（烧录字幕）。

## 引用与限制

端到端评测样本量仅 30，LLM 裁判与生成器共用模型池存在自我偏好风险，引用评测数字时
须保留 `out/table2_pipeline_eval.tex` caption 中的限定声明，不得表述为 prove / SOTA 类结论。

## 许可证

基于 [MIT License](LICENSE) 开源。
