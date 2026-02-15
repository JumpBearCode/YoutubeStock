# YoutubeStock

YouTube 频道监控 & 视频/音频下载 & 语音转文字 & AI 分析 & 邮件推送工具。通过 RSS 检测新视频，使用 yt-dlp 下载，Whisper API 转录音频，AI Agent 整理段落并提取投资观点，完成后自动发送每日总结邮件，内置防封策略。

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

# OpenAI API key（parse / summary 命令必需，transcript 用 openai provider 时也需要）
OPENAI_API_KEY=sk-your-key
```

### 邮件推送配置

Pipeline 完成 summary 后自动发送每日总结邮件。使用 Gmail SMTP + App Password，零外部依赖。

1. 打开 [Google 安全设置](https://myaccount.google.com/security)，开启 **两步验证**
2. 打开 [App Passwords](https://myaccount.google.com/apppasswords)，创建一个（名称填 `YoutubeStock`）
3. 在 `.env` 中添加：

```bash
GMAIL_ADDRESS=你的邮箱@gmail.com
GMAIL_APP_PASSWORD=生成的16位密码
GMAIL_RECIPIENT=你的邮箱@gmail.com    # 收件人，可以和发件人一样
```

未配置时邮件自动跳过，不影响其他功能。

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

### `parse` — 转录文本整理

将 Whisper 逐行碎片转录整理为自然段落，修正音译错误（如"按摩店"→AMD），使用 OpenAI Agents SDK：

```bash
uv run python -m src.cli parse                          # 所有频道
uv run python -m src.cli parse --channel "老李玩钱"       # 指定频道
uv run python -m src.cli parse --path path/to/file.txt   # 单个文件
uv run python -m src.cli parse --model gpt-5.1           # 用 gpt-5.1（默认 gpt-5-mini）
```

- 输出 `{标题}_parsed.txt`，与原转录文件在同一目录
- 已有 `_parsed.txt` 会自动跳过
- 自动统计 token 用量和费用

### `summary` — AI 投资观点提取

读取 `_parsed.txt`，用 LangChain ReAct Agent（gpt-5.1 + DuckDuckGo 搜索）提取买入/加仓/卖出点位：

```bash
uv run python -m src.cli summary                          # 所有频道
uv run python -m src.cli summary --channel "老李玩钱"       # 指定频道
uv run python -m src.cli summary --path path/to/_parsed.txt # 单个文件
uv run python -m src.cli summary -v                        # 详细输出
```

- **`--path` 模式**：单文件 → 单 `_summary.txt`，输出在同目录
- **频道/全局模式**：所有 `_parsed.txt` 汇总为一份报告，按每期视频逐期总结，输出到 `.report/summary_{时间戳}.txt`
- 每期总结包含：频道、日期、标题、市场观点、买入/加仓/卖出建议
- 如果博主提了买入但没给卖出点位，自动搜索目标价/阻力位补充（标注"网络搜索补充"）
- 搜索次数上限 5 次，使用 DuckDuckGo（无需额外 API key）

### 全量 Pipeline — `main.py`

一条命令跑完全流程：下载 → 转录 → 整理 → 汇总 → 发邮件：

```bash
uv run python main.py                      # 默认：每频道最近 1 个视频
uv run python main.py --last_n 3 -v         # 每频道最近 3 个，详细输出
uv run python main.py --force-summary -v    # 强制跑 summary（即使没有新视频）
```

- **Phase 1**: Check & Process — 检测新视频、下载、转录、parse
- **Phase 2**: Batch Summary — 汇总所有 `_parsed.txt` 为一份报告
- **Phase 3**: Email Report — 将报告发送到指定邮箱（标题：`每日美股总结 {日期}`）

日志自动保存到 `.logs/log_{时间戳}.txt`。

## 文件结构

下载内容存在 `.storage/` 下：

```
.storage/
├── channel_cache.json              # 频道 URL → id/name 缓存
└── {频道名}/
    ├── download_history.json       # 该频道已下载视频记录
    ├── video/{时间戳}/{标题}.mp4
    ├── audio/{时间戳}/{标题}.mp3
    └── transcript/{时间戳}/
        ├── {标题}.json             # Whisper 时间戳 JSON
        ├── {标题}.txt              # Whisper 纯文本
        ├── {标题}_parsed.txt       # parse agent 整理后
        └── {标题}_summary.txt      # summary agent 总结（--path 模式）

.report/
└── summary_{时间戳}.txt             # summary agent 汇总报告（频道/全局模式）
```

## 防封策略

- 下载之间随机等待 10-45 秒（三角分布）
- 频道切换时等待更久（1.5x）
- 严格串行，无并发
- yt-dlp 内部限速（`sleep_interval`）
- 失败自动重试 3 次，指数退避（30s → 60s → 120s）
