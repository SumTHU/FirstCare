# -*- coding: utf-8 -*-
"""Fig.2 自动截图 v5（方案A）：真实成人病例驱动的口语查询。
查询要素逐项对应 CMCR case_1671920（84岁女性急性心梗，主诉胸痛7小时24分+冷汗+气短），
保留口语比喻（像被大石头压着）以维持 Fig.1 示例连续性，含规范词面"胸痛"保证离线词面精排区分度。"""
from playwright.sync_api import sync_playwright
import pathlib, json

ROOT = pathlib.Path(r'C:/Users/苏萌/Desktop/FeverFavor')
URL = (ROOT / 'medir_triage_ui_real.html').as_uri()
OUT = ROOT / 'out'
QUERY = '我胸痛了七个多小时，像被大石头压着，喘不上气，还出冷汗。'

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': 1600, 'height': 1000}, device_scale_factor=2)
    page.goto(URL)
    page.wait_for_load_state('load')
    page.evaluate("document.getElementById('cfgModal').style.display='none'")
    page.evaluate("document.getElementById('langEn').click()")
    page.wait_for_timeout(200)

    page.evaluate(f"document.getElementById('queryInput').value = {json.dumps(QUERY, ensure_ascii=False)}")
    page.evaluate("document.querySelector('.btn-send').click()")
    page.wait_for_selector('#rfmClose', state='visible', timeout=90000)
    page.evaluate("document.getElementById('rfmClose').click()")
    print('红旗模态已触发并关闭', flush=True)
    page.wait_for_timeout(400)

    st = page.evaluate("""() => {
      const r = roundCache[roundCache.length-1]; const g = r.gated; const a = r.answer;
      return {sup:(g.supported||[]).length, gap:(g.gap||[]).length, abs:(g.absent||[]).length,
              urgency: a.urgency, red: a.red_flag, chips: (a.chips||[]).length,
              supId: (g.supported[0]&&g.supported[0].prop&&g.supported[0].prop.id)||''};
    }""")
    print('三态:', st, flush=True)

    page.evaluate("document.querySelector('button[data-tab=\"panelVerify\"]').click()")
    page.wait_for_timeout(400)
    page.screenshot(path=str(OUT / 'fig2_demo_verify.png'))
    page.evaluate("document.querySelector('button[data-tab=\"panelAnswer\"]').click()")
    page.wait_for_timeout(400)
    page.screenshot(path=str(OUT / 'fig2_demo_answer.png'))
    print('badgeMode:', page.text_content('#badgeMode'), flush=True)
    browser.close()
print('DONE')
