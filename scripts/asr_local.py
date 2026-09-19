#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地兜底 ASR（faster-whisper，无需密钥/联网）。

用法: python asr_local.py <音频文件> [模型名或本地模型目录] [输出前缀]
  模型名: tiny/base/small/medium/large-v3（首次自动下载，国内建议:
          set HF_ENDPOINT=https://hf-mirror.com 并用 curl 手动下载权重，见 docs/workflow.md）

输出:
  <前缀>.srt
  <前缀>_raw.txt
  <前缀>_trans.json   与云端 filetrans 同 schema，可直接交给 finalize.py

提示: 无 GPU 时 medium 以上对 10 分钟级音频非常慢，建议优先云端（cloud_transcribe.py）。
"""
import argparse
import json
import os
import sys
import time


def fmt(s):
    h, mn = int(s // 3600), int(s % 3600 // 60)
    return f"{h:02d}:{mn:02d}:{int(s % 60):02d},{int(round((s - int(s)) * 1000)):03d}"


def main():
    ap = argparse.ArgumentParser(description="本地 faster-whisper ASR 兜底")
    ap.add_argument("audio", help="音频文件路径")
    ap.add_argument("model", nargs="?", default="small",
                    help="模型名 tiny/base/small/medium/large-v3，或本地模型目录（默认 small）")
    ap.add_argument("prefix", nargs="?", default=None,
                    help="输出前缀（默认去掉音频扩展名）")
    a = ap.parse_args()

    if not os.path.isfile(a.audio):
        sys.exit(f"音频不存在: {a.audio}")
    prefix = a.prefix or os.path.splitext(a.audio)[0]
    os.environ.setdefault("OMP_NUM_THREADS", str(os.cpu_count() or 8))

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("未安装 faster-whisper：pip install faster-whisper（或改用云端 cloud_transcribe.py）")

    t0 = time.time()
    m = WhisperModel(a.model, device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 8)
    print(f"model loaded in {time.time()-t0:.0f}s", flush=True)
    segments, info = m.transcribe(
        a.audio, beam_size=5, vad_filter=True,
        initial_prompt="以下是普通话口播内容，请加上标点。",
    )
    print(f"lang={info.language} p={info.language_probability:.2f} dur={info.duration:.1f}s", flush=True)

    srt, texts, sents = [], [], []
    for i, seg in enumerate(segments, 1):
        text = seg.text.strip()
        srt.append(f"{i}\n{fmt(seg.start)} --> {fmt(seg.end)}\n{text}\n")
        texts.append(text)
        sents.append({
            "begin_time": int(round(seg.start * 1000)),
            "end_time": int(round(seg.end * 1000)),
            "text": text,
        })

    if not sents:
        sys.exit("未识别到语音内容，请检查音频。")

    open(prefix + ".srt", "w", encoding="utf-8").write("\n".join(srt))
    open(prefix + "_raw.txt", "w", encoding="utf-8").write("\n".join(texts))
    trans = {
        "transcripts": [{"sentences": sents}],
        "properties": {
            "original_duration_in_milliseconds": int(round((info.duration or 0) * 1000)),
        },
    }
    json.dump(trans, open(prefix + "_trans.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"done in {time.time()-t0:.0f}s -> {prefix}.srt / {prefix}_raw.txt / {prefix}_trans.json")
    print(f"下一步: python scripts/finalize.py \"{prefix}_trans.json\" \"<视频标题>\" --model faster-whisper/{a.model}")


if __name__ == "__main__":
    main()
