# 完整工作流

## 〇、平台支持速览

| 类型 | 做法 |
|---|---|
| B 站 | 下文「一」；AI 字幕加 `--browser` |
| 抖音 | 下文「二」；必须浏览器嗅探 |
| YouTube 等 yt-dlp 站点 | 与 B 站类似：`extract.py <URL>`，有字幕则直接产出 SRT |
| 本地文件 | 跳过 extract，从云端或本地 ASR 起步 |

云端 ASR 与 `finalize.py` 对站点无感。

## 一、B 站链接（及其它 yt-dlp 友好站点）

1. `python scripts/extract.py <URL> --browser edge`
   - 有 CC/AI 字幕 → 直接产出 `<标题>_时间戳字幕.srt` + `<标题>_逐句原始.txt`，**跳过 ASR**（AI 字幕需要登录态：`--browser` 复用浏览器 cookies，浏览器需处于关闭状态否则数据库被锁）。
   - 无字幕 → 自动下载音频到 `output/<标题>/audio.*`，继续第二步。
2. `python scripts/cloud_transcribe.py <音频> work/out`
3. `python scripts/cloud_result_to_srt.py work/out.json work/out`
4. `python scripts/finalize.py work/out_trans.json "<标题>" --link <URL> --platform bilibili`
5. **内容总结**（定稿后人工/AI 通读 `<标题>_完整文案.txt`，手写 `<标题>_内容总结.txt`）：
   - 结构：视频基本信息 → 一句话概括 → 核心要点（编号列表）→（长视频可加结构脉络）→ 金句摘录
   - 半页以内；引用必须来自文案原文；总结不替代逐字文案，两者并存交付。

YouTube 等站点把第 1 步 URL 换掉即可；第 4 步 `--platform youtube`（或 `other`）。

## 二、抖音链接（平台风控，需一次浏览器嗅探）

`yt-dlp` 直连会报 `Fresh cookies are needed`，老解析接口已下线。可行路线：

1. **解析短链**：`curl -sIL v.douyin.com/xxxx` 跟随重定向，从 URL 里取视频 ID。
2. **真实浏览器打开** `https://www.douyin.com/video/<ID>`（自动通过 JS 挑战/验证码）。
3. **嗅探媒体流**：读取页面网络请求（Performance API 或开发者工具 Network 面板），找
   `douyinvod.com/.../media-video-hvc1/...`（HEVC 视频流）与 `.../media-audio-und-mp4a/...`（音频流）。
   - 若 `<video>.currentSrc` 是 `blob:`，说明走 MSE 分片，双流地址就在网络请求里。
   - 部分视频仍是直链 mp4（`...mime_type=video_mp4`），单流即可。
4. **curl 下载**两流（带 `-A <浏览器UA>` 与 `-H "Referer: https://www.douyin.com/"`，URL 有时效，拿到尽快下）。
5. **合成**：`ffmpeg -i vid.mp4 -i aud.mp4 -c copy -map 0:v:0 -map 1:a:0 merged.mp4`，再 `-vn -ac 1 -ar 16000 audio.wav`。
6. 后续同 B 站第 2-4 步（`--platform douyin`）。

> 用带浏览器的 AI Agent（如 ZCode/Claude 的 browser 工具）时，第 2-3 步可由 Agent 自动完成。

## 三、质量校验（强烈建议）

- **烧录字幕**：`ffmpeg -ss <t> -i merged.mp4 -frames:v 1 f.jpg` 抽帧读画面底部字幕，与 ASR 对照；很多知识类视频全程有烧录字幕，等于免费 ground-truth。
- **幻灯片/课件帧**：专有名词、引文以画面文字为准（例：ASR 听出"江枫"，幻灯片"《致云雀》末节·江枫 译"才能确认不是错字）。
- 把确认后的错字写成 `fixes.json`（`[["工业之忧","功业之忧"], ...]`）交给 `finalize.py`。

## 四、本地兜底（无密钥/断网）

本地脚本会输出与云端同 schema 的 `*_trans.json`，可直接 `finalize.py`。

```bash
pip install faster-whisper

# 模型权重建议 curl 断点续传从 hf-mirror 下载（huggingface_hub 经镜像易卡死）
curl -C - -o models/faster-whisper-small/model.bin https://hf-mirror.com/Systran/faster-whisper-small/resolve/main/model.bin
# 同目录补齐 config.json / tokenizer.json / vocabulary.txt（large-v3 还需 preprocessor_config.json，vocabulary 为 .json）

python scripts/asr_local.py audio.wav models/faster-whisper-small work/out
python scripts/finalize.py work/out_trans.json "<标题>" --model faster-whisper/small
```

无 GPU 时 small 是速度/精度甜点；medium 以上仅适合 3 分钟内音频。

## 五、常见坑

| 现象 | 原因/解法 |
|---|---|
| `Could not copy Chrome cookie database` | 浏览器正在运行，cookies 库被锁，关闭浏览器再 `--browser edge` |
| chat 接口 400 / "URL does not appear to be valid" | 该系列模型不收 base64，走 filetrans + OSS 临时上传 |
| 多进程并行 ASR 反而慢 | OpenMP 线程池争抢，单进程满线程即可 |
| large-v3 报 `expected (1,128,3000) got (1,80,3000)` | 缺 `preprocessor_config.json` |
| 转写结果 URL 404 | `transcription_url` 24 小时过期，及时下载；过期后重新 `cloud_transcribe.py` |
| 音频增强（去噪/带通）后识别更差 | whisper 对原始音频最稳，别做额外处理 |
| `finalize.py` 报结构不符合预期 | 本地请用新版 `asr_local.py`（会写 `_trans.json`）；云端先 `cloud_result_to_srt.py` |
