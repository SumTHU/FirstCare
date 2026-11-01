# -*- coding: utf-8 -*-
"""无头冒烟测试：从构建产物中提取管线 JS（CORPUS → runPipeline），
在 Node 中直接运行，验证 Stage-1/Stage-2/双问题核验/硬闸门/演绎模板全链路。"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HTML = ROOT / "medir_triage_ui_real.html"

CUT_MARK = "/* ============ 会话状态 ============ */"

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
const queries = [
  "大夫，我前天吃了冰葡萄后开始发高烧到40度，伴寒战、肚子胀还吐了两天；我以前确诊过成人斯蒂尔病，一直在吃泼尼松。",
  "我前天吃冰葡萄后高烧40度伴呕吐，有成人斯蒂尔病史；今天觉得明显气短、测血氧只有92%，而且腋窝摸到肿大淋巴结、身上起了散在红色皮疹，吃消炎药抗感染无效。",
  "我昨天吃冰葡萄后发烧38.3度、恶心呕吐拉肚子，但我既往体健无风湿病史，而且无气短、血氧正常98%、无淋巴结肿大、无皮疹。",
  "我昨天开始发烧到39.2度，浑身肌肉酸痛、头疼嗓子疼，还有点干咳。"
];
(async () => {
console.log("cases:", CORPUS.cases.length);
const t0 = Date.now();
buildIndex();
console.log("index built in", Date.now() - t0, "ms; N =", IDX.N, "grams =", IDX.idf.size);
console.log("cfg: jevKey=", !!CFG.jevKey, "ptKey=", !!CFG.ptKey, "(离线确定性档位)");
for (const q of queries){
  const t = Date.now();
  const r = await runPipeline(q);
  const c = k => r.facts.filter(f => f.role === k).length;
  console.log("---- q:", q.slice(0, 26) + "…", "|", r.ms + "ms", "| s2mode:", r.s2mode);
  console.log("  top3:", r.ranked.slice(0,3).map(x => x.cmaid + "=" + x.score.toFixed(2)).join(" "));
  console.log("  三态 sup/gap/abs:", c("supported") + "/" + c("gap") + "/" + c("absent"),
              "| red-flag supported:", r.facts.filter(f => f.prop.rf && f.role === "supported").length);
  console.log("  urgency:", r.answer.urgency, "| route:", r.answer.route);
  console.log("  chips:", r.gated.chips.length, "| cited:", r.answer.cited.join(","));
  console.log("  (i):", r.answer.sections.i.slice(0, 60));
  console.log("  en_summary:", r.answer.en_summary.slice(0, 80));
}
console.log("SMOKE OK");
})().catch(e => { console.error("SMOKE FAIL:", e); process.exit(1); });
"""

STUB_JS = r"""
/* 最小 DOM 桩（仅管线部分需要） */
global.document = {
  querySelector: () => ({addEventListener(){}, classList:{add(){},remove(){},toggle(){}},
                         setAttribute(){}, style:{}, focus(){}}),
  querySelectorAll: () => [], addEventListener(){}, documentElement:{style:{}}
};
"""
Path(ROOT / "_smoke.js").write_text(
    STUB_JS + pipeline_js + "\n" + TEST_JS, encoding="utf-8")
r = subprocess.run(["node", str(ROOT / "_smoke.js")], capture_output=True, text=True,
                   cwd=str(ROOT), timeout=300)
print(r.stdout)
if r.returncode != 0:
    print("STDERR:", r.stderr[-3000:])
    sys.exit(1)
