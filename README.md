# video-transcript-toolkit

给一个视频链接，稳定拿到**完整口播文案**（带时间戳字幕 + 纯文本）。
字幕优先取平台现成轨道，取不到再走 ASR；ASR 主推阿里云百炼 `qwen-audio` 云端转写（中文准、快），本地 faster-whisper 作断网兜底（与云端同一套 `trans.json` schema，可直接定稿）。

> 实战验证：14 分钟课堂视频，云端转写 15 秒返回、词级置信度 1.0，产出 131 句带时间戳文案。

## 支持哪些站点

| 站点 | 支持程度 | 说明 |
|---|---|---|
| 哔哩哔哩 | 主力 | CC/AI 字幕优先；AI 字幕加 `--browser edge` 复用登录态 |
| 抖音 | 主力（需嗅探） | 直连被风控；按 [docs/workflow.md](docs/workflow.md) 浏览器嗅探双流 |
| YouTube / 西瓜 / Vimeo 等 | 通用 | `extract.py` 基于 yt-dlp，能下到字幕就跳过 ASR，否则下音频再转写 |
| 其它 yt-dlp 支持站 | 通用 | 同一命令；成功率取决于该站反爬与是否有字幕轨 |
| 本地音视频文件 | 支持 | 跳过 extract，直接 `cloud_transcribe.py` 或 `asr_local.py` |

云端 ASR 与定稿清洗与站点无关，只要拿到音频或 `trans.json` 即可。

## 流程总览

```
链接 ──> ① 拿音频/字幕 ──> ② 云端 ASR ──> ③ 定稿清洗 ──> output/<标题>/
   通用: yt-dlp 字幕优先        filetrans 异步链路        同音字校正+去语气词    脚本五件套
   抖音: 浏览器嗅探 DASH 双流   (本地 whisper 兜底)       自然段整理            +人工内容总结
```

## 快速开始

```bash
pip install -r requirements.txt          # yt-dlp（云端转写零依赖，纯标准库）
set DASHSCOPE_API_KEY=sk-xxxx            # 阿里云百炼控制台创建；Linux/macOS 用 export

# B 站：字幕优先，没字幕自动下音频
python scripts/extract.py "https://www.bilibili.com/video/BVxxxx" --browser edge
# YouTube 等同理
python scripts/extract.py "https://www.youtube.com/watch?v=xxxx"
```

### 云端一条龙（extract 未直接吐出字幕时）

```bash
python scripts/cloud_transcribe.py "output/<标题>/audio.m4a" work/out
python scripts/cloud_result_to_srt.py work/out.json work/out
python scripts/finalize.py work/out_trans.json "<标题>" --link "<URL>" --platform bilibili

# 人工/AI 通读完整文案后，手写总结（脚本不生成）
#   output/<标题>/<标题>_内容总结.txt
```

### 本地兜底一条龙（无密钥 / 断网）

```bash
pip install faster-whisper
python scripts/asr_local.py "output/<标题>/audio.m4a" small "work/out"
python scripts/finalize.py work/out_trans.json "<标题>" --model faster-whisper/small
```

### 任意本地音频

```bash
python scripts/cloud_transcribe.py work/audio.wav work/out
python scripts/cloud_result_to_srt.py work/out.json work/out
python scripts/finalize.py work/out_trans.json "视频标题" --link URL --platform other --fixes fixes.json --video work/merged.mp4
```

抖音链接步骤见 [docs/workflow.md](docs/workflow.md)。

## 输出结构（文件名自带视频名）

`finalize.py` 脚本产出四件（或五件，若提供源视频）：

```
output/<视频标题>/
├── <标题>_时间戳字幕.srt
├── <标题>_逐句原始.txt        # 逐句原始
├── <标题>_完整文案.txt        # 去语气词、修同音字、分自然段（不改语序与观点）
├── <标题>_来源说明.txt        # 链接/方法/模型/校验依据/失败记录
└── <标题>_源视频.mp4          # 仅当传入 --video 时
```

完整交付再补一份（人工/AI）：

```
└── <标题>_内容总结.txt        # 一句话概括/核心要点/结构脉络/金句，半页内
```

> 逐字文案与内容总结互不替代：文案保证"说了什么就是什么"，总结负责"5 分钟看懂全片"。

## 脚本一览

| 脚本 | 作用 |
|---|---|
| `scripts/extract.py` | 获取阶段：现成字幕优先（yt-dlp → 真 SRT）/ 音频下载 / 抖音给出嗅探指引 |
| `scripts/cloud_transcribe.py` | 云端转写：getPolicy → OSS 上传（不带 API Key 头）→ 异步提交 → 轮询（带超时） |
| `scripts/cloud_result_to_srt.py` | 下载转写结果 → SRT / 逐句 TXT / 句级 JSON |
| `scripts/asr_local.py` | 本地兜底：faster-whisper CPU int8，输出与云端同 schema 的 `_trans.json` |
| `scripts/finalize.py` | 定稿：同音字校正 + 去语气词 + 自然段；云端/本地 trans.json 均可 |

## 关键经验（踩坑记录）

- 抖音对 `yt-dlp`/`curl` 全面设防：SSR 不内嵌播放数据、老解析 API 已下线、桌面页走验证码。**真实浏览器加载页面后从网络请求嗅探 `media-video`/`media-audio` 双流**是唯一稳定的免登录路线（等价于视频下载插件的原理）。
- DashScope 的 chat 接口**不接受 base64 音频**，长音频必须走 `filetrans` 异步链路 + `oss://` 临时上传。
- 无 GPU 的机器上 whisper medium/large 跑 10 分钟级音频不可行；**多进程并行反而更慢**（OpenMP 线程池互相争抢），单进程满线程才对。
- 从 hf-mirror 下载 whisper 模型用 `curl -C -` 断点续传（`huggingface_hub` 经镜像易卡死）；large-v3 需补 `preprocessor_config.json`。
- 转写文本别大段贴进带内容审核的 LLM 对话，清洗一律在文件里做。

## 安全

- 密钥**只**从环境变量 `DASHSCOPE_API_KEY` 或 `--key-file` 读取；OSS 表单上传请求不携带 Bearer。
- `.gitignore` 已屏蔽 `.env`/密钥文件/产物目录/`.spec-workflow/`。
- 仓库不包含任何真实链接、密钥与视频内容。

## License

[MIT](LICENSE)
