# -*- coding: utf-8 -*-
"""生成 4 个音色的开场旁白对比音频，供试听选音色。"""
import asyncio
import edge_tts
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "audio" / "voice_samples"
OUT.mkdir(parents=True, exist_ok=True)

# 开场旁白（含底层中文说明，最能体现音色气质）
TEXT = ("FirstCare is a low-hallucination medical triage assistant, built natively "
        "on a large Chinese clinical case corpus. The interface you see is in "
        "English, but the underlying reasoning operates in the Chinese semantic "
        "space. It combines hybrid retrieval with a two-question fact-verification "
        "gate, before any answer is generated.")

VOICES = {
    "AriaNeural": "en-US-AriaNeural",
    "GuyNeural": "en-US-GuyNeural",
    "RyanNeural": "en-GB-RyanNeural",
    "SoniaNeural": "en-GB-SoniaNeural",
}


async def main():
    for name, voice in VOICES.items():
        out = OUT / f"{name}.mp3"
        c = edge_tts.Communicate(TEXT, voice, rate="+0%")
        await c.save(str(out))
        print(f"[OK] {name} ({voice}) -> {out}")


if __name__ == "__main__":
    asyncio.run(main())
