#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""转写结果定稿：同音字校正 + 去语气词/口吃 + 自然段整理 -> output/<标题>/。

兼容输入:
  - 云端: cloud_result_to_srt.py 产出的 <前缀>_trans.json
  - 本地: asr_local.py 产出的 <前缀>_trans.json（同 schema）

用法:
  python finalize.py <trans.json> <视频标题> [--link URL] [--platform douyin|bilibili|other]
                     [--author 作者] [--model 模型说明] [--fixes fixes.json] [--video 源视频.mp4]
                     [--out-root output]

fixes.json 格式（可选，逐条字符串替换，用于修正 ASR 同音字）:
  [["工业之忧", "功业之忧"], ["巨云雀", "致云雀"]]

脚本产出（文件名 = <视频标题>_xxx）:
  时间戳字幕.srt / 逐句原始.txt / 完整文案.txt / 来源说明.txt / 源视频.mp4(若提供)

完整交付另需人工/AI 补一份 <标题>_内容总结.txt（脚本不生成）。
"""
import argparse
import json
import os
import re
import shutil
import sys
import time

FILLER = re.compile(r"(?<![^\s，。！？、])(啊|呀|呃|嗯|哦|哎)(?=[，。、！？])")


def fmt(ms):
    s = ms / 1000
    h, m = int(s // 3600), int(s % 3600 // 60)
    return f"{h:02d}:{m:02d}:{int(s % 60):02d},{int(round((s - int(s)) * 1000)):03d}"


def fix(t, pairs):
    for a, b in pairs:
        t = t.replace(a, b)
    return t


def strip_filler(t):
    t = FILLER.sub("", t)
    t = re.sub(r"。，", "。", t)
    t = re.sub(r"[，]{2,}", "，", t)
    t = re.sub(r"，(?=[。！？])", "", t)
    t = re.sub(r"^，+", "", t)
    return t.strip()


def merge_paras(lines, width=140):
    paras, cur = [], ""
    for line in lines:
        if not cur or len(cur) + len(line) <= width:
            cur += line
        else:
            paras.append(cur)
            cur = line
    if cur:
        paras.append(cur)
    return paras


def main():
    ap = argparse.ArgumentParser(description="转写结果定稿清洗")
    ap.add_argument("trans", help="*_trans.json（云端或本地 asr_local 产出）")
    ap.add_argument("title", help="视频标题（用于输出目录与文件名）")
    ap.add_argument("--link", default="")
    ap.add_argument("--platform", default="other",
                    help="douyin | bilibili | youtube | other 等")
    ap.add_argument("--author", default="")
    ap.add_argument("--model", default="qwen-audio-3.0-asr-flash-filetrans (DashScope)")
    ap.add_argument("--fixes", default=None)
    ap.add_argument("--video", default=None)
    ap.add_argument("--out-root", default="output")
    a = ap.parse_args()

    if not os.path.isfile(a.trans):
        sys.exit(f"trans 文件不存在: {a.trans}")
    try:
        data = json.load(open(a.trans, encoding="utf-8-sig"))
        sents = data["transcripts"][0]["sentences"]
        dur = data["properties"]["original_duration_in_milliseconds"] / 1000
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
        sys.exit(
            f"trans.json 结构不符合预期（{e}）。\n"
            "需要包含 transcripts[0].sentences 与 "
            "properties.original_duration_in_milliseconds。\n"
            "云端请先跑 cloud_result_to_srt.py；本地请先跑 asr_local.py。"
        )
    if not sents:
        sys.exit("trans.json 中没有句子数据。")

    pairs = []
    if a.fixes:
        if not os.path.isfile(a.fixes):
            sys.exit(f"fixes 文件不存在: {a.fixes}")
        try:
            pairs = json.load(open(a.fixes, encoding="utf-8-sig"))
        except json.JSONDecodeError as e:
            sys.exit(f"fixes.json 解析失败: {e}")
        if not isinstance(pairs, list):
            sys.exit("fixes.json 应为 [[\"错字\",\"正字\"], ...] 数组")

    srt = "\n".join(
        f"{i}\n{fmt(s['begin_time'])} --> {fmt(s['end_time'])}\n{fix(s['text'].strip(), pairs)}\n"
        for i, s in enumerate(sents, 1)
    )
    raw = "\n".join(fix(s["text"].strip(), pairs) for s in sents)
    cleaned = [x for x in (strip_filler(fix(s["text"].strip(), pairs)) for s in sents) if x]
    clean = "\n\n".join(merge_paras(cleaned))

    safe_title = re.sub(r'[\\/:*?"<>|\n\r]', "_", a.title)[:80] or "untitled"
    outdir = os.path.join(a.out_root, safe_title)
    os.makedirs(outdir, exist_ok=True)
    t = a.title
    open(os.path.join(outdir, f"{t}_时间戳字幕.srt"), "w", encoding="utf-8").write(srt)
    open(os.path.join(outdir, f"{t}_逐句原始.txt"), "w", encoding="utf-8").write(raw)
    open(os.path.join(outdir, f"{t}_完整文案.txt"), "w", encoding="utf-8").write(clean)
    if a.video:
        if os.path.exists(a.video):
            shutil.copy(a.video, os.path.join(outdir, f"{t}_源视频.mp4"))
        else:
            print(f"[warn] 源视频不存在，跳过: {a.video}")
    info = "\n".join([
        f"原始视频链接: {a.link}",
        f"视频标题: {t}" + (f"（作者：{a.author}）" if a.author else ""),
        f"平台: {a.platform}，时长 {dur:.1f} 秒",
        "是否使用 ASR: 是",
        f"ASR 模型: {a.model}",
        f"同音字校正: {len(pairs)} 条" + (f"（见 {a.fixes}）" if a.fixes else ""),
        "后处理: 去语气词 + 自然段整理，未改写语序与观点",
        "处理时间: " + time.strftime("%Y-%m-%d %H:%M:%S"),
        "说明: 内容总结需人工/AI通读后另存 <标题>_内容总结.txt，本脚本不生成",
    ])
    open(os.path.join(outdir, f"{t}_来源说明.txt"), "w", encoding="utf-8").write(info)
    print(f"OK {len(sents)} sents -> {outdir}")
    print(f"已产出: 时间戳字幕 / 逐句原始 / 完整文案 / 来源说明"
          + (" / 源视频" if a.video and os.path.exists(a.video) else ""))
    print(f"请人工补: {os.path.join(outdir, t + '_内容总结.txt')}")


if __name__ == "__main__":
    main()
