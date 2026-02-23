---
date: 2026-02-20
tags:
  - lessonlearn
---

# YouTube RSS Feed 不稳定，需要 yt-dlp 回退机制

## 背景

频道"老李玩钱"发布了新视频，但 `uv run main.py` 报告没有新视频。排查发现 YouTube RSS feed (`/feeds/videos.xml?channel_id=...`) 频繁返回 404 或 500 错误，导致 feedparser 解析结果为空，系统误判为"无新视频"。

## 经验教训

- **YouTube RSS feed 服务不可靠**：同一个 URL 时而返回 200，时而返回 404/500，属于间歇性故障
- **User-Agent 很重要**：不带 UA 的请求更容易被 YouTube 拒绝（直接 404），加上浏览器 UA 可以降低被拒概率
- **feedparser 默认不带 User-Agent**：需要通过 `feedparser.parse(url, agent="...")` 显式设置
- **单一数据源是脆弱的**：只依赖 RSS 意味着 YouTube 一旦抽风，整个检测流程就失效
- **yt-dlp 的 `extract_flat` 模式是可靠的备选**：`yt_dlp` 用 `extract_flat=True` + `playlistend=N` 可以快速获取频道最新视频列表，不需要实际下载，作为 RSS 的回退非常合适

## 思考

- 关键数据获取环节应该始终有 fallback 机制，不要依赖单一外部服务
- 当外部 API 静默失败（返回空结果而非报错）时，系统容易产生误判——应该对"0 结果"的情况加日志/告警
- 未来如果 yt-dlp fallback 也不稳定，可以考虑 YouTube Data API v3 作为第三层保障
