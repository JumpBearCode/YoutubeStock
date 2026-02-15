# YoutubeStock

YouTube 频道监控 & 视频/音频下载 & 语音转文字工具。通过 RSS 检测新视频，使用 yt-dlp 下载，Whisper API 转录音频，内置防封策略。

## 安装

需要先装 ffmpeg：

```bash
brew install ffmpeg
```

安装项目依赖：

```bash
uv sync
```

## 配置

编辑 `config.py`，直接贴频道链接：

```python
CHANNELS = [
    {"url": "https://www.youtube.com/@SomeChannel", "last_n": 5},
    {"url": "https://www.youtube.com/@Another"},          # last_n 用全局默认值
    {"url": "https://www.youtube.com/channel/UCxxxxxx"},   # /channel/ 格式也行
]

GLOBAL_LAST_N = 3       # 默认每频道拉最近几个视频
CHECK_MAX_NEW = 5       # check 命令每频道最多下载几个新视频
SLEEP_MIN = 10           # 下载间隔最小秒数
SLEEP_MAX = 45           # 下载间隔最大秒数
```

程序会自动从 URL 解析出 channel_id 和频道名，结果缓存到 `.storage/channel_cache.json`。

### 转录配置

复制 `.env.example` 为 `.env`，填入 API key：

```bash
cp .env.example .env
```

```bash
# 选择 provider（默认 groq，便宜 12 倍）
TRANSCRIPTION_PROVIDER=groq

# Groq: $0.03/小时，free tier 每天 8 小时免费
# 先用 free key，额度用完自动切 paid key
GROQ_API_KEY=gsk-your-free-key
GROQ_API_KEY_PAID=gsk-your-paid-key    # 可选

# 或者用 OpenAI: $0.36/小时
# TRANSCRIPTION_PROVIDER=openai
# OPENAI_API_KEY=sk-your-key
```

## 用法

### `download` — 下载最近 N 个视频

拉取每个频道最近 N 个视频（由 `last_n` 控制），跳过已下载的：

```bash
uv run python -m src.cli download
uv run python -m src.cli download --verbose   # 显示详细路径信息
```

适合第一次使用，批量拉取近期内容。

### `check` — 检查并下载新视频

获取 RSS 全量 feed，过滤掉已下载的，只下载新视频（由CHECK_MAX_NEW定义）：

```bash
uv run python -m src.cli check
uv run python -m src.cli check --verbose
```

适合定期运行（比如 cron），增量同步新内容。

### `transcript` — 音频转文字

把已下载的音频通过 Whisper API 转成文字，输出 `.json`（带时间戳）和 `.txt`（纯文本）：

```bash
uv run python -m src.cli transcript                        # 所有频道
uv run python -m src.cli transcript --channel "老李玩钱"     # 指定频道
uv run python -m src.cli transcript --path path/to/file.mp3  # 单个文件
uv run python -m src.cli transcript --language zh -v        # 指定语言，详细输出
```

- 已有转录文件会自动跳过
- 音频先压缩（mono 16kHz 32kbps），超过 25MB 自动分片
- 支持中英文，不指定语言时自动检测
- Groq free key 触发限流时自动切换到 paid key

## 文件结构

下载内容存在 `.storage/` 下：

```
.storage/
├── channel_cache.json              # 频道 URL → id/name 缓存
└── {频道名}/
    ├── download_history.json       # 该频道已下载视频记录
    ├── video/{时间戳}/{标题}.mp4
    ├── audio/{时间戳}/{标题}.mp3
    └── transcript/{时间戳}/{标题}.json + .txt
```

## 防封策略

- 下载之间随机等待 10-45 秒（三角分布）
- 频道切换时等待更久（1.5x）
- 严格串行，无并发
- yt-dlp 内部限速（`sleep_interval`）
- 失败自动重试 3 次，指数退避（30s → 60s → 120s）
