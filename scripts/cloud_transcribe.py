#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DashScope 云端语音转写（qwen-audio ASR filetrans）：本地音频 -> 带时间戳的转写 JSON。

用法:
  set DASHSCOPE_API_KEY=sk-xxx          # Windows（Linux/macOS 用 export）
  python cloud_transcribe.py <音频文件> <输出前缀> [--model ID] [--base-url URL] [--lang zh]
                             [--key-file path] [--poll-timeout 秒]

输出:
  <前缀>.json    任务结果（含 transcription_url，24h 内有效）
  <前缀>.mp3     实际上传的压缩音频

说明:
  - chat/completions 接口不接受 base64 音频，长音频必须走 filetrans 异步链路：
    getPolicy -> OSS 临时上传 -> 异步提交 -> 轮询。
  - base-url 默认官方端点；工作空间(MaaS)端点也可，自动剥离 /compatible-mode/v1。
  - 密钥只从环境变量或 --key-file 读取；OSS 表单上传不携带 Bearer，避免密钥进上传主机请求头。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

MODEL_DEFAULT = "qwen-audio-3.0-asr-flash-filetrans"
BASE_DEFAULT = os.environ.get("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com")
TERMINAL_STATUS = {"SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN"}


def get_key(args):
    key = os.environ.get("DASHSCOPE_API_KEY")
    if not key and args.key_file and os.path.exists(args.key_file):
        key = open(args.key_file, encoding="utf-8-sig").read().strip()
    if not key:
        sys.exit("缺少密钥：请设置环境变量 DASHSCOPE_API_KEY，或用 --key-file 指定密钥文件（勿提交到仓库）")
    return key


def ffmpeg_exe():
    return (
        os.environ.get("FFMPEG_PATH")
        or shutil.which("ffmpeg")
        or sys.exit("找不到 ffmpeg，请安装并加入 PATH 或设置 FFMPEG_PATH")
    )


def post(url, key=None, body=None, headers=None, method=None, timeout=120, auth=True):
    """HTTP 请求。auth=False 时不发送 Authorization（用于 OSS 表单上传）。"""
    h = {}
    if auth and key:
        h["Authorization"] = f"Bearer {key}"
    if headers:
        h.update(headers)
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except urllib.error.URLError as e:
        return 0, str(e.reason if hasattr(e, "reason") else e)


def main():
    ap = argparse.ArgumentParser(description="DashScope filetrans 云端转写")
    ap.add_argument("audio")
    ap.add_argument("prefix")
    ap.add_argument("--model", default=MODEL_DEFAULT)
    ap.add_argument("--base-url", default=BASE_DEFAULT)
    ap.add_argument("--lang", default="zh")
    ap.add_argument("--key-file", default=None, help="密钥文件路径（勿提交到仓库）")
    ap.add_argument("--poll-timeout", type=int, default=1800,
                    help="任务轮询最长等待秒数（默认 1800）")
    a = ap.parse_args()

    if not os.path.isfile(a.audio):
        sys.exit(f"音频不存在: {a.audio}")
    key = get_key(a)
    root = a.base_url.rstrip("/")
    for suffix in ("/compatible-mode/v1", "/v1"):
        if root.endswith(suffix):
            root = root[: -len(suffix)]
            break

    out_dir = os.path.dirname(os.path.abspath(a.prefix))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # 1) 压缩为 16k 单声道 mp3，减小上传体积
    mp3 = a.prefix + ".mp3"
    subprocess.run(
        [ffmpeg_exe(), "-y", "-v", "error", "-i", a.audio,
         "-ar", "16000", "-ac", "1", "-b:a", "48k", mp3],
        check=True,
    )
    print(f"compressed: {os.path.getsize(mp3)/1e6:.1f} MB -> {mp3}")

    # 2) getPolicy
    st, body = post(f"{root}/api/v1/uploads?action=getPolicy&model={a.model}", key)
    if st != 200:
        sys.exit(f"getPolicy 失败 {st}: {body[:300]}")
    try:
        pol = json.loads(body)["data"]
    except (json.JSONDecodeError, KeyError, TypeError):
        sys.exit(f"getPolicy 响应异常: {body[:300]}")
    fname = os.path.basename(mp3)
    key_field = pol["upload_dir"] + "/" + fname

    # 3) multipart 上传到 OSS（不携带 DashScope Bearer）
    boundary = "----vt" + uuid.uuid4().hex
    fields = {
        "key": key_field,
        "policy": pol["policy"],
        "OSSAccessKeyId": pol["oss_access_key_id"],
        "signature": pol["signature"],
        "x-oss-object-acl": pol["x_oss_object_acl"],
        "x-oss-forbid-overwrite": pol["x_oss_forbid_overwrite"],
        "success_action_status": "200",
    }
    parts = b""
    for k, v in fields.items():
        parts += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    parts += (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"{fname}\"\r\nContent-Type: audio/mpeg\r\n\r\n"
    ).encode()
    parts += open(mp3, "rb").read() + f"\r\n--{boundary}--\r\n".encode()
    st, body = post(
        pol["upload_host"],
        key=None,
        body=parts,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
        auth=False,
        timeout=300,
    )
    if st not in (200, 204):
        sys.exit(f"OSS 上传失败 {st}: {body[:300]}")
    print("OSS upload ok")

    # 4) 异步提交 + 轮询（带超时与错误处理）
    st, body = post(
        f"{root}/api/v1/services/audio/asr/transcription",
        key,
        body={
            "model": a.model,
            "input": {"file_urls": ["oss://" + key_field]},
            "parameters": {"channel_id": [0], "language_hints": [a.lang]},
        },
        headers={
            "X-DashScope-Async": "enable",
            "X-DashScope-OssResourceResolve": "enable",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        d = json.loads(body)
    except json.JSONDecodeError:
        sys.exit(f"提交失败 {st}: 响应不是 JSON: {body[:300]}")
    if st != 200 or "task_id" not in d.get("output", {}):
        sys.exit(f"提交失败 {st}: {body[:300]}")
    tid = d["output"]["task_id"]
    print("task:", tid)

    deadline = time.time() + max(30, a.poll_timeout)
    status = "?"
    while time.time() < deadline:
        st, body = post(f"{root}/api/v1/tasks/{tid}", key)
        if st != 200:
            print(f"[warn] 查询任务失败 {st}: {body[:160]}", flush=True)
            time.sleep(5)
            continue
        try:
            d = json.loads(body)
        except json.JSONDecodeError:
            print(f"[warn] 任务响应不是 JSON: {body[:160]}", flush=True)
            time.sleep(5)
            continue
        status = d.get("output", {}).get("task_status", "?")
        print("status:", status, flush=True)
        if status in TERMINAL_STATUS:
            break
        time.sleep(5)
    else:
        sys.exit(f"轮询超时（{a.poll_timeout}s），task_id={tid}。可用同一 id 手动查询后重试下载。")

    if status != "SUCCEEDED":
        sys.exit("转写失败: " + json.dumps(d, ensure_ascii=False)[:500])
    json.dump(d, open(a.prefix + ".json", "w", encoding="utf-8"), ensure_ascii=False)
    print("saved:", a.prefix + ".json",
          "| transcription_url 24h 内有效，请尽快用 cloud_result_to_srt.py 下载")
    print(f"下一步: python scripts/cloud_result_to_srt.py \"{a.prefix}.json\" \"{a.prefix}\"")


if __name__ == "__main__":
    main()
