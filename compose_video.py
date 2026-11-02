# -*- coding: utf-8 -*-
"""把 7 段英文旁白按时间轴铺到录屏上，合成最终 MP4。"""
import subprocess
import imageio_ffmpeg
from mutagen.mp3 import MP3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AUDIO = ROOT / "data" / "audio"
VIDEO = ROOT / "data" / "video"
FF = imageio_ffmpeg.get_ffmpeg_exe()

# 旁白播放顺序与起始时间（秒），按 demo 场景节奏排布
ORDER = ["opening", "retrieval", "verify", "multiturn", "redflag", "output", "closing"]

def main():
    # 1. 计算累计时间轴
    starts = {}
    t = 0.0
    for key in ORDER:
        starts[key] = t
        t += MP3(str(AUDIO / f"{key}.mp3")).info.length
    total = t

    # 2. 逐段用 adelay 延迟，再 amix 叠加
    inputs = []
    filters = []
    for i, key in enumerate(ORDER):
        ms = int(starts[key] * 1000)
        inputs += ["-i", str(AUDIO / f"{key}.mp3")]
        filters.append(f"[{i+1}:a]adelay={ms}|{ms}[a{i}]")
    # amix 合并 7 路
    mix_in = "".join(f"[a{i}]" for i in range(len(ORDER)))
    filters.append(f"{mix_in}amix=inputs={len(ORDER)}:normalize=0[outa]")

    cmd = [
        FF, "-y",
        "-i", str(VIDEO / "demo_raw.webm"),
        *inputs,
        "-filter_complex", ";".join(filters),
        "-map", "0:v", "-map", "[outa]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-t", f"{total:.2f}",
        str(VIDEO / "demo_final.mp4"),
    ]
    print("合成中...")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("[ERR]", r.stderr[-1500:])
        return
    out = VIDEO / "demo_final.mp4"
    print(f"[OK] 合成完成 -> {out} ({out.stat().st_size/1e6:.1f} MB, {total:.1f}s)")


if __name__ == "__main__":
    main()
