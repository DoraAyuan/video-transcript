#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载 filetrans 结果并生成 逐句 JSON + SRT + 原始 TXT。

用法: python cloud_result_to_srt.py <task.json> <输出前缀>
输出: <前缀>_trans.json（含句/词级时间戳）、<前缀>.srt、<前缀>_raw.txt
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def fmt(ms):
    s = ms / 1000
    h, m = int(s // 3600), int(s % 3600 // 60)
    return f"{h:02d}:{m:02d}:{int(s % 60):02d},{int(round((s - int(s)) * 1000)):03d}"


def main():
    ap = argparse.ArgumentParser(description="下载 DashScope filetrans 转写结果")
    ap.add_argument("task", help="cloud_transcribe.py 产出的 <前缀>.json")
    ap.add_argument("prefix", help="输出前缀")
    a = ap.parse_args()

    if not os.path.isfile(a.task):
        sys.exit(f"task 文件不存在: {a.task}")
    try:
        task = json.load(open(a.task, encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        sys.exit(f"task JSON 解析失败: {e}")

    url = (task.get("output") or {}).get("transcription_url")
    if not url:
        status = (task.get("output") or {}).get("task_status", "?")
        sys.exit(
            f"task.json 里没有 transcription_url（task_status={status}）。\n"
            "请确认 cloud_transcribe.py 已成功结束；失败任务需重新提交转写。"
        )

    try:
        raw = urllib.request.urlopen(url, timeout=60).read().decode()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            sys.exit(
                "transcription_url 已过期或无效（通常仅 24h 有效）。\n"
                "请重新运行 cloud_transcribe.py 生成新任务后立刻下载。"
            )
        sys.exit(f"下载转写结果失败 HTTP {e.code}: {e.read()[:200]}")
    except urllib.error.URLError as e:
        sys.exit(f"网络错误，无法下载转写结果: {e}")

    try:
        data = json.loads(raw)
        sents = data["transcripts"][0]["sentences"]
        dur = data["properties"]["original_duration_in_milliseconds"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
        sys.exit(f"转写结果结构异常（{e}），原始响应前 200 字: {raw[:200]}")

    out_dir = os.path.dirname(os.path.abspath(a.prefix))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    json.dump(data, open(a.prefix + "_trans.json", "w", encoding="utf-8"), ensure_ascii=False)
    open(a.prefix + ".srt", "w", encoding="utf-8").write(
        "\n".join(
            f"{i}\n{fmt(s['begin_time'])} --> {fmt(s['end_time'])}\n{s['text'].strip()}\n"
            for i, s in enumerate(sents, 1)
        )
    )
    open(a.prefix + "_raw.txt", "w", encoding="utf-8").write(
        "\n".join(s["text"].strip() for s in sents)
    )
    print(f"sentences: {len(sents)}, duration: {dur/1000:.1f}s")
    print(f"输出: {a.prefix}_trans.json / {a.prefix}.srt / {a.prefix}_raw.txt")
    print(f"下一步: python scripts/finalize.py \"{a.prefix}_trans.json\" \"<视频标题>\"")


if __name__ == "__main__":
    main()
