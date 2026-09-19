#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""获取阶段：优先拿平台现成字幕（B站 CC/AI 字幕等），拿不到就下载音频；可选 --video 一并保留源视频。

用法:
  python extract.py <视频链接> [--browser edge|chrome|firefox] [--out-root output] [--video]

行为（默认，不加 --video）:
  1) yt-dlp 解析元信息（标题等）；B站可加 --browser 复用登录态取 AI 字幕。
  2) 有字幕轨道 -> 下载并转成 <标题>_时间戳字幕.srt + <标题>_逐句原始.txt，结束（不下视频/音频）。
  3) 无字幕     -> 只下载音频到 <out-root>/<标题>/audio.*，提示接着跑 cloud_transcribe.py。
  4) 抖音直连失败（平台要求新鲜 cookies）-> 提示按 docs/workflow.md 的浏览器嗅探步骤拿流。

加 --video 时:
  - 有字幕：字幕流程不变，额外下载源视频到 <out-root>/<标题>/video.*
  - 无字幕：下载源视频 video.*，并用 ffmpeg 抽出 audio.wav 供后续 ASR（不重复下音频流）
  - 需要打进定稿目录时，把 video 路径传给 finalize.py --video

平台范围: 凡 yt-dlp 能解析并下载字幕/音视频的站点（YouTube、西瓜、Vimeo 等）流程通用；
  抖音因风控需浏览器嗅探；B站 AI 字幕建议加 --browser。

依赖: pip install yt-dlp；ffmpeg 在 PATH（或设 FFMPEG_PATH），用于字幕转 srt、音频抽取。
"""
import argparse
import json
import os
import re
import shutil
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


def ffmpeg_exe():
    return os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg")


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


def pick_prefix(outdir, prefix):
    for f in sorted(os.listdir(outdir)):
        if f.startswith(prefix + "."):
            return os.path.join(outdir, f)
    return None


def download_video(outdir, used, url, timeout=1200):
    """下载较好画质的音视频合并流，文件名 video.<ext>。"""
    r = ytdlp(
        [
            "-f", "bv*+ba/b",
            "--no-playlist",
            "--merge-output-format", "mp4",
            "-o", os.path.join(outdir, "video.%(ext)s"),
            *used, url,
        ],
        timeout=timeout,
    )
    if r.returncode != 0:
        # 部分站点无分离流，退回单一最佳格式
        r = ytdlp(
            [
                "-f", "b",
                "--no-playlist",
                "-o", os.path.join(outdir, "video.%(ext)s"),
                *used, url,
            ],
            timeout=timeout,
        )
    video = pick_prefix(outdir, "video")
    return video, r


def extract_audio_from_video(video, outdir):
    """从已下载视频抽出 16k 单声道 wav，供后续 ASR。"""
    ff = ffmpeg_exe()
    if not ff:
        sys.exit("需要 ffmpeg 从视频抽音频：请安装并加入 PATH，或设置 FFMPEG_PATH。")
    audio = os.path.join(outdir, "audio.wav")
    subprocess.run(
        [ff, "-y", "-v", "error", "-i", video,
         "-vn", "-ac", "1", "-ar", "16000", audio],
        check=True,
    )
    return audio


def print_next_steps(title, audio, video=None):
    print("下一步:")
    print(f'  python scripts/cloud_transcribe.py "{audio}" work/out')
    print("  python scripts/cloud_result_to_srt.py work/out.json work/out")
    video_arg = f' --video "{video}"' if video else ""
    print(f'  python scripts/finalize.py work/out_trans.json "{title}"{video_arg}')
    print("  # 或本地兜底: python scripts/asr_local.py \"%s\" small \"%s\"" % (
        audio, os.path.join("work", "out")))


def main():
    ap = argparse.ArgumentParser(description="从视频链接提取字幕或音频（可选源视频）")
    ap.add_argument("url")
    ap.add_argument("--browser", default=None,
                    help="复用浏览器登录态（bilibili AI 字幕需要；浏览器需先关闭）")
    ap.add_argument("--out-root", default="output")
    ap.add_argument("--video", action="store_true",
                    help="同时下载源视频 video.*（默认只拿字幕或音频，不下载视频）")
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
    print("模式:", "字幕/音频 + 源视频(--video)" if a.video else "仅字幕/音频（默认不下载视频）")

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
        print("[warn] 字幕下载失败，将改走音频/视频路径:", last_err(r))

    sub_files = [
        f for f in os.listdir(outdir)
        if f.startswith("sub") and f.endswith((".srt", ".vtt"))
    ]
    if sub_files:
        best = pick_subtitle(sub_files)
        src = os.path.join(outdir, best)
        text = open(src, encoding="utf-8", errors="replace").read()
        if best.endswith(".vtt"):
            text = re.sub(r"^WEBVTT[^\n]*\n(\n)?", "", text)
            text = re.sub(r"(?m)(\d{2}:\d{2}:\d{2})\.(\d{3})", r"\1,\2", text)
            text = re.sub(r"(?m)^(NOTE|STYLE|REGION).*\n?", "", text)
        os.replace(src, os.path.join(outdir, f"{title}_时间戳字幕.srt"))
        open(os.path.join(outdir, f"{title}_逐句原始.txt"), "w", encoding="utf-8").write(srt_to_text(text))
        for f in sub_files:
            p = os.path.join(outdir, f)
            if os.path.exists(p):
                os.remove(p)
        print(f"已保存平台字幕 -> {outdir}（无需 ASR）")

        if a.video:
            print("按 --video 下载源视频 ...")
            video, vr = download_video(outdir, used, a.url)
            if not video:
                print("[fail] 源视频下载:", last_err(vr))
                sys.exit(1)
            print("源视频已下载:", video)
            print(f'可选: python scripts/finalize.py <trans.json> "{title}" --video "{video}"')
        return

    # 2) 无字幕：默认只下音频；--video 则下视频并抽出音频
    if a.video:
        print("按 --video 下载源视频，并抽出音频用于转写 ...")
        video, vr = download_video(outdir, used, a.url)
        if not video:
            print("[fail] 源视频下载:", last_err(vr))
            sys.exit(1)
        print("源视频已下载:", video)
        audio = extract_audio_from_video(video, outdir)
        print("已从视频抽出音频:", audio)
        print_next_steps(title, audio, video)
        return

    r = ytdlp(
        ["-f", "ba/b", "--no-playlist",
         "-o", os.path.join(outdir, "audio.%(ext)s"), *used, a.url],
        timeout=600,
    )
    if r.returncode != 0:
        print("[fail] 音频下载:", last_err(r))
        sys.exit(1)
    audio = pick_prefix(outdir, "audio")
    if not audio:
        sys.exit("未在输出目录找到音频文件，请检查 yt-dlp 输出。")
    print("音频已下载:", audio, "（默认未下载视频；如需片源请加 --video）")
    print_next_steps(title, audio, None)


if __name__ == "__main__":
    main()
