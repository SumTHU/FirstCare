/* 神经链路 mock 测试体：需与 stub + pipeline JS 拼接后运行（见 neural_test.py） */
const http = require("http");
const jevSrv = http.createServer((req, res) => {
  let b = ""; req.on("data", d => b += d);
  req.on("end", () => {
    const p = JSON.parse(b);
    if (!p.state || !p.model || !p.questions){ res.statusCode = 400; res.end("{}"); return; }
    res.setHeader("content-type", "application/json");
    const answers = {};
    for (const k of Object.keys(p.questions))
      answers[k] = {noul: k === "p_sup" ? 0.82 : 0.91};
    res.end(JSON.stringify({answers}));
  });
});
const ptSrv = http.createServer((req, res) => {
  let b = ""; req.on("data", d => b += d);
  req.on("end", () => {
    const p = JSON.parse(b);
    res.setHeader("content-type", "application/json");
    if (p.messages && p.messages[0] && p.messages[0].role === "system" &&
        p.messages[0].content.includes("相关性判别专家")){
      res.end(JSON.stringify({choices: [{message: {content: '{"score": 0.77, "reason_zh": "测试判分理由"}'}}]}));
    } else {
      res.end(JSON.stringify({choices: [{message: {content:
        "(i) 分诊结论：测试。(ii) 误区：测试。(iii) 映射：测试。(iv) 边界：测试。(v) 建议：测试。"}}]}));
    }
  });
});

(async () => {
  await new Promise(r => jevSrv.listen(0, r));
  await new Promise(r => ptSrv.listen(0, r));
  const jp = jevSrv.address().port, pp = ptSrv.address().port;

  /* —— 场景 A：Jev Key → Stage-2 走 jev，Task-5 走神经 —— */
  CFG.jevKey = "sk-test"; CFG.jevUrl = "http://127.0.0.1:" + jp + "/v1/systemone";
  CFG.ptKey = ""; CFG.useLLMGen = false;
  pipeCache.clear();
  const q = "大夫，我前天吃了冰葡萄后开始发高烧到40度，伴寒战、肚子胀还吐了两天；我以前确诊过成人斯蒂尔病，一直在吃泼尼松。";
  const r = await runPipeline(q);
  console.log("A s2mode:", r.s2mode);                     /* 期望 jev */
  console.log("A top1:", r.ranked[0].cmaid, r.ranked[0].score, "reason:", r.ranked[0].reason_zh);
  const f0 = r.facts.find(x => x.role !== "absent") || r.facts[0];
  console.log("A fact0:", f0.role, "p_men=" + f0.p_men.toFixed(2), "p_sup=" + f0.p_sup.toFixed(2));
  console.log("A answer mode:", r.answer.mode, "(期望 template，未开 LLM 生成)");

  /* —— 场景 B：无 Jev、仅智算云 → Stage-2 走 llm；Task-6 开 LLM —— */
  CFG.jevKey = ""; CFG.ptKey = "sk-test";
  CFG.ptUrl = "http://127.0.0.1:" + pp + "/v1"; CFG.ptModel = "test-model"; CFG.useLLMGen = true;
  pipeCache.clear();
  const r2 = await runPipeline(q + " b");
  console.log("B s2mode:", r2.s2mode, "| reason:", r2.ranked[0].reason_zh);   /* 期望 llm / 测试判分理由 */
  console.log("B answer mode:", r2.answer.mode);                              /* 期望 llm */
  console.log("B (i):", r2.answer.sections.i.slice(0, 40));

  /* —— 场景 C：Key 在但端点不可达 → 全部逐级回落 + 留痕 —— */
  CFG.jevUrl = "http://127.0.0.1:1/v1/systemone";
  CFG.ptUrl = "http://127.0.0.1:1/v1";
  pipeCache.clear();
  const r3 = await runPipeline(q + " c");
  console.log("C s2mode:", r3.s2mode, "| note:", r3.s2note);                  /* 期望 lexical + 回落说明 */
  console.log("C answer mode:", r3.answer.mode, "| fallback:", r3.answer.fallback_reason || "(无)");
  console.log("C urgency:", r3.answer.urgency, "(期望与场景 A 一致的模板输出)");

  jevSrv.close(); ptSrv.close();
  console.log("NEURAL SMOKE OK");
})().catch(e => { console.error("NEURAL FAIL:", e); process.exit(1); });
