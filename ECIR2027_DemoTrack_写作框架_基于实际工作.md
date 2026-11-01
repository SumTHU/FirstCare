# ECIR 2027 Demo Track 写作框架（对应绝对真实工作）

> 配套文件：《ECIR2027_DemoTrack_投稿写作要求说明.md》（硬性约束）与《项目工作实况说明.md》（真实工作盘点）。
> **本框架的每一处"可写内容"均对应仓库内可查证的产物；每一处"不可写"均对应实况说明中明确标注的未完成项。**
> 正文限 4 页、英文、LNCS 模板。下述章节按 LNCS demo paper 惯例给出篇幅分配与素材映射。

---

## 诚信红线（写作前必读）

**以下数字/结论目前可写入论文（均有实测依据）：**

| 可写内容 | 数值/事实 | 依据文件 |
|---|---|---|
| 病例候选池规模 | 1,543 例（六类分层），原始存档 3,117 文件 | `case_pool_index.json`、`data/raw_cmcr/` |
| 结构化病例 | 1,341 例（JATS → 七槽位结构化 JSON） | `data/structured_cases/`、`jats_parser.py` |
| 原子临床命题 | 43,271 条，均值 32.3 条/例，红旗命题 9,968 条（23.0%） | `proposition_generator.py` 产出报告 |
| 口语化数据集 | 1,339 条问答对，分层 5 折切分（seed=42） | `colloquial_cases.json`、`fold_split_report.json` |
| 混合召回主线 | fold 0 全量对照 Recall@50=0.948 / MRR=0.826（270 条留出查询） | `data/cache/lora_folds/cv_results.json` |
| 全量预检数字 | Recall@50=0.955（200 条抽检，须标注为 preliminary sample check） | 技术报告 2026-10-29 |
| 密集检索 LoRA | **负结果**：微调后 0.237 < 零样本 0.467 < 混合召回 0.948（fold 0，可作 ablation/lesson） | 同上 |
| 双问题核验协议 v2 | 端到端联调实测修正（要素级提问，胸痛要素 p_men=0.95 / p_sup=0.78，小样本探针） | `jev_cache.sqlite`、技术报告 5.4 节 |
| 防幻觉硬闸门 | absent 命题物理剔除 + 红旗急危升级模板层硬规则 | `jev_fact_verifier.py`、`deductive_generator.py` |
| 混合召回 5 折全量 | Recall@50 均值 0.941（0.948/0.944/0.929/0.936/0.948） | `data/cache/lora_folds/fusion_results.json` |
| RRF 融合（消融） | 与零样本稠密检索 RRF 融合最优 Recall@50=0.9477（fold 0 达 0.959），但 MRR 0.827→0.487；oracle 上限 0.9522 | 同上 |
| LoRA 适配器（5 折） | Recall@50=0.197，灾难性遗忘结论强化（负结果） | 同上 |
| max_len 1024 探针 | 稠密零样本 0.426→0.224，5 折全恶化，截断假说否定 | `fusion_results_len1024.json` |
| 端到端管线评测（Task 7） | 30 条分层抽样，全 LLM 模式：R1-R6 均分 71.4（红旗组 67.8 / 非红旗 73.2）；Veto1=0、Veto3=0；Veto2=3.3%（1 例）、Veto4=3.3%（1 例）；红旗组"立即急诊"判定 70%；gold Top-1 86.7%；引用有效 100%；分层均值：发热 77.6 / 腹痛 68.8 / 胸痛 80.7 / 头痛 43.5 等 | `data/cache/eval_results/pipeline_eval.json`、`out/table2_pipeline_eval.tex` |
| Supported 双栏拆分 + 数值守卫 | Jev supported 须同时过确定性词面锚点才算"患者已确认"，否则降为"病例待核对特征"；数值/时间限定词守卫阻断过度具体化 | `deductive_generator.py`（`split_supported_facts()`、`_specificity_guard()`） |
| 前端↔后端对拍 | 浏览器端 JS 移植管线与 Python 后端 5 场景逐位一致（top-2/三态计数/urgency/route/引用/chips/五步文本全 PASS） | `compare_backend_frontend.py`、`smoke_test_pipeline.py` |

**以下内容在完成前严禁写入论文（实况说明明确标注的未完成项）：**

1. `evaluate_fever_agent.py` 中的"100% 准确率"与整张消融表——来源是 3 条硬编码 mock 用例 + 硬编码占位字面量，**不是实验输出**；
2. "专家审核/锁定数据集"——1,339 条记录全部 `status=draft`，Stage 4 人工双盲审核未执行，**不得声称 expert-verified / expert-locked**；
3. Task 7 端到端数字**引用时必须带限定**：样本量仅 30、LLM 裁判与生成器共用模型池存在自我偏好风险（`table2_pipeline_eval.tex` 的 caption 已内置此声明，论文中必须保留并译为英文）；不得表述为 prove/SOTA 类结论；红旗组 Veto2=1 例与 Veto4=1 例须如实报告；
4. LLM 自评 QC 分数（9.92/9.75）——若提及必须明确限定为 LLM-based self-assessment 下限保证，不得表述为质量指标；
5. "已上线/在线部署"——本地单文件 demo 已可运行，但在完成托管并取得公开 URL 前不得声称 online deployment。

---

## 论文结构与篇幅分配（共 4 页正文）

### 0. Title / Authors / ORCID

- 建议英文主标题：**FirstCare: Fact Verification and Interactive Clarification for Trustworthy Medical Triage**（沿用 `project_introduction.md` 已定英文名，中文描述性副题不进入英文论文）。
- 全体作者按实际贡献署名，附 ORCID；**提交后不可变更署名**。

### 1. Introduction & Motivation（约 0.75 页）

**写作要点（对应真实痛点与真实设计动机）：**
- 问题：患者主诉口语化且常被表面诱因误导；通用 LLM 在医疗导诊中存在无凭据确诊式幻觉与急危重症漏判风险。
- 本文做什么：构建了基于中国临床病例成果数据库（CMCR）的真实病例资源与口语化导诊查询数据集 **CMCR-Colloquial**，并实现一个以双问题事实核验（two-question fact verification）为核心的检索—核验—生成管线，用于演示如何将"未提及"的症状在进入生成器前物理剔除，并将存疑要素转化为交互式追问。
- 明确声明 demo 的 **purpose**（演示 grounded fact verification 在医疗导诊中的端到端价值）、**scope**（中文病例、六类症候群：发热/腹痛/呼吸困难/胸痛/头痛/意识异常）、**audience**（IR 与医疗 NLP 研究者、fact verification 与 grounded generation 系统开发者）。

**素材来源**：`project_introduction.md` 第一节、`项目工作实况说明.md` 第一节。

### 2. Data Resource: CMCR-Colloquial（约 0.75 页）

**写作要点（全部为已完成工作）：**
- 数据源与采集：对 CMCR（cmcr.yiigle.com）站点接口做逆向工程，经 TLS 指纹规避方案抓取 1,543 例真实中文临床病例报告（HTML+JATS XML 双存档）。
- 结构化：JATS XML → 七槽位结构化 JSON，1,341 例通过完整性校验入库。
- 命题化：LLM 流水线拆解出 **43,271 条原子临床命题**（平均 32.3 条/例，23.0% 为红旗命题），每条含中英双语表述与四组判别关键词。
- 口语化逆向构造：四阶段流水线（金标准抽取 → 患者口语主诉逆向构造 → 通俗导诊输出逆向构造 → 专家审核状态机），产出 **1,339 条口语化问答对**，分层 5 折切分（seed=42）。
- **如实限定一句**：目前 1,339 条记录处于 draft 状态，医学专家审核正在进行中/尚未锁定——按实际进度措辞，若投稿前完成部分审核则据实更新。

**素材来源**：实况说明第二节、第三节；数据管线四份源码文件行数可作实现规模佐证。

### 3. System Architecture（约 1 页，配 Figure 1 架构图）

**写作要点（按四模块写，全部对应已实现代码）：**

1. **Stage 1 Hybrid Candidate Retrieval**：字符级 n-gram BM25 与 TF-IDF 等权混合，索引 1,341 例文档（447,089 词条），单查询约 64ms；**5 折全量** Recall@50 均值 0.941 / fold 0 MRR=0.826。
2. **Ablation note（负结果如实写；素材已超 4 页容量，按优先级取舍）**：
   - LoRA 微调 5 折 Recall@50 均值 0.197（fold 0 为 0.237），灾难性遗忘；
   - RRF 融合：与零样本稠密检索融合最优 Recall@50=0.9477（+0.7pt，fold 0 达 0.959），但 MRR 0.827→0.487、gold 在 Top-50 内被推深——恰好论证 Stage 2 精排的必要性；
   - max_len 512→1024 探针：0.426→0.224，截断假说否定；
   - 页面预算建议：正文各写一句即可，完整数字放入开源仓库/技术报告。
3. **Stage 2 Pointwise Reranking**：Jev 判别式打分链（神经判别 → LLM 评分 → 词面覆盖率离线兜底），结果持久化。
4. **Two-Question Fact Verification（核心）**：对每条候选命题执行 Mention 检查（p_men）与 Support 检查（p_sup），阈值 τ_f=0.7 三态划分 supported / gap / absent；强调 v2 协议（要素级提问）经端到端真实 API 联调修正的过程——这本身是可写的实现经验；口语归一化词表以对照行追加、不改写患者原话。
5. **Grounded Generation with Hard Gates**：absent 命题在进入生成器前被物理剔除并导出为前端追问 chips；红旗命题 supported 即触发"立即急诊"升级硬规则（模板层实现 + LLM 输出禁语清洗审计字段）；五步式结构化输出（分诊结论/破除表面诱因/事实映射/边界条件/下一步建议）；模板与 LLM 双模式。
6. **Supported 双栏拆分与数值守卫**（Task 7 评测根因分析驱动的后端修正）：Jev supported 须同时通过确定性词面锚点才计入"患者已确认"栏，否则降为"病例待核对特征"栏（严禁生成"您具备/您患有"类表述）；数值/时间限定词守卫剔除患者未陈述的具体值（如"2 年前/38.4℃"）。动机可如实写：端到端评测发现 Jev 对复合/否定命题存在精率问题（缓存实证："无皮疹"被判 supported p_sup=0.85），确定性规则作第二道确认。

**素材来源**：实况说明第三、四节；`stage1_retriever.py`、`stage2_jev_reranker.py`、`jev_fact_verifier.py`、`deductive_generator.py`。

### 4. Demonstration（约 0.75 页，配 Figure 2 界面截图）

**写作要点（前端已同步真实管线，本节较昨版实质性升级）：**
- 演示形态：**单文件浏览器端 demo** `medir_triage_ui_real.html`（11.3 MB，双击即用）——内嵌 1,341 例瘦语料与 16,084 条命题关键词，浏览器端逐行移植完整管线（Stage 1 混合召回 → Stage 2 精排 → 双问题核验 + 硬闸门 → 五步生成），无需服务端。
- 可靠性证据（可写入论文的强声明）：JS 移植与 Python 后端经对拍脚本逐位一致（5 场景 top-2 / 三态计数 / urgency / route / 引用 / chips / 五步文本全 PASS），可声明 the in-browser demo reproduces the backend pipeline exactly。
- 双模式演示：**离线模式**（确定性兜底链，结果可复现）与**神经模式**（Jev TypeSafe 直连 + 智算云 LLM，API Key 现场配置；逐级回落 Jev→LLM 评分→词面兜底，生成失败回落模板并留痕）——回落链路与后端行为一致。
- 演示流程：口语主诉输入（如"胸口像被大石头压着"→ 口语归一化 → 胸痛要素 p_men=0.95/p_sup=0.78）→ 检索候选 → 三态徽标（supported/gap/absent）→ 追问胶囊 → 多轮陈述拼接重算 → 五步导诊输出 + 红旗警示弹窗；中英双语切换。
- **投稿前唯一硬性动作**：将单文件页托管至可公开访问的 URL（或录制演示视频）。与昨版"须先接入后端"相比，接入与对拍已完成，**仅剩托管**。
- 开源声明（若完成 git 初始化与脱敏）：提供源码仓库 URL，作为评审加分项。

**素材来源**：`medir_triage_ui_real.html`、`build_frontend_real.py`、`compare_backend_frontend.py`、`smoke_test_pipeline.py`、《可视化平台设计说明.md》第 4 节。

### 5. Status and Limitations（约 0.5 页，单盲评审下的加分写法）

**写作要点（诚实呈现，demo track 评审重视真实性与可用性）：**
- 已完成：数据管线全量；检索主线 **5 折**实测（均值 0.941）；核验协议 v2 经真实 API 联调；生成器可复现 CLI；**端到端评测完成首轮**（30 条分层抽样，Veto 两层量表，产物已导出 LaTeX 表）；前端与后端逐位对拍。
- 局限，逐条如实写：
  - 端到端评测样本量 30，LLM 裁判与生成器共用模型池，存在自我偏好风险，分数仅作相对参考（Table 2 caption 保留此声明）；
  - 红旗组遗留问题：10 例红旗中 3 例未触发升级（Jev 对红旗命题召回不全），其中 1 例被裁判判 Veto 2；Veto 4 残余 1 例（LLM 越过事实清单捏造症状）——已定位机制（双栏拆分 + 数值守卫为应对措施），修复与重跑进行中；
  - 口语化数据集的专家双盲审核尚未锁定（1,339 条 draft）；
  - 密集检索 LoRA 为负结果（5 折 0.197），修正超参扫描未执行；
  - Jev 精排与核验的规模化延迟/成本统计尚未系统开展。
- 这样写的理由：单盲评审中，与可查证仓库一致、边界清晰的 limitation 声明比夸大结论更可信；同时规避"引用占位数字"的学术诚信风险（ACM 抄袭/失实政策）。

### 6. Conclusion（约 0.25 页）

- 一句话重申贡献：真实病例数据资源（1,543 例 → 1,341 例 → 43,271 命题 → 1,339 口语问答对）+ 双问题事实核验驱动的低幻觉导诊演示系统（浏览器端与后端管线逐位一致；端到端评测 Veto1/Veto3=0，引用病例 100% 有效）；邀请社区现场体验并反馈。

### References

- 只引用可核验文献：Jev/事实核验相关工作、医疗导诊与 triage 相关 IR 文献、LNCS 数据集论文先例等，撰写时逐条经 WebSearch 核实后编号；不引用博客作为学术依据（Jev 官方博客仅作脚注性质的系统说明源，不作为学术引用主体）。

---

## 篇幅预算汇总

| 章节 | 页数 | 图表 |
|---|---|---|
| 1 Introduction & Motivation | 0.75 | — |
| 2 Data Resource | 0.75 | — |
| 3 System Architecture | 1.0 | Figure 1 架构图 |
| 4 Demonstration | 0.75 | Figure 2 界面截图 |
| 5 Status and Limitations | 0.5 | Table 1 端到端评测（英文改写自 `out/table2_pipeline_eval.tex`） |
| 6 Conclusion | 0.25 | — |
| **合计** | **4.0** | 2 图 + 1 表（附录不使用，避免占用页数） |

> 篇幅提醒：新增素材（5 折检索、RRF 融合、len1024 探针、双栏拆分、端到端评测）已超 4 页容量。优先级：端到端评测表 > 5 折检索主结果 > LoRA 负结果一句 > RRF 一句；len1024 探针与完整融合数据不放正文，存入开源仓库。

---

## 投稿前待办清单（按硬性程度排序）

1. **【硬性】** 托管 `medir_triage_ui_real.html`（单文件、无需服务端）取得公开 URL，或录制演示视频；
2. **【硬性】** 按本文档"诚信红线"核对全文每个数字与每句结论；端到端评测表可直接复用 `out/table2_pipeline_eval.tex`，caption 的 LLM-judge 自我偏好声明必须保留并译为英文；
3. **【硬性】** 使用 Springer LNCS LaTeX 模板排版，控制在 4 页正文 + 参考文献不限；
4. **【建议】** 决定红旗召回遗留问题（3/10 未触发升级、1 例 Veto 2）是否在截稿前修复并重跑评测——不修则按现状如实报告；
5. **【强烈建议】** 初始化 git、移除 `.env`、开源仓库并获取 URL；
6. **【建议】** 完成部分数据集专家审核以更新第 2 节措辞（时间不允许则按 draft 现状如实表述）；
7. **【流程】** 11 月 2 日前经 EasyChair 提交；确认通讯作者与 Consent-to-Publish 签署一致。
