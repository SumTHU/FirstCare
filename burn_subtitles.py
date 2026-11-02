# -*- coding: utf-8 -*-
"""逐句跟随式字幕：把每段旁白按句子拆分，逐句分配时间戳（按字数比例），
单行底部居中显示，烧录进视频。"""
import subprocess
import re
import os
from pathlib import Path
from mutagen.mp3 import MP3
import imageio_ffmpeg

ROOT = Path(__file__).resolve().parent
AUDIO = ROOT / "data" / "audio"
VIDEO = ROOT / "data" / "video"
FF = imageio_ffmpeg.get_ffmpeg_exe()

ORDER = ["opening", "retrieval", "verify", "multiturn", "redflag", "output", "closing"]
TEXTS = {
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

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1600
PlayResY: 900
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,24,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,0,2,40,40,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def split_sentences(text: str):
    """按句末标点切分句子，保留标点，归一化空格。"""
    text = " ".join(text.split())
    parts = re.split(r'(?<=[.!?])\s+', text)
    return [p.strip() for p in parts if p.strip()]


def ass_time(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def build_ass() -> Path:
    lines = [ASS_HEADER]
    t = 0.0
    for key in ORDER:
        dur = MP3(str(AUDIO / f"{key}.mp3")).info.length
        sents = split_sentences(TEXTS[key])
        total_chars = sum(len(s) for s in sents)
        seg_start = t
        for s in sents:
            seg_dur = dur * len(s) / total_chars
            seg_end = seg_start + seg_dur
            txt = s.replace("\\", "").replace("{", "(").replace("}", ")")
            lines.append(
                f"Dialogue: 0,{ass_time(seg_start)},{ass_time(seg_end)},Default,,0,0,0,,{txt}"
            )
            seg_start = seg_end
        t += dur
    ass = VIDEO / "demo_subtitles_sentence.ass"
    ass.write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] 逐句 ASS 生成 -> {ass}")
    for key in ORDER:
        sents = split_sentences(TEXTS[key])
        print(f"  {key}: {len(sents)} 句")
    return ass


def burn(ass: Path):
    tmp = Path("C:/tmp/sub/demo_subtitles_sentence.ass")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(ass.read_bytes())
    out = VIDEO / "demo_final_subtitled.mp4"
    cmd = [FF, "-y", "-i", str(VIDEO / "demo_final.mp4"),
           "-vf", "ass=demo_subtitles_sentence.ass",
           "-c:v", "libx264", "-preset", "medium", "-crf", "20",
           "-c:a", "copy", "-pix_fmt", "yuv420p",
           str(out)]
    print("烧录逐句字幕中...")
    cwd = os.getcwd()
    os.chdir(str(tmp.parent))
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    finally:
        os.chdir(cwd)
    if r.returncode != 0:
        print("[ERR]", r.stderr[-1500:])
        return
    print(f"[OK] 字幕版完成 -> {out} ({out.stat().st_size/1e6:.1f} MB)")


if __name__ == "__main__":
    ass = build_ass()
    burn(ass)
