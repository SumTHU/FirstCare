# MedIR-CMCR 核心工程开发包 (Master Development Suite)

基于中国临床案例成果数据库 (CMCR) 与 Jev 双步事实核验（Mention & Support）的低幻觉普惠医疗导诊系统。
面向 **ECIR 2027 Call for Demos** 投稿开发。

## 📦 包含的核心文件清单

1. **`project_plan_medir_cmcr.md`**：开发 Agent 主规划规范书（定义症候群覆盖范围、Task 0 至 Task 8、Pydantic 数据契约、Jev 公式 1–3、System Prompts 及一票否决 Veto% + R1–R6 100 分制评测体系）。
2. **`cmcr_case_1673770_structured.json`**：标杆结构化病例（cmaid/1673770，T细胞淋巴瘤合并嗜血综合征HLH）及 5 条离线查询无关原子临床命题 $\mathcal{F}$。
3. **`app.py`**：（**已废弃**——早期内联演示版，未接真实管线；前端将按 `src/` 真实管线全新实现，见《可视化平台设计说明.md》）。
4. **`evaluate_fever_agent.py`**：基准评测与消融实验脚本（已验证 Pointwise Jev Reranking Top-1 准确率 100.0%，Two-Question Jev Fact Role 判定准确率 100.0%）。
5. **`cmcr_native_extractor.py`**：CMCR 原生 11 章节抽取与生命体征数值解析器。
6. **`project_introduction.md`**：项目全景介绍。
7. **`medical_collaborator_guide.md`**：医学合作同学分工指南与双盲评测准则。
8. **`可视化平台设计说明.md`**：前端交接文档（目录结构、后端调用链、数据结构、设计要求、缓存策略）。

## 🚀 快速启动指南

### 1. 运行基准评测脚本 (100% 准确率验证)
```bash
python3 evaluate_fever_agent.py
```

### 2. 启动 Streamlit 交互演示系统
```bash
pip install -r requirements.txt
streamlit run app.py
```

### 3. 交给开发 Agent (Cursor / Claude Code) 自主开工
直接将 `project_plan_medir_cmcr.md` 中的【第六章：一键启动主提示词】复制给开发 Agent 即可开始全自动重构与扩展。
