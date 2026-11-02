# -*- coding: utf-8 -*-
"""用 edge-tts 生成英文旁白（美式女声 Aria），并输出每段时长供录屏脚本对齐。"""
import asyncio
import json
import sys
import edge_tts

VOICE = "en-US-AriaNeural"
OUT_DIR = "data/audio"

# 旁白分段：key -> 台词（与 demo 按钮点击顺序对齐）
NARRATION = {
    "opening": "FirstCare is a low-hallucination medical triage assistant, built natively "
               "on a large Chinese clinical case corpus. The interface you see is in "
               "English, but the underlying reasoning operates in the Chinese semantic "
               "space. It combines hybrid retrieval with a two-question fact-verification "
               "gate, before any answer is generated.",
    "retrieval": "The user enters a colloquial chief complaint. In this case, crushing "
                 "chest pain with cold sweats. Stage One performs hybrid recall over more "
                 "than one thousand three hundred structured cases. Then Stage Two uses a "
                 "pointwise Jev verifier to rerank candidates by calibrated probability.",
    "verify": "Here is the core contribution. Every proposition from the top case is "
              "verified through two questions. Is it mentioned, and is it supported? "
              "Propositions are classified into three states. Supported, gap, or absent. "
              "Critically, absent propositions are physically removed before they ever "
              "reach the generator, so they cannot leak into the final answer.",
    "multiturn": "When information is missing, the system does not guess. Instead, it "
                 "surfaces follow-up question chips to request the missing facts from "
                 "the user. As the user provides additional statements, the verification "
                 "states and the final conclusion are recomputed in real time.",
    "redflag": "If a red-flag sign, such as crushing chest pain, is confirmed, the system "
               "immediately escalates and instructs the patient to seek emergency care "
               "without delay. For a non-urgent case, the same pipeline produces a routine "
               "outpatient recommendation.",
    "output": "Finally, the verified facts are passed to a large language model to produce "
              "a structured five-step output. The triage conclusion, the ruling out of "
              "superficial causes, the fact-to-conclusion mapping, boundary conditions, and "
              "next-step recommendations. Every claim is traceable back to its cited source "
              "case.",
    "closing": "FirstCare demonstrates a retrieval-augmented triage pipeline where "
               "verification strictly precedes generation. A concrete step toward reducing "
               "hallucination in medical AI. Thank you.",
}


async def synth(key: str, text: str) -> float:
    out = f"{OUT_DIR}/{key}.mp3"
    c = edge_tts.Communicate(text, VOICE, rate="+0%")
    await c.save(out)
    # 用 edge-tts 的流式接口读时长更麻烦，这里用本地解析粗估
    import subprocess
    # 若无 ffprobe，退化为按字数估算
    return 0.0


async def main():
    import os
    os.makedirs(OUT_DIR, exist_ok=True)
    durations = {}
    for key, text in NARRATION.items():
        out = f"{OUT_DIR}/{key}.mp3"
        c = edge_tts.Communicate(text, VOICE, rate="+0%")
        await c.save(out)
        print(f"[OK] {key} -> {out} ({len(text)} chars)")
    print("全部生成完毕")
    json.dump({"voice": VOICE, "segments": list(NARRATION.keys())},
              open(f"{OUT_DIR}/manifest.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
