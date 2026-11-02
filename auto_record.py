# -*- coding: utf-8 -*-
"""Playwright 全自动录制 demo 视频（含中文→英文切换镜头）。

流程：切中文 → chest 场景（中文界面展示检索/核验）→ 点 EN 切英文
→ 追问/红旗/gastro 对照（英文界面）→ 收束。
输出 data/video/demo_raw.webm。
"""
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
DEMO = (ROOT / "medir_triage_ui.html").as_uri()
OUT = ROOT / "data" / "video" / "demo_raw.webm"
OUT.parent.mkdir(parents=True, exist_ok=True)

VIEW = {"width": 1600, "height": 900}


def close_rf(page):
    try:
        if page.locator("#rfModal").is_visible():
            page.wait_for_timeout(2200)
            page.click("#rfmClose")
            page.wait_for_timeout(600)
            return True
    except Exception:
        pass
    return False


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            viewport=VIEW,
            record_video_dir=str(OUT.parent),
            record_video_size=VIEW,
        )
        page = ctx.new_page()
        page.goto(DEMO)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2500)

        # ---- Scene 0: 点 EN（英文界面）+ 开场 (opening) ----
        page.click("#langEn")
        page.wait_for_timeout(12000)

        # ---- Scene 1: chest 场景，英文界面检索 (retrieval) ----
        page.click('[data-scenario="chest"]')
        page.wait_for_timeout(1500)
        close_rf(page)
        page.wait_for_timeout(19500)

        # ---- Scene 2: 命题拆解 + 核验，英文界面 (verify) ----
        page.click('[data-tab="panelProps"]')
        page.wait_for_timeout(2500)
        page.click('[data-tab="panelVerify"]')
        page.wait_for_timeout(23500)

        # ---- Scene 3: 追问胶囊，英文界面 (multiturn) ----
        try:
            chip = page.locator(".ask-chip, [data-ask], .followup-chip").first
            if chip.is_visible():
                chip.click()
                page.wait_for_timeout(1600)
        except Exception:
            pass
        page.wait_for_timeout(16500)

        # ---- Scene 4: 04 面板 + 红旗，英文界面 (redflag) ----
        page.click('[data-tab="panelAnswer"]')
        page.wait_for_timeout(16000)

        # ---- Scene 5: 重置 + gastro 对照，英文界面 (output) ----
        page.click("#btnReset")
        page.wait_for_timeout(1000)
        page.click('[data-scenario="gastro"]')
        page.wait_for_timeout(1500)
        page.click('[data-tab="panelAnswer"]')
        page.wait_for_timeout(19500)

        # ---- Scene 6: 收束 (closing) ----
        page.wait_for_timeout(13500)

        ctx.close()
        browser.close()

    vids = sorted(OUT.parent.glob("*.webm"))
    if vids:
        latest = vids[-1]
        if latest != OUT:
            latest.rename(OUT)
        print(f"[OK] 录屏完成 -> {OUT}")
    else:
        print("[WARN] 未找到录制产物")


if __name__ == "__main__":
    main()
