# MedIR-CMCR 开发 Agent 终极工程实施计划书 (Master Agent Development Specification)

> **文档用途**：本计划书专为**自动化代码开发 Agent**（如 Claude Code、Cursor Agent、Windsurf、OpenHands 等）及项目主控工程师编写。开发 Agent 应严格按照本文档定义的**目录架构、Pydantic/JSON 数据契约、数学公式、System Prompts、模块接口签名及分阶段验收标准（Acceptance Criteria）**逐项实现代码，严禁擅自简化字段或预设固定科室。

---

## 一、项目全局定义与核心技术栈

### 1.1 项目定位与对标基准
* **项目代号**：`MedIR-CMCR`（论文拟定名：*FirstCare: Fact Verification and Interactive Clarification for Trustworthy Medical Triage*）
* **目标顶会**：[ECIR 2027 Call for Demos](https://www.ecir2027.co.uk/call-for-demos) / IR for Good
* **方法路线**：将 [Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev) 双步事实核验引入医疗导诊场景——以 [中国临床案例成果数据库 (CMCR)](https://cmcr.yiigle.com/index) 为循证底座，构建"病例检索 + 临床要素演绎核验"的低幻觉导诊管线。
* **核心铁律**：
  1. **100% 聚焦 CMCR 原生结构**：所有病例知识均从 CMCR（如标杆病例 [cmaid/1673770](https://rs.yiigle.com/cmaid/1673770)）的原生临床章节提取，彻底移除 MIMIC-IV-ED 等外部异构英文库；
  2. **零预设科室（Zero Hardcoded Departments）**：分诊目标科室与多学科协作路径（如“急诊科抢救低氧 $\rightarrow$ 血液内科骨髓确诊与依托泊苷化疗 $\times$ 风湿免疫科鉴别排除”）完全由检索命中的 CMCR 病例真实救治链动态派生；
  3. **Jev 双问题事实判断阻断幻觉**：任何候选临床要素 $f_j$ 必须经过 Jev 的 `Mention` ($p_{\text{men}}$) 与 `Support` ($p_{\text{sup}}$) 双重概率核验（阈值 $\tau_f = 0.7$），划分为 `supported`、`gap`、`absent` 三态；**`absent` 要素在进入生成模型前必须从物理上下文中硬性剔除**，彻底根除大模型脑补患者症状或无凭据确诊的幻觉。

### 1.2 症候群覆盖范围（Scope Definition）
本系统**不再聚焦单一发热主诉**，按以下两层结构组织病例池：

* **核心导诊主诉（两大类）**：
  1. **发热**：以标杆病例 1673770 为锚，另补充至少 1 例发热类鉴别病例，构成"常见良性病因 vs. 易误诊危重病因"的 Near-Miss 对照结构；
  2. **腹痛**：补充至少 3 例 CMCR 腹痛病例，覆盖常见病因（如急性胃肠炎、胆囊炎、阑尾炎）与至少 1 例易被表面诱因掩盖的危重病因，用于检验"破除表面诱因认知误区"的演绎能力。
* **红旗症候群案例池（四类急危主诉）**：从 CMCR 抓取若干急诊病例作为红旗（Red-Flag）案例，每类至少 1 例、合计不少于 4 例：
  1. **胸痛**（如急性冠脉综合征、主动脉夹层、肺栓塞类病例）；
  2. **呼吸困难**（如重症肺炎、张力性气胸、肺栓塞类病例）；
  3. **意识改变**（如脑卒中、糖尿病酮症酸中毒昏迷、肝性脑病类病例）；
  4. **剧烈头痛**（如蛛网膜下腔出血、颅内感染类病例）。
* **红旗案例的专属用途**：红旗病例不参与日常导诊的常规候选池排序展示，而是承担两项硬性职责——(a) 评测 `Veto 2（反向致命结论）`：凡患者陈述命中红旗症候群，系统不得给出"居家观察/普通慢诊"结论；(b) 验证生成器步骤 (iv) 的急危升级逻辑（详见 Task 6）。
* **遴选标准（对所有新增病例生效）**：公开发表、原生 11 章节完整、含明确最终诊断与鉴别诊断、救治链可溯源（保留 CMCR URL）；抓取后统一入 `data/raw_cmcr/`。

### 1.3 工程环境与外部接口规范 (`.env.example`)
开发 Agent 需在项目根目录创建 `.env.example` 与配置加载器 `src/config.py`，支持以下环境变量：

```ini
# 1. 并行智算云 MaaS API (OpenAI SDK 兼容接口，用于生成、逆向改写与 Rubric 评测)
PARATERA_API_KEY="your_paratera_api_key"
PARATERA_BASE_URL="https://llmapi.paratera.com/v1"
PARATERA_GEN_MODEL="DeepSeek-V3"          # 生成模型
PARATERA_DECOMPOSE_MODEL="DeepSeek-R1"    # 离线拆解原子事实命题的强推理模型
PARATERA_JUDGE_MODEL="Qwen-2.5-72B-Instruct" # 100 分制量表打分的评委模型

# 2. Jev 神经判别器与重排接口（TypeSafe AI System One 判别模型 jev-latest）
#    判别型模型：state + 类型化 questions 并行评估 → 类型安全校准概率（70-500ms，
#    无文本生成、不可能类型错误）。Noul 原语返回 0-1 概率，用于 Two-Question
#    核验（Task 5）与 Pointwise 相关性打分（Task 4.2）。
#    Key 获取（二选一）：
#    a) DMXAPI 聚合通道（国内直连、人民币计价，推荐）：https://www.dmxapi.cn 注册充值 →
#       JEV_BASE_URL="https://www.dmxapi.cn/typesafe/v1/systemone"，JEV_API_KEY 填 sk- 开头的 DMXAPI Key，
#       协议与官方逐字一致（原生 TypeSafe 协议透传，非 chat 模拟）；jev-latest 自动指向最新版本
#    b) TypeSafe 官方 early access：https://typesafe.ai 申请 → https://console.typesafe.ai/keys 创建
#    官方 SDK：pip install typesafe-sdk（官方直连时可用；第三方通道走 HTTP 直连）
JEV_API_KEY="your_typesafe_api_key_optional"   # 亦可写 TYPESAFE_API_KEY
JEV_BASE_URL="https://api.typesafe.ai/v1/systemone"
JEV_MODEL="jev-latest"
JEV_MODE="hybrid_deterministic_or_jev"    # 无 Key 时自动切换本地高保真确定性核验器
JEV_CONFIRMATION_THRESHOLD=0.70           # 确认阈值 tau_f = 0.7

# 3. 向量召回模型配置 (Stage 1 Dense Retriever)
EMBEDDING_MODEL_NAME_OR_PATH="Qwen/Qwen3-Embedding-4B"
USE_LIGHTWEIGHT_BM25_FALLBACK="true"      # 在无 GPU 的演示容器上自动启用字符 N-gram + BM25 混合召回
```

---

## 二、目标代码仓库完整目录结构 (Repository Tree)

开发 Agent 必须严格按照以下目录树组织全部代码、数据、脚本与测试：

```text
medir-cmcr/
├── .env.example                          # 环境变量模板
├── requirements.txt                      # Python 依赖包清单
├── README.md                             # 项目说明与快速启动指令 (供 ECIR 审稿人查阅)
├── data/
│   ├── raw_cmcr/                         # 存放从 CMCR 抓取或导入的原始病例 HTML/JSON (含 1673770 等)
│   ├── structured_cases/                 # Stage 1 产出的原生结构化病例库 (含离线拆解的临床命题集 F)
│   │   └── cmcr_case_1673770_structured.json
│   ├── cmcr_colloquial/                  # Stage 2-4 产出的口语化导诊数据集 (CMCR-Colloquial)
│   │   ├── train_synthetic_pairs.jsonl   # 用于微调检索器的 5.7k 合成口语查询-病例对 (含 N=7 负样本)
│   │   └── test_colloquial_cases.json    # 经医学专家 Stage 4 双盲锁定的测试集
│   └── cache/                            # 持久化缓存目录 (缓存 Jev 并发打分与 Embedding 向量)
│       └── jev_cache.sqlite
├── src/
│   ├── __init__.py
│   ├── config.py                         # 全局配置与环境变量管理
│   ├── schemas.py                        # Pydantic 数据模型契约 (严格定义 5 大核心数据结构)
│   ├── data_pipeline/
│   │   ├── cmcr_native_extractor.py      # Task 1: CMCR 原生 11 章节确定性解析器
│   │   ├── proposition_generator.py      # Task 2: 离线查询无关临床事实命题拆解器 (公式 2)
│   │   └── colloquial_constructor.py     # Task 3: 四阶段口语化数据集逆向构造与 5 维质控流水线 (四阶段口语化流水线)
│   ├── retrieval/
│   │   ├── stage1_retriever.py           # Task 4.1: Stage 1 粗召回 (Qwen3-Embed-4B LoRA / 字符级 BM25)
│   │   └── stage2_jev_reranker.py        # Task 4.2: Stage 2 Pointwise Jev 精排器 (并发 API + SQLite 缓存)
│   ├── verification/
│   │   └── jev_fact_verifier.py          # Task 5: Two-Question Jev 事实判断引擎 (Mention & Support -> Eq. 3)
│   └── generation/
│       └── deductive_generator.py        # Task 6: 五步式受控演绎导诊回答与追问选项生成器
├── experiments/
│   ├── evaluate_retrieval.py             # Task 7.1: 产出检索评测 Table 1 (Recall@5..50, MRR@3..10)
│   ├── evaluate_generation.py            # Task 7.2: 产出生成评测 Table 2 (一票否决率 Veto% + R1-R6 100分制消融实验)
│   └── export_latex_tables.py            # 一键将实验 JSON 导出为 Springer LNCS 标准 LaTeX 表格
├── app.py                                # Task 8: 前端（早期内联 demo，已废弃；将按真实管线重做） 
└── tests/
    ├── test_cmcr_extractor.py            # 单元测试：验证病例 1673770 原生章节解析完整性
    ├── test_jev_verifier.py              # 单元测试：验证 supported / gap / absent 三态判定准确率 = 100%
    └── test_end_to_end.py                # 集成测试：验证从患者口语输入到防幻觉生成的全链路
```

---

## 三、核心数据契约规范 (`src/schemas.py`)

开发 Agent 在编写任何业务逻辑前，必须先在 `src/schemas.py` 中使用 `pydantic` 实现以下 5 个核心数据模型：

### 3.1 模型 1：CMCR 原生结构化病历 (`CMCRNativeCase`)
严格对齐 [CMCR 病例 1673770](https://rs.yiigle.com/cmaid/1673770) 的原生小节，不设任何预设科室枚举：

```python
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

class VitalSigns(BaseModel):
    temperature_c: Optional[float] = Field(None, description="体温 (℃)")
    systolic_bp_mmhg: Optional[int] = Field(None, description="收缩压 (mmHg)")
    diastolic_bp_mmhg: Optional[int] = Field(None, description="舒张压 (mmHg)")
    heart_rate_bpm: Optional[int] = Field(None, description="心率 (次/min)")
    respiratory_rate_bpm: Optional[int] = Field(None, description="呼吸频率 (次/min)")
    spo2_percent: Optional[float] = Field(None, description="脉搏血氧饱和度 (%)")

class ClinicalProposition(BaseModel):
    """对应公式 (2): F = {(f_j, d_{s_j})}_{j=1}^M"""
    prop_id: str = Field(..., description="命题唯一ID，如 F_1673770_01")
    source_cmaid: str = Field(..., description="所属源病例编号 d_{s_j}")
    element_category: str = Field(..., description="要素类别：核心症状与诱因 | 既往病史 | 阳性查体 | 急危红旗征 | 治疗反应")
    proposition_zh: str = Field(..., description="单句、通俗、可独立核验的临床事实命题 f_j (中文)")
    proposition_en: str = Field(..., description="单句可验证临床事实命题 (英文对照，供论文与审稿人展示)")
    mention_keywords: List[str] = Field(default_factory=list, description="触发 Mention 检查的关键词列表")
    affirm_keywords: List[str] = Field(default_factory=list, description="触发阳性肯定的关键词列表")
    negate_keywords: List[str] = Field(default_factory=list, description="触发明确否定的关键词列表")
    uncertain_keywords: List[str] = Field(default_factory=list, description="触发未知/未检查的关键词列表")
    is_red_flag: bool = Field(False, description="是否为危及生命的急诊红旗征要素")
    clarification_question_zh: str = Field(..., description="当该命题为 absent/gap 时向患者展示的追问话术")
    follow_up_chip: str = Field(..., description="前端一键点击的追问胶囊按钮短语")

class InvolvedSpecialty(BaseModel):
    specialty: str = Field(..., description="由病例真实救治链产生的科室名称，不预设固定分类")
    role: str = Field(..., description="该科室在该病例诊治链中的具体职能")

class CMCRNativeCase(BaseModel):
    cmaid: str
    doi: Optional[str] = None
    title: str
    url: str
    reporting_institution: str
    reporting_department: str
    keywords: List[str]
    structured_abstract: Dict[str, str]
    clinical_record: Dict[str, Any]  # 包含 patient_demographics, chief_complaint, history_of_present_illness, past_medical_history, physical_examination, initial_emergency_management, auxiliary_examinations, differential_diagnosis, final_diagnoses, treatment_course_and_regimen, clinical_outcome_and_follow_up, discussion_and_clinical_pearls
    propositions: List[ClinicalProposition] = Field(..., max_length=100, description="离线拆解的查询无关原子命题集 F (M <= 100)")
    involved_specialties: List[InvolvedSpecialty]
```

### 3.2 模型 2：Jev 双问题事实核验结果 (`JevVerifiedFact`)
公式 (3)：

```python
from typing import Literal

class JevVerifiedFact(BaseModel):
    prop_id: str
    source_cmaid: str
    proposition_zh: str
    proposition_en: str
    is_red_flag: bool
    p_men: float = Field(..., ge=0.0, le=1.0, description="Step 1: Fact Mention 概率 p_men(f)")
    p_sup: float = Field(..., ge=0.0, le=1.0, description="Step 2: Fact Support 概率 p_sup(f)")
    role: Literal["supported", "gap", "absent"] = Field(
        ...,
        description="supported (p_sup >= tau_f); gap (p_sup < tau_f and p_men >= tau_f); absent (p_men < tau_f)"
    )
    matched_evidence_span: List[str] = Field(default_factory=list, description="患者查询中命中的文本片段")
    follow_up_chip: str
```

---

## 四、分模块开发任务书 (Task-by-Task Engineering Instructions)

开发 Agent 需按顺序执行以下 **8 个开发任务（Task 1 – Task 8）**。每完成一个任务，必须运行对应的验收命令通过后方可进入下一任务。

---

### Task 0：CMCR 多症候群病例池抓取与遴选（数据获取前置任务）

* **任务目标**：按 1.2 节定义的症候群覆盖范围，从 CMCR 抓取并遴选新增病例，替换"单一发热病例"的旧数据形态。
* **抓取清单（最低配额）**：
  | 类别 | 数量 | 遴选要求 |
  | :--- | :--- | :--- |
  | 发热类 | ≥ 2 例（含标杆 1673770） | 覆盖"普通感染 vs. 血液系统/风湿性疾病"鉴别对照 |
  | 腹痛类 | ≥ 3 例 | 至少 1 例为易误诊危重病因，其余为常见良性病因（构造 Near-Miss Distractor） |
  | 红旗类·胸痛 | ≥ 1 例 | 急诊起病，救治链含急救处理与多科室协作 |
  | 红旗类·呼吸困难 | ≥ 1 例 | 同上 |
  | 红旗类·意识改变 | ≥ 1 例 | 同上 |
  | 红旗类·剧烈头痛 | ≥ 1 例 | 同上 |
* **实现要求**：
  1. 抓取脚本输出统一存放 `data/raw_cmcr/`，文件名含 cmaid，保留原始 HTML 与页面 URL 溯源字段；
  2. 每例入库前运行完整性预检：缺失【主诉】【最终诊断】【鉴别诊断】任一章节即剔除并记录日志；
  3. 建立 `data/raw_cmcr/case_pool_index.json` 索引，登记每例的 `cmaid / 症候群类别 (fever | abdominal_pain | red_flag_chest_pain | red_flag_dyspnea | red_flag_consciousness | red_flag_headache) / URL / 入库状态`，供下游 Task 1–3 与评测分层使用。

### Task 1：开发 CMCR 原生章节确定性抽取器 (`src/data_pipeline/cmcr_native_extractor.py`)

* **任务目标**：利用 CMCR 病例报告高度结构化的特点，直接按原生章节标题将原始文本或 HTML 解析为 `CMCRNativeCase` 基础结构。
* **核心实现要求**：
  1. 编写函数 `parse_cmcr_case(raw_input: dict | str) -> dict`，精确切分以下 11 个原生小节：
     * `一般资料`（提取性别、年龄、入院时间与首诊科室）
     * `主诉`（拆分为 `symptom` 与 `duration`；同时按症候群索引标注该例所属类别：发热 / 腹痛 / 四类红旗之一）
     * `现病史`（提取起病诱因 `trigger` 及各系统伴随症状：发热类提取最高体温 `peak_temperature_c`、是否寒战 `chills`；腹痛类提取疼痛部位、性质与放射、恶心呕吐、腹泻/便血、腹膜刺激征等；红旗类提取红旗症状的起效时间与严重程度描述；通用提取各系统伴随症状）
     * `既往史`（提取既往确诊疾病如“成人Still病”、长期口服药物如“泼尼松 1.25 mg/d”、手术过敏史）
     * `体格检查`（用正则精确提取数值型生命体征：`体温 ([\d\.]+)℃`、`血压 (\d+)/(\d+)\s*mmHg`、`心率 (\d+)次/min`、`呼吸 (\d+)次/min`、`血氧饱和度 ([\d\.]+)%`，以及浅表淋巴结、皮疹与心肺腹查体）
     * `初步诊疗`（提取首诊拟诊假设 `presumed_initial_diagnosis`，如“急性胃肠炎可能”，以及初步治疗反应）
     * `辅助检查`（分类提取血常规/炎症指标 WBC/PLT/CRP/PCT、铁蛋白与凝血指标、病原学阴性结果、胸腹部 CT 及骨髓/淋巴结活检病理）
     * `诊断与鉴别诊断`（提取最终诊断列表 `final_diagnoses`，以及针对感染性疾病、非感染性风湿病、肿瘤性疾病的逐条鉴别排除理由）
     * `诊疗经过`、`临床转归` 与 `讨论`（提取思维定势陷阱 `diagnostic_pitfall` 与临床警示 `clinical_takeaway`）。
  2. **防幻觉校验**：如果原始病历缺失【主诉】、【最终诊断】或【鉴别诊断】任一核心小节，抛出 `ValueError("Incomplete CMCR source record")` 并终止入库。

---

### Task 2：开发离线查询无关临床事实命题拆解器 (`src/data_pipeline/proposition_generator.py`)

* **任务目标**：实现公式 (2)（查询无关的条件事实拆解），将每篇 CMCR 病例独立拆解为 $M$ 条（$M \le 100$）单句、通俗、可验证的原子临床命题 $\mathcal{F} = \{(f_j, d_{s_j})\}_{j=1}^M$。
* **关键设计原则**：
  * **查询无关（Query-Agnostic）**：拆解仅依赖 CMCR 病例自身内容，与患者实时输入无关，因此可在构建知识库时**一次性离线运行并缓存到 JSON 文件中**；
  * **禁止跨病例合并**：每条命题 $f_j$ 严格绑定唯一源病例 `source_cmaid`，且只包含一个可独立核验的临床要素（症状、诱因、既往史、阳性查体、危急值红旗征或抗感染反应）。
* **内置 System Prompt（供调用 `PARATERA_DECOMPOSE_MODEL` 批量拆解新病例时使用）**：
  ```text
  你是一名严谨的临床医学知识工程专家。给定一篇《中国临床案例成果数据库 (CMCR)》的结构化病例报告 d_s，请将其拆解为决定该病例鉴别诊断、排除误诊定势及触发急诊预警所需的原子化临床事实命题集合 F = {f_1, f_2, ..., f_m}。
  要求：
  1. 每条命题 f_j 必须改写为单句、面向普通患者可核验的陈述句（包含中文 proposition_zh 与英文 proposition_en）；
  2. 必须覆盖 5 个维度：(a) 核心症状与起病诱因；(b) 关键既往史与用药史；(c) 特征性体表查体（如浅表淋巴结、特征性皮疹）；(d) 危及生命的急诊红旗征（如 SpO2 <= 92%、休克、抽搐、喷射呕吐、胸痛、呼吸困难、意识改变、剧烈头痛）；(e) 常规经验性治疗反应（如抗生素无效）；
  3. 为每条命题配套生成 mention_keywords、affirm_keywords、negate_keywords、uncertain_keywords 以及当该命题未被患者提及时用于主动追问的 clarification_question_zh 和快捷按钮短语 follow_up_chip；
  4. 不要合并不同维度的复合命题，仅对完全同义的命题去重。输出严格符合 ClinicalProposition 列表的 JSON 格式。
  ```

---

### Task 3：开发四阶段 `CMCR-Colloquial` 口语化数据集逆向构造流水线 (`src/data_pipeline/colloquial_constructor.py`)

* **任务目标**：将专业的 CMCR 病例报告转化为贴近真实患者日常咨询的口语化问答对，用于微调检索器与执行端到端评测。
* **四阶段流水线实现细节**：
  1. **Stage 1（Gold-Case Extraction & Verification）**：加载经 Task 1 & Task 2 生成并由医学同学核验通过的 `CMCRNativeCase`；
  2. **Stage 2（Reverse Construction of Colloquial Patient Input）**：
     * 引导 LLM 将病例中的【主诉 + 现病史 + 起病诱因 + 部分初始体征】逆向改写为**第一人称患者口语化求医叙述**（平均约 200–300 字）：保留决定性时间线与表面诱因（如“前天吃了冰葡萄后发烧40度、肚子胀一直吐”，腹痛类如“半夜吃完火锅后右上腹绞痛”），加入真实患者的情绪表达与日常口语噪声，**严禁泄露最终确诊疾病名称（如“T细胞淋巴瘤”、“嗜血细胞综合征”）或专业医学术语**；
     * **红旗类病例的口语化特殊要求**：红旗病例的口语叙述中必须自然嵌入红旗症状的日常表述（如“胸口像被大石头压着喘不上气”“家里人叫半天都叫不醒”“这辈子最剧烈的一次头痛”），且**不得**在叙述中自我诊断为具体疾病；此类样本专用于检验系统的急危升级逻辑与 `Veto 2` 否决行为；
     * **五维自动质控打分（总分 10 分）**：对每次改写按 `(1) 口语化语气 (2分)`、`(2) 专业医学术语掩蔽度 (2分)`、`(3) 核心临床事实充分性 (2分)`、`(4) 诊断方向保真度 (2分)`、`(5) 叙述自然度 (2分)` 打分。**总分 $< 8/10$ 自动触发重生成（最多 3 次）；若“事实充分性”或“诊断方向保真度”得 0 分则直接淘汰**；
  3. **Stage 3（Reverse Construction of Plain-Language Triage Output）**：
     * 将病例的【鉴别诊断 + 诊疗经过 + 讨论警示】改写为面向非医学背景患者的通俗导诊与就医建议，同样按 5 维量表（结论保真度、依据完整性、术语通俗度、表达自然度、就医可操作性）质控，$\ge 8/10$ 方可放行；
  4. **Stage 4（Expert Quality Verification 状态机）**：
     * 每条记录维护状态字段 `status: Literal["draft", "reviewed", "adjudicated", "locked", "rejected"]`，提供导出 CSV/JSON 供两位医学合作同学独立盲审及第三方仲裁锁定的接口。

---

### Task 4：开发两阶段检索与 Pointwise Jev 重排管线 (`src/retrieval/`)

* **任务目标**：采用 *Retrieve-then-Rerank* 两阶段架构，解决“患者日常口语描述”与“规范临床病例表述”之间的语义鸿沟，并把“真正吻合深层鉴别要件的黄金病例”从“仅表面症状相似的干扰项（Near-Miss Distractors，如普通胃肠炎）”中精准排到首位。
* **子模块 4.1：Stage 1 粗召回器 (`src/retrieval/stage1_retriever.py`)**：
  1. **双编码器微调模式（GPU 可用时）**：加载 `Qwen/Qwen3-Embedding-4B`，采用最后一个 Token 池化（Last-token pooling）与 $\ell_2$ 归一化计算余弦相似度 $s(q, d) = \text{Emb}(q)^\top \text{Emb}(d)$；对注意力投影层 $\{q, k, v, o\}_{\text{proj}}$ 应用 LoRA（$r=16, \alpha=32, \text{dropout}=0.05$），使用温度缩放对比交叉熵损失（公式 1，$\tau = 0.05$，每个正样本配 $N=7$ 个难负样本）训练；
  2. **轻量级混合召回回退模式（无 GPU / Hugging Face Spaces CPU 免费容器）**：使用字符级 Unigram/Bigram/Trigram + BM25 与 TF-IDF 余弦相似度混合打分，从语料库 $C$ 中毫秒级返回 Top-$K$（默认 $K=50$）候选病例列表。
  3. **路线决策（2026-10-29，方案 A）**：以**混合召回为 Stage 1 主线**。依据：fold 0 实测显示该规模语料下 naive LoRA 微调发生灾难性遗忘（Recall@50：混合 0.948 / 基座 dense 0.467 / 微调后 0.237），混合召回兼具高召回、毫秒级延迟与 CPU 免费容器可部署性；微调负结果作为消融实验报告（技术报告 5.3 节）。**双编码器微调降级为可选增强（方案 B 保留）**：修正超参（lr 2e-5/5e-5、r=8 仅 q/v、难负混随机负、1 epoch）的快速扫描留待后续，若取得正提升再升级主线。
* **子模块 4.2：Stage 2 Pointwise Jev 精排器 (`src/retrieval/stage2_jev_reranker.py`)**：
  1. 接收患者完整口语咨询 $q$ 与 Stage 1 召回的 $K$ 个候选病例；
  2. 对每个 $(q, d_i)$ 独立评估该病例的临床鉴别要素是否直接、特异地支撑患者的核心症候群与风险特征，输出连续相关性分数 $\text{score}_{\text{Jev}}(q, d_i) \in [0, 1]$；**打分实现为 Jev 的 Noul 原语**（TypeSafe System One 判别模型 `jev-latest`：state = 患者咨询 + 候选病例文本，单条 Noul 问题"该病例鉴别要件是否直接且特异地支撑患者核心症候群"→ 0-1 校准概率，70-500ms，无文本生成幻觉）；
  3. 使用 `ThreadPoolExecutor` 并发请求，并将 `(hash(q), cmaid)` 结果持久化写入 `data/cache/jev_cache.sqlite`，按得分降序截断返回 Top-$k$（默认 $k=10$）；打分链：Jev Noul（配置 Key 时）→ 并行智算云 LLM 评分（神经判别替代）→ 词面覆盖率兜底（完全离线）。

---

### Task 5：开发 Two-Question Jev 事实判断引擎 (`src/verification/jev_fact_verifier.py`)

* **任务目标**：实现本系统最核心的 **Two-Question Jev Fact Verification**（公式 3），通过显式验证患者叙述与候选临床命题的匹配关系来彻底阻断幻觉。
* **数学定义与三态划分逻辑**：
  对于 Top-$k$ 病例下的每一个候选临床命题 $f \in \mathcal{F}$，针对患者当前累积陈述 $q$ 执行两次独立核验查询，获取 $[0, 1]$ 区间的连续概率 $p_{\text{men}}(f)$ 与 $p_{\text{sup}}(f)$：
  1. **Question 1 — Mention Check ($p_{\text{men}}(f)$)**：
     *判别标准*：患者陈述 $q$ 是否提及或涉及了该临床要素（无论患者是肯定它、明确否定它，还是表示尚未检查/不清楚）？
  2. **Question 2 — Support Check ($p_{\text{sup}}(f)$)**：
     *判别标准*：患者陈述 $q$ 是否以第一人称明确肯定、预设或复述了该基础临床事实（症状、诱因、持续时间、既往病史、体征数值），使得该临床要素可被推断为成立？
  * **双问题协议 v2（要素级提问，工程实测修订）**：Jev 将整句复合命题作为整体判定，直接把命题全文嵌入问题会因患者未提及命题中的次要细节（如"晚餐后"）而整句否决 Mention（实测 $p_{\text{men}}=0.07\text{-}0.23$）。v2 从命题的 `mention_keywords[0]` 提取**核心要素短语**（如「胸痛」）构造要素级双问题，整句命题仅在 state 中作归属参考——实测口语隐喻查询（"胸口像被大石头压着"）下胸痛要素 $p_{\text{men}}=0.95, p_{\text{sup}}=0.78$，正确进入 `supported`。缓存键含协议版本号 `v2`，协议变更自动隔离旧缓存。
  * **口语归一化预处理层**：确定性词表（`COLLOQUIAL_NORMS`）将口语隐喻/俗称（"胸口像被大石头压着"→胸痛、"喘不上气"→气短/呼吸困难、"拉肚子"→腹泻等 9 类）映射为规范术语，以"口语表述对照"行追加于核验输入，不改写患者原始陈述、不直接抬高概率。
  3. **三态角色赋值公式（设定确认阈值 $\tau_f = 0.7$）**：
     $$\text{role}(f) = \begin{cases} \text{supported}, & p_{\text{sup}}(f) \ge \tau_f, \\ \text{gap}, & p_{\text{sup}}(f) < \tau_f \land p_{\text{men}}(f) \ge \tau_f, \\ \text{absent}, & p_{\text{men}}(f) < \tau_f. \end{cases}$$
* **工程实现双模要求**：
  * **Jev 神经判别模式（真实 System One 模型接入）**：配置 `JEV_API_KEY`（TypeSafe AI early access Key）后，每条命题对同一 state（患者陈述 + 命题文本）发起**一次 Jev 请求、并行携带两个 Noul 原语问题**（Question 1 Mention → $p_{\text{men}}$；Question 2 Support → $p_{\text{sup}}$），直接提取 Noul 校准概率（官方 SDK `typesafe-sdk` 或 HTTP 直连 `POST https://api.typesafe.ai/v1/systemone`，`ThreadPoolExecutor` 并发）；未配置 Key 或调用失败自动回落确定性规则模式；
  * **高保真确定性规则核验模式（离线/零延迟兜底）**：基于 `ClinicalProposition` 中的 `affirm_keywords`、`negate_keywords`、`uncertain_keywords` 与 `mention_keywords` 精确计算 $p_{\text{men}}$ 与 $p_{\text{sup}}$（命中 `negate_keywords` 或 `uncertain_keywords` 时 $p_{\text{men}} \ge 0.84, p_{\text{sup}} \le 0.35 \rightarrow \text{gap}$；命中 `affirm_keywords` 且无否定前缀时 $p_{\text{men}} \ge 0.86, p_{\text{sup}} \ge 0.83 \rightarrow \text{supported}$；未命中任何提及词时 $p_{\text{men}} < 0.20, p_{\text{sup}} < 0.05 \rightarrow \text{absent}$）。
* **防幻觉硬性护栏（Hard Gate）**：
  函数 `filter_facts_for_generator(verified_facts: List[JevVerifiedFact])` **必须将所有 `role == "absent"` 的命题从传给生成器的已确认上下文列表中彻底移除**，仅保留 `supported_facts` 与 `gap_facts`；同时将 Top-1/Top-2 病例中处于 `absent` 状态的命题按鉴别重要度导出为 `pending_clarification_chips`，供前端渲染为多轮追问按钮。

---

### Task 6：开发五步式演绎受控回答生成器 (`src/generation/deductive_generator.py`)

* **任务目标**：实现受控 *Grounded Answer Generation*，将医疗导诊建模为显式三段论演绎推理（大前提：CMCR 病例鉴别规范；小前提：经 Jev 核验的 `supported` 与 `gap` 患者事实；结论：分诊科室与就医建议）。
* **强制五步输出结构（在 System Prompt 与确定性模板中同时锁定）**：
  生成器仅接收三项输入：`(患者查询 q, Top-k CMCR 病例摘要与鉴别诊断, Jev 核验后的 supported 与 gap 事实列表)`，并严格按以下 5 个段落输出：
  1. **(i) 明确的方向性分诊结论（Directional Triage Conclusion）**：开门见山给出推荐就诊科室路径（如“急诊科首诊排查 $\rightarrow$ 血液内科/风湿免疫科协同”）及就医紧急度（立即急诊 vs. 门诊就诊）；
  2. **(ii) 破除患者表面诱因认知误区（Addressing Surface-Trigger Bias）**：直接回应患者可能存在的错误归因（例如：针对病例 1673770，明确指出“虽然发烧呕吐发生在吃冰葡萄之后，但结合基础病史绝不能简单当成普通急性胃肠炎处理”）；
  3. **(iii) 演绎事实映射分析（Deductive Mapping of Supported Facts）**：逐一将 Jev 判定的 `supported` 阳性事实映射到检索到的 CMCR 病例鉴别标准上，向患者透明展示“因为您具备事实 A 和 B，对应 CMCR 病例 X 的特征，所以需要重点排查 Y”；
  4. **(iv) 结论翻转边界与红旗征警示（Boundary Conditions & Gap/Red-Flag Warnings）**：针对处于 `gap`（患者否定或尚未检查）的关键要素，明确告知“目前您尚未确认或暂未出现某症状，一旦出现气短血氧低于 93%、浅表淋巴结肿大、流鼻血或抗生素治疗无效，病情风险将立即升级”；
  5. **(v) 可执行的下一步就医与检查建议（Actionable Next Steps）**：列出到院后建议优先完善的具体检查项目（如血常规看血小板、血清铁蛋白、胸部 CT 等）及就诊前注意事项，并附上简明 `[EN]` 英文摘要供国际审稿人阅读。
* **红旗急危升级硬性规则（Red-Flag Escalation Hard Rule）**：
  * 凡 Jev 核验判定任一 `is_red_flag = True` 的命题（涵盖胸痛、呼吸困难、意识改变、剧烈头痛四类红旗症候群及 SpO2/休克类体征命题）为 `supported`，生成器必须：步骤 (i) 的就医紧急度**强制置为“立即急诊”**，且**禁止**在结论中出现“居家观察”“普通门诊随诊”类表述；
  * 该规则在确定性模板层（非仅 System Prompt 层）实现，与评测实验的 `Veto 2（反向致命结论）` 一票否决联动——红旗组测试样本的 `Veto 2` 发生率必须为 0。
* **实现状态（已完成）**：`src/generation/deductive_generator.py` 交付双模式——LLM 模式（并行智算云，System Prompt 锁五步结构，多候选模型×重试调用链，输出经禁语清洗与红旗规则校验，不合规自动回落模板）与确定性模板模式（默认，零成本可复现）；输出容器 `DeductiveAnswer` 含五段正文、[EN] 摘要、紧急度、科室路径、红旗触发标记、引用病例编号与 `veto2_scrubbed` 审计字段。CLI 全链路演示：`python -m src.generation.deductive_generator --query "..." [--api] [--llm]`。

---

### Task 7：开发复现级评测与消融实验套件 (`experiments/`)

* **任务目标**：产出两组主实验（Table 1 & Table 2），编写可一键运行并导出 LaTeX 表格的自动化实验脚本。
* **实验 1：口语化病例检索与 Jev 重排评测 (`experiments/evaluate_retrieval.py`，产出 Table 1)**
  * **对比方法**：
    1. `Zero-shot LLMs`（直接让 LLM 生成匹配的病例/疾病类型并对齐至语料库 Top-10）；
    2. `Lexical / Dense Stage-1 Only`（仅使用第一阶段召回 Top-50）；
    3. `Two-Stage (+ Jev Reranking)`（第一阶段召回 Top-50 后经 Pointwise Jev 重排截断至 Top-10）。
  * **输出指标**：自动计算并打印 `Recall@5, R@10, R@20, R@30, R@50` 以及 `MRR@3, M@5, M@10`。
* **分层评测要求（适用于实验 1 与实验 2）**：
  * 测试集 `test_colloquial_cases.json` 必须按症候群分层标注：`发热组 / 腹痛组 / 红旗组（胸痛、呼吸困难、意识改变、剧烈头痛四子组）`；
  * Table 1 与 Table 2 除报告总体指标外，须分别报告各症候群分组的分层指标；
  * **红旗组硬性指标**：`Veto 2（反向致命结论）发生率 = 0`；红旗组样本的就医紧急度判定准确率（应判"立即急诊"的比例）须单独报告。
* **实验 2：端到端导诊回答生成与消融实验 (`experiments/evaluate_generation.py`，产出 Table 2)**
  * **三种消融设置（Ablation Settings）**：
    1. `Zero-shot`：仅输入患者口语主诉 $q$；
    2. `Cases-only`：输入 $q$ + 检索到的 Top-$K$ ($K \in \{5, 10, 15\}$) CMCR 病例原文，**不经过 Jev 事实拆解与双步核验**；
    3. `Full pipeline (Ours)`：输入 $q$ + Top-$K$ 病例 + **Jev 双问题核验生成的 `(supported, gap)` 事实结构（剔除 `absent` 事实）**。
  * **两层 100 分制专家量表（Two-Tier 100-Point Rubric）自动评分器**：
    * **第一层：四项医疗安全一票否决检查（One-Vote Veto，触发任一则总分强制记为 0 分，并计入 `Veto (%)`）**：
      1. `Veto 1 (Fabricated Case/Guideline)`：伪造不存在的 CMCR 病例编号或诊疗规范；
      2. `Veto 2 (Opposite/Dangerous Triage Direction)`：对含急危重症红旗征的病例给出“居家观察/普通慢诊”的反向致命结论；
      3. `Veto 3 (Unsafe Prescription)`：违规直接开具处方药具体剂量或教唆危险医疗行为；
      4. `Veto 4 (Fabricated Patient Facts)`：将 `absent`（患者未提及）或 `gap`（患者已否定）的症状捏造为患者既定阳性事实进行推理。
    * **第二层：六维 23 原子项细粒度打分（总分 100 分，在非 Veto 样本上求均值）**：
      * `R1 分诊结论与响应度 (Conclusion & Responsiveness)`：**9 分**
      * `R2 临床病例与指南依据覆盖 (Clinical Case & Normative Basis)`：**40 分**（其中核心黄金病例覆盖占 16 分）
      * `R3 鉴别诊断分析与演绎推理 (Differential Analysis & Deductive Reasoning)`：**17 分**
      * `R4 患者事实保真度与要点覆盖 (Factual Fidelity & Point Coverage)`：**18 分**
      * `R5 语言通俗度与就医实用性 (Expression & Practicality)`：**8 分**
      * `R6 医疗安全边界与合规提示 (Safety & Compliance Boundary)`：**8 分**

---

### Task 8：开发四步透明可视化平台 (`app.py`)

* **任务目标**：使用 Streamlit 打造兼具“患者友好交互”与“审稿人算法可解释性”的 Web 演示平台。
* **界面布局与交互要求**：
  1. **侧边栏（Sidebar）**：提供 Jev 确认阈值 $\tau_f \in [0.50, 0.95]$ 滑块（默认 `0.70`）、并行智算云 API Key / Base URL / 模型名称配置框、中英文字幕开关及会话重置按钮；
  2. **左栏（患者口语交互与五步演绎导诊区 — 左侧 `Layperson Query` & `04 Generated Answer`）**：
     * 顶部放置**分组式一键预设场景按钮**（按症候群分组渲染）：
       * **发热组**：场景 1：[CMCR-1673770](https://rs.yiigle.com/cmaid/1673770) 首轮不完整口语输入；场景 2：CMCR-1673770 补充低氧 SpO2 92% 与腋下淋巴结肿大的完备态；场景 3：吃冰葡萄呕吐但明确否定所有红旗征的单纯急性胃肠炎对照；
       * **腹痛组**：至少 1 个腹痛类预设（基于 Task 0 抓取的腹痛病例构造口语输入，含"表面诱因 + 危重信号并存"的 Near-Miss 样本与单纯良性病因对照样本）；
       * **红旗组**：4 个红旗快填胶囊按钮——**胸痛 / 呼吸困难 / 意识改变 / 剧烈头痛**，点击即在当前对话中注入对应红旗口语陈述（如“胸口像被大石头压着”），用于实时演示 Jev 将红旗命题判为 `supported` 后系统强制升级为"立即急诊"的过程；
     * 对话框底部动态渲染由 Jev 筛出的 `Absent` 高权重鉴别要素胶囊按钮（点击任意胶囊按钮即可模拟患者补充陈述，实时观察右侧命题从 `🚫 ABSENT` 跃迁为 `✅ SUPPORTED` 或 `⚠️ GAP`）；
  3. **右栏（Jev 演绎事实核验透明面板 — 右侧 `01` $\rightarrow$ `02` $\rightarrow$ `03`）**：
     * **面板 `01 Colloquial Case Retrieval & Jev Reranking`**：展示候选 CMCR 病例的 Pointwise Jev 得分进度条与排名；
     * **面板 `02 & 03 Case-Conditioned Elements & Two-Question Jev Fact Verification`**：以卡片形式逐条展示 Top-1 病例的原子命题 $f_j$、Step 1 Mention 概率 $p_{\text{men}}(f_j)$、Step 2 Support 概率 $p_{\text{sup}}(f_j)$、匹配到的患者原话片段，以及醒目的三态徽章（绿色 `✅ SUPPORTED`、黄色 `⚠️ GAP`、灰色 `🚫 ABSENT — 已阻断防幻觉`）。

---

## 五、分阶段自动化验收测试清单 (Verification & Acceptance Protocol)

开发 Agent 每完成一个阶段，必须运行以下终端命令并确保 100% 通过：

| 验收阶段 | 自动化测试命令 | 必须达成的硬性验收指标 (Pass Criteria) |
| :--- | :--- | :--- |
| **Gate 1: 原生抽取校验** | `python3 cmcr_native_extractor.py` | 成功解析病例池内**全部病例**（发热 ≥2、腹痛 ≥3、红旗 ≥4）的 11 个原生小节；标杆病例 1673770 准确提取 `temperature_c=39.1`, `spo2_percent=92.0`；`case_pool_index.json` 分层登记完整，且零预设科室硬编码 |
| **Gate 2: Jev 三态核验测试** | `python3 evaluate_fever_agent.py` | 1. Pointwise Jev Reranking Top-1 准确率 = **100.0%**；<br>2. Two-Question Jev Fact Role (`supported` / `gap` / `absent`) 分类准确率 = **100.0%**；<br>3. Full Pipeline 的一票否决率 `Veto (%)` 显著低于 Zero-shot 与 Cases-only，且 R2/R4 得分显著提升；<br>4. **红旗组 `Veto 2` 发生率 = 0**，且红旗样本均正确输出“立即急诊”升级结论 |
| **Gate 3: UI 编译与语法检查** | `python3 -m py_compile app.py` | 零语法错误；在无 API Key 与有 Paratera API Key 两种模式下均可正常启动，并完成发热组 3 场景、腹痛组预设与红旗组 4 个快填胶囊的全部动态交互（含红旗触发的急诊升级演示） |

---

## 六、给你的开发 Agent 的一键启动主提示词 (Master Prompt to Copy-Paste)

当你把本文件（`project_plan_medir_cmcr.md`）以及沙箱中已生成的标杆文件（`cmcr_case_1673770_structured.json`、`app.py`、`evaluate_fever_agent.py`）交给你的开发 Agent 时，直接复制粘贴以下指令即可让它全自动开工：

```text
请读取工作区中的《project_plan_medir_cmcr.md》（Master Agent Development Specification）以及现有的标杆实现文件（cmcr_case_1673770_structured.json, cmcr_native_extractor.py, app.py, evaluate_fever_agent.py）。
你的任务是严格按照计划书中的【1.2 症候群覆盖范围】、【二、目录结构】、【三、Pydantic 数据契约】和【四、Task 0 至 Task 8 工程规范】，依次完成全套模块化代码重构与扩展：
1. 系统不再聚焦单一发热主诉：按 1.2 节覆盖"发热 + 腹痛"两类核心导诊主诉，并完成胸痛、呼吸困难、意识改变、剧烈头痛四类红旗急危病例的抓取与入库（Task 0）；
2. 保持 100% 聚焦中国临床案例成果数据库 (CMCR) 原生结构，绝不预设固定科室；
3. 严格实现公式 (2) 的离线查询无关临床事实命题拆解，以及公式 (3) 的 Two-Question Jev Fact Verification (Mention p_men & Support p_sup, 阈值 tau_f = 0.7 -> supported / gap / absent)；
4. 确保所有 absent 事实在进入生成模型前被硬性截断丢弃，并自动转化为前端追问按钮；
5. 严格落实红旗急危升级硬性规则：任一 is_red_flag=True 命题被判为 supported 时，分诊结论强制"立即急诊"且禁止居家观察类表述（与 Veto 2 联动，红旗组 Veto 2 发生率必须为 0）；
6. 每完成一个 Task，自动运行【五、自动化验收测试清单】中的校验命令，全部通过后输出实验结果表与可运行 Demo。
```
