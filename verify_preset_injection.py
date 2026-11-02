# -*- coding: utf-8 -*-
"""验证 mock 版预设场景命中真实 API 预跑缓存后的覆盖逻辑。"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HTML = ROOT / "medir_triage_ui.html"
CUT_MARK = "/* ============ 会话状态（多轮语义与 app.py 一致：累积陈述重跑全管线） ============ */"

html = HTML.read_text(encoding="utf-8")
m = re.search(r"<script>(.*)</script>", html, re.S)
if not m:
    sys.exit("未找到 <script> 块")
js = m.group(1)
idx = js.find(CUT_MARK)
if idx < 0:
    sys.exit("未找到会话状态切分标记")
pipeline_js = js[:idx]

TEST_JS = r"""
const chest = "胸口像被大石头压着，喘不上气，出了很多冷汗";
const gastro = "我昨天在外面吃了烧烤后开始发烧38.3度、恶心呕吐拉肚子，但我既往体健，无气短、血氧正常98%、无淋巴结肿大、无皮疹";
(async () => {
  console.log("PRESET_API 场景数:", PRESET_API ? Object.keys(PRESET_API).length : 0);
  buildIndex();
  console.log("index N =", IDX.N);

  for (const q of [chest, gastro]){
    const r = runPipeline(q);
    console.log("==== q:", q.slice(0,18) + "…");
    console.log("  api:", r.api);
    console.log("  ranked top1:", r.ranked[0].cmaid, "score", r.ranked[0].score.toFixed(3), "| reason:", r.ranked[0].reason_zh);
    console.log("  三态 sup/gap/abs:",
      r.facts.filter(f=>f.role==="supported").length + "/" +
      r.facts.filter(f=>f.role==="gap").length + "/" +
      r.facts.filter(f=>f.role==="absent").length);
    console.log("  fact[0] p_men/p_sup:", r.facts[0].p_men.toFixed(2), r.facts[0].p_sup.toFixed(2), r.facts[0].role);
    console.log("  answer mode/urgency/redflag:", r.answer.mode, r.answer.urgency, r.answer.red_flag);
    console.log("  answer route:", r.answer.route);
    console.log("  cited:", r.answer.cited.join(","));
    console.log("  (i):", r.answer.sections.i.slice(0,50));
    console.log("  en_summary:", (r.answer.en_summary||"").slice(0,60));
  }
  console.log("PRESET SMOKE OK");
})().catch(e => { console.error("FAIL:", e); process.exit(1); });
"""

STUB_JS = r"""
global.document = {
  querySelector: () => ({addEventListener(){}, classList:{add(){},remove(){},toggle(){}},
                         setAttribute(){}, style:{}, focus(){}}),
  querySelectorAll: () => [], addEventListener(){}, documentElement:{style:{}}
};
"""
Path(ROOT / "_preset_smoke.js").write_text(
    STUB_JS + pipeline_js + "\n" + TEST_JS, encoding="utf-8")
r = subprocess.run(["node", str(ROOT / "_preset_smoke.js")],
                   capture_output=True, text=True, cwd=str(ROOT), timeout=300)
print(r.stdout)
if r.returncode != 0:
    print("STDERR:", r.stderr[-3000:])
    sys.exit(1)
