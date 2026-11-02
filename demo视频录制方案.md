# FirstCare Demo 录制方案 · 按钮最优点击顺序

> 适用对象：`medir_triage_ui.html`（模拟版 · 已注入真实 API 预跑结果）
> 生成时间：2026-11-02
> 录制时长建议：**2–3 分钟**

---

## 一、录制前准备（必读）

| 项 | 操作 |
|---|---|
| 打开文件 | 双击 `medir_triage_ui.html`（Chrome/Edge 干净无痕窗口，缩放 100%） |
| 等索引就绪 | 打开后**等 1–3 秒**，右上角徽标变为「真实管线就绪」（Stage-1 索引首次构建） |
| 语言 | 默认英文，**第一步先切到「中」**（左上角语言按钮），保证 UI 全中文 |
| 分辨率 | 浏览器窗口最大化，1920×1080 |
| 音频 | 建议配**英文旁白**（面向 ECIR 2027 国际评审），逐句脚本见下文「七、英文旁白脚本」；界面保持中文，仅旁白/字幕用英文 |

**核心卖点（录制要体现的 4 个链路）**：
1. Stage-1 混合召回（1341 例，毫秒级）
2. Stage-2 Jev 精排（真实 API 校准概率）
3. Task-5 双问题事实核验（supported / gap / absent 三态 + p_men/p_sup）
4. Task-6 五步受控演绎生成 + 红旗急危升级硬规则

---

## 二、按钮最优点击顺序（主线路 · 约 1 分 40 秒）

> 关键原则：**先展示「红旗触发的危急场景」，再展示「无红旗的常规场景」形成对比**，
> 这样能在一支视频里同时覆盖「红旗升级」与「防幻觉硬闸门」两个核心卖点。

### 镜头 1 —— 开场 + 切中文（0:00–0:08）
| 步骤 | 点击 | 目的 |
|---|---|---|
| 1.1 | 左上角 **「中」** 按钮 | 切中文 UI |
| 1.2 | （口播）系统定位：基于 CMCR 真实病例 + Jev 双问题核验的低幻觉医疗导诊 | 开场定调 |

> 🎙 **英文旁白（Scene 0 · Opening）**
> "FirstCare is a low-hallucination medical triage assistant built on a large Chinese clinical case corpus, combining hybrid retrieval with a two-question fact-verification gate before any answer is generated."

### 镜头 2 —— 红旗场景：压榨样胸痛（0:08–0:50）
| 步骤 | 点击 | 目的 |
|---|---|---|
| 2.1 | 主诉卡 → **「压榨样胸痛伴冷汗气短」** | 触发 chest 场景（真实 API 预跑，Top1=1671920） |
| 2.2 | 观察右侧 **01 检索精排** 标签（默认激活） | 展示 Stage-2 Jev 真实分数（0.84）与候选病例 |
| 2.3 | 点击 **02 命题拆解** 标签 | 展示 Top-1 病例的原子命题集 |
| 2.4 | 点击 **03 双问题核验** 标签 | 展示三态徽标 + 真实 p_men/p_sup（如 p_men=0.97/p_sup=0.79 supported） |
| 2.5 | 点击 **04 演绎导诊** 标签 | 展示五步生成 + 「立即急诊」红旗横幅 |
| 2.6 | **关闭红旗报警弹窗**（「我知道了」） | 展示红旗弹窗本身（这是关键镜头） |
| 2.7 | 展开 04 面板的红旗详情（点红旗横幅 summary） | 展示触发升级的红旗命题 + 硬闸门剔除的 absent 命题 |

> 🎙 **英文旁白（Scene 1–2 · 检索 + 核验）**
> "The user enters a colloquial chief complaint — in this case, crushing chest pain with cold sweats. Stage One performs hybrid recall over more than one thousand three hundred structured cases, then Stage Two uses a pointwise Jev verifier to rerank candidates by calibrated probability. Here is the core contribution: every proposition from the top case is verified through two questions — is it mentioned, and is it supported. Propositions are classified into three states — supported, gap, or absent. Critically, absent propositions are physically removed before they ever reach the generator, so they cannot leak into the final answer."

### 镜头 3 —— 多轮交互：补充红旗体征（0:50–1:15）
| 步骤 | 点击 | 目的 |
|---|---|---|
| 3.1 | 点击对话流下方的 **追问胶囊**（如「气短/血氧」类 chip） | 演示「未提及要素 → 交互式追问」 |
| 3.2 | 观察左侧 **演化时间线** 更新（确/疑/断随轮次演化） | 展示多轮重算 |
| 3.3 | （可选）点击红旗组 **「气短，血氧92%」** 胶囊填入输入框 → **提交** | 演示自由补充体征 |

> 🎙 **英文旁白（Scene 3 · 多轮交互）**
> "When information is missing, the system does not guess. Instead, it surfaces follow-up question chips to request the missing facts from the user. As the user provides additional statements, the verification states and the final conclusion are recomputed in real time."

### 镜头 4 —— 无红旗对照场景（1:15–1:35）
| 步骤 | 点击 | 目的 |
|---|---|---|
| 4.1 | **重置会话**（右上角「重置会话」） | 清空 |
| 4.2 | 主诉卡 → **「发烧呕吐拉肚子」** | 触发 gastro 场景（无红旗，门诊就诊） |
| 4.3 | 依次点 01→02→03→04 标签 | 展示「门诊就诊」绿色横幅 + absent 命题被硬闸门剔除 |

> 🎙 **英文旁白（Scene 4 · 红旗升级）**
> "If a red-flag sign — such as crushing chest pain — is confirmed, the system immediately escalates and instructs the patient to seek emergency care without delay. For a non-urgent case, the same pipeline produces a routine outpatient recommendation, demonstrating that the escalation is driven by verified facts rather than by the query wording alone."

### 镜头 5 —— 语言切换 + 收束（1:35–1:40）
| 步骤 | 点击 | 目的 |
|---|---|---|
| 5.1 | 点击 **「EN」** 按钮 | 展示中英双语即时切换（从缓存轮次重渲染，不重跑） |
| 5.2 | （口播）一句话收束 + 引用病例链接可点开 | 结尾 |

> 🎙 **英文旁白（Scene 5–6 · 五步输出 + 收束）**
> "Finally, the verified facts are passed to a large language model to produce a structured five-step output: the triage conclusion, the ruling-out of superficial causes, the fact-to-conclusion mapping, boundary conditions, and next-step recommendations. Every claim is traceable back to its cited source case. FirstCare demonstrates a retrieval-augmented triage pipeline where verification strictly precedes generation — a concrete step toward reducing hallucination in medical AI. Thank you."

---

## 三、备用镜头（时间富余时补拍，增强说服力）

| 场景按钮 | 展示点 |
|---|---|
| **「发热两天+糖尿病史13年」**（fever） | 无红旗常规发热，展示既往史如何参与核验 |
| **「转移性右下腹痛 + 低烧」**（abdomen） | 红旗触发（阑尾炎样），展示「立即急诊」 |
| **「高烧39℃+浑身酸痛」**（flu） | 红旗触发，展示「表面诱因误区提醒」（家中有感冒患儿） |

---

## 四、录制技术清单

| 项 | 建议 |
|---|---|
| 工具 | OBS Studio（1080p/30fps）或 Windows Xbox Game Bar（Win+G，零安装录浏览器窗口） |
| 画质 | 1920×1080，浏览器缩放 100% |
| 旁白 | **英文**，语速适中；或配英文字幕（脚本见第七节） |
| 隐私 | 录制前关闭通知、退出微信/邮件，避免弹窗干扰 |
| 导出 | MP4（H.264），上传至可公开访问处取 URL 填入投稿表 |

---

## 五、诚实性说明（录制/投稿时须知）

1. **真实 API 预跑结果**：`chest/fever/flu/gastro/abdomen` 五个预设场景的 Stage-2 精排分数、Task-5 的 p_men/p_sup、Task-6 的 LLM 五步文本，均来自 `prewarm_demo_cache.py` 用真实 Jev(DMXAPI) + 并行智算云 LLM 预跑的结果（非现场调用、非离线兜底）。UI 中已标注「真实 API 预计算」。
2. **非预设的任意输入**：仍走离线确定性模式（词面兜底 + 确定性规则），与后端无 API 时逐位一致。
3. **Stage-2 分数偏低属真实结果**：部分场景 Jev 精排分数较低（如 gastro 0.23、flu 0.22），这是真实判别模型的输出，如实呈现，不美化。
4. 论文中若引用本 demo，须明确「离线确定性模式」与「真实 API 预计算」两种形态，避免误导为「现场直连后端服务」。

---

## 六、产物清单

| 文件 | 说明 |
|---|---|
| `medir_triage_ui.html` | 模拟版 demo（20.5MB，已注入真实 API 预跑结果，双击即用） |
| `prewarm_demo_cache.py` | 预跑脚本（真实 API 全链路，可重跑） |
| `data/cache/demo_preset_cache.json` | 完整预跑结果（含全部字段） |
| `data/cache/demo_preset_slim.json` | 精简注入版（前端实际使用的缓存） |
| `build_frontend_mock.py` | 构建脚本（已支持注入 PRESET_API） |
| `verify_preset_injection.py` | 注入验证脚本（Node 冒烟测试） |

---

## 七、英文旁白脚本（完整照读版）

> 面向 ECIR 2027 国际评审。界面保持中文，旁白/字幕用英文。
> 语速平稳，每段之间留 1–2 秒停顿给镜头展示 UI。

**Scene 0 · Opening（0:00–0:12）**
> FirstCare is a low-hallucination medical triage assistant built on a large Chinese clinical case corpus, combining hybrid retrieval with a two-question fact-verification gate before any answer is generated.

**Scene 1–2 · Retrieval + Verification（0:12–1:15）**
> The user enters a colloquial chief complaint — in this case, crushing chest pain with cold sweats. Stage One performs hybrid recall over more than one thousand three hundred structured cases, then Stage Two uses a pointwise Jev verifier to rerank candidates by calibrated probability. Here is the core contribution: every proposition from the top case is verified through two questions — is it mentioned, and is it supported. Propositions are classified into three states — supported, gap, or absent. Critically, absent propositions are physically removed before they ever reach the generator, so they cannot leak into the final answer.

**Scene 3 · Multi-turn Interaction（1:15–1:45）**
> When information is missing, the system does not guess. Instead, it surfaces follow-up question chips to request the missing facts from the user. As the user provides additional statements, the verification states and the final conclusion are recomputed in real time.

**Scene 4 · Red-flag Escalation（1:45–2:00）**
> If a red-flag sign — such as crushing chest pain — is confirmed, the system immediately escalates and instructs the patient to seek emergency care without delay. For a non-urgent case, the same pipeline produces a routine outpatient recommendation, demonstrating that the escalation is driven by verified facts rather than by the query wording alone.

**Scene 5–6 · Five-step Output + Closing（2:00–2:30）**
> Finally, the verified facts are passed to a large language model to produce a structured five-step output: the triage conclusion, the ruling-out of superficial causes, the fact-to-conclusion mapping, boundary conditions, and next-step recommendations. Every claim is traceable back to its cited source case. FirstCare demonstrates a retrieval-augmented triage pipeline where verification strictly precedes generation — a concrete step toward reducing hallucination in medical AI. Thank you.
