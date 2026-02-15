# YoutubeStock

YouTube 频道监控 & 视频/音频下载工具。通过 RSS 检测新视频，使用 yt-dlp 下载，内置防封策略。

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

## 用法

### `download` — 下载最近 N 个视频

拉取每个频道最近 N 个视频（由 `last_n` 控制），跳过已下载的：

```bash
uv run python -m src.cli download
uv run python -m src.cli download --verbose   # 显示详细路径信息
```

适合第一次使用，批量拉取近期内容。

### `check` — 检查并下载新视频

获取 RSS 全量 feed，过滤掉已下载的，只下载新视频（最多 5 个）：

```bash
uv run python -m src.cli check
uv run python -m src.cli check --verbose
```

适合定期运行（比如 cron），增量同步新内容。

## 文件结构

下载内容存在 `.storage/` 下：

```
.storage/
├── channel_cache.json              # 频道 URL → id/name 缓存
└── {频道名}/
    ├── download_history.json       # 该频道已下载视频记录
    ├── video/{时间戳}/{标题}.mp4
    └── audio/{时间戳}/{标题}.mp3
```

## 防封策略

- 下载之间随机等待 10-45 秒（三角分布）
- 频道切换时等待更久（1.5x）
- 严格串行，无并发
- yt-dlp 内部限速（`sleep_interval`）
- 失败自动重试 3 次，指数退避（30s → 60s → 120s）
