#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""获取阶段：优先拿平台现成字幕（B站 CC/AI 字幕等），拿不到就下载音频，交给后续转写。

用法:
  python extract.py <视频链接> [--browser edge|chrome|firefox] [--out-root output]

行为:
  1) yt-dlp 解析元信息（标题等）；B站可加 --browser 复用登录态取 AI 字幕。
  2) 有字幕轨道 -> 下载并转成 <标题>_时间戳字幕.srt + <标题>_逐句原始.txt，结束。
  3) 无字幕     -> 下载音频到 <out-root>/<标题>/audio.*，提示接着跑 cloud_transcribe.py。
  4) 抖音直连失败（平台要求新鲜 cookies）-> 提示按 docs/workflow.md 的浏览器嗅探步骤拿流。

平台范围: 凡 yt-dlp 能解析并下载字幕/音频的站点（YouTube、西瓜、Vimeo 等）流程通用；
  抖音因风控需浏览器嗅探；B站 AI 字幕建议加 --browser。

依赖: pip install yt-dlp；ffmpeg 在 PATH（或设 FFMPEG_PATH），用于字幕转 srt 与音频抽取。
"""
import argparse
import json
import os
import re
import subprocess
import sys


def sanitize(name):
    return re.sub(r'[\\/:*?"<>|\n\r\t]', "_", name).strip(" ._")[:80] or "untitled"


def ytdlp(args, timeout=600):
    return subprocess.run(
        [sys.executable, "-m", "yt_dlp", "--no-warnings", *args],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout,
    )


def last_err(r):
    err = (r.stderr or "").strip()
    return err.splitlines()[-1][:200] if err else "?"


def srt_to_text(srt):
    out = []
    for block in re.split(r"\n\s*\n", srt.strip()):
        lines = [l for l in block.splitlines()
                 if l.strip() and "-->" not in l and not l.strip().isdigit()]
        if lines:
            out.append(" ".join(lines))
    return "\n".join(out)


def pick_subtitle(filenames):
    """优先中文 srt，其次任意 srt，再次中文 vtt。"""
    def score(name):
        n = name.lower()
        s = 0
        if n.endswith(".srt"):
            s += 20
        if "zh" in n or "ai-zh" in n or "hans" in n or "hant" in n:
            s += 10
        return s
    return sorted(filenames, key=lambda x: (-score(x), x))[0]


def main():
    ap = argparse.ArgumentParser(description="从视频链接提取字幕或音频")
    ap.add_argument("url")
    ap.add_argument("--browser", default=None,
                    help="复用浏览器登录态（bilibili AI 字幕需要；浏览器需先关闭）")
    ap.add_argument("--out-root", default="output")
    a = ap.parse_args()

    cookie_opts = [f"--cookies-from-browser={a.browser}"] if a.browser else []
    info = None
    used = []
    attempts = [[], cookie_opts] if a.browser else [[]]
    for opts in attempts:
        r = ytdlp(["-J", *opts, a.url], timeout=120)
        if r.returncode == 0:
            try:
                info = json.loads(r.stdout)
            except json.JSONDecodeError:
                print("[fail] yt-dlp 输出不是合法 JSON")
                continue
            used = opts
            break
        print("[fail] yt-dlp 解析:", last_err(r))
    if info is None:
        if "douyin" in a.url.lower():
            sys.exit(
                "抖音直连被平台风控拦截。请按 docs/workflow.md：真实浏览器打开页面 -> "
                "嗅探 media-video/media-audio 双流 -> curl 下载 -> ffmpeg 合成。"
            )
        sys.exit("yt-dlp 解析失败，请检查链接，或加 --browser 复用登录态（B站 AI 字幕）。")

    title = sanitize(info.get("title", "untitled"))
    outdir = os.path.join(a.out_root, title)
    os.makedirs(outdir, exist_ok=True)
    print("标题:", title)
    print("平台: ", info.get("extractor_key") or info.get("extractor") or "unknown")

    # 1) 现成字幕优先（convert-subs 保证落盘为真正的 SRT）
    r = ytdlp(
        [
            "--skip-download",
            "--write-subs", "--write-auto-subs",
            "--sub-langs", "zh.*,ai-zh,zh-Hans,zh-Hant,zh-CN,en.*,en",
            "--convert-subs", "srt",
            "-o", os.path.join(outdir, "sub.%(ext)s"),
            *used, a.url,
        ],
        timeout=180,
    )
    if r.returncode != 0:
        print("[warn] 字幕下载失败，转为下载音频:", last_err(r))

    sub_files = [
        f for f in os.listdir(outdir)
        if f.startswith("sub") and f.endswith((".srt", ".vtt"))
    ]
    if sub_files:
        best = pick_subtitle(sub_files)
        src = os.path.join(outdir, best)
        text = open(src, encoding="utf-8", errors="replace").read()
        if best.endswith(".vtt"):
            # 兜底：yt-dlp --convert-subs 失败时的粗略转换
            text = re.sub(r"^WEBVTT[^\n]*\n(\n)?", "", text)
            # VTT 时间轴 00:00:00.000 -> SRT 00:00:00,000
            text = re.sub(r"(?m)(\d{2}:\d{2}:\d{2})\.(\d{3})", r"\1,\2", text)
            text = re.sub(r"(?m)^(NOTE|STYLE|REGION).*\n?", "", text)
        os.replace(src, os.path.join(outdir, f"{title}_时间戳字幕.srt"))
        open(os.path.join(outdir, f"{title}_逐句原始.txt"), "w", encoding="utf-8").write(srt_to_text(text))
        # 清理未选用的字幕副本
        for f in sub_files:
            p = os.path.join(outdir, f)
            if os.path.exists(p):
                os.remove(p)
        print(f"已保存平台字幕 -> {outdir}（无需 ASR）")
        return

    # 2) 下载音频
    r = ytdlp(
        ["-f", "ba/b", "--no-playlist",
         "-o", os.path.join(outdir, "audio.%(ext)s"), *used, a.url],
        timeout=600,
    )
    if r.returncode != 0:
        print("[fail] 音频下载:", last_err(r))
        sys.exit(1)
    audio = next(
        (os.path.join(outdir, f) for f in os.listdir(outdir) if f.startswith("audio.")),
        None,
    )
    if not audio:
        sys.exit("未在输出目录找到音频文件，请检查 yt-dlp 输出。")
    print("音频已下载:", audio)
    print("下一步:")
    print(f'  python scripts/cloud_transcribe.py "{audio}" work/out')
    print(f'  python scripts/cloud_result_to_srt.py work/out.json work/out')
    print(f'  python scripts/finalize.py work/out_trans.json "{title}"')
    print("  # 或本地兜底: python scripts/asr_local.py \"%s\" small \"%s\"" % (
        audio, os.path.join(outdir, title)))


if __name__ == "__main__":
    main()
