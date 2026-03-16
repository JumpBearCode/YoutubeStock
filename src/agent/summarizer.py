import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
import warnings

from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from tavily import TavilyClient

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    from langgraph.prebuilt import create_react_agent

# ── Search-conditional prompt fragments ───────────────────────

_SINGLE_SEARCH_RULES_ON = """\
### 3. 卖出点位补充规则
- **如果博主没有推荐任何具体的买入/加仓/卖出操作**，则不使用搜索工具，只总结内容和观点即可。
- **如果博主推荐了买入或加仓但没有给出卖出点位**，请对每只缺少卖出点位的股票**逐只**使用 web search 查询该股票的目标价（target price）或阻力位（resistance level），并在结果中标注"（网络搜索补充）"。
- Web search 仅用于查询卖出点位，不用于其他用途。"""

_SINGLE_SEARCH_RULES_OFF = """\
### 3. 卖出点位说明
- 仅提取博主在视频中明确提到的卖出点位。
- 如果博主没有给出卖出建议，写"无"即可，不需要额外补充。"""

_BATCH_SEARCH_RULES_ON = """\
### 卖出点位补充规则
- **如果所有频道都没有推荐任何具体的买入/加仓/卖出操作**，则不使用搜索工具。
- **如果有频道推荐了买入或加仓但没有给出卖出点位**，请对每只缺少卖出点位的股票**逐只**使用 web search 查询该股票的目标价（target price）或阻力位（resistance level），并在结果中标注"（网络搜索补充）"。
- Web search 仅用于查询卖出点位，不用于其他用途。"""

_BATCH_SEARCH_RULES_OFF = """\
### 卖出点位说明
- 仅提取博主在视频中明确提到的卖出点位。
- 如果博主没有给出卖出建议，写"无"即可，不需要额外补充。"""

_SELL_NOTE_ON = '（如果有买入/加仓但缺少卖出建议，使用搜索补充，在理由列标注"（网络搜索补充）"）'
_SELL_NOTE_OFF = '（如果没有卖出建议，写"无（简短原因）"，不需要表格）'

# ── Prompt templates ──────────────────────────────────────────

SUMMARY_PROMPT_TEMPLATE = """\
你是一个专业的财经视频内容分析助手。以下是来自 YouTube 频道「{channel_name}」于 {date} 发布的视频转录文本。

## 任务

请对以下转录内容进行结构化总结：

### 1. 市场观点总结
总结博主对当前市场的整体观点和判断，包括对大盘走势、宏观经济、政策影响等方面的看法。
- 使用 Markdown 无序列表（bullet list）分点总结，按主题分条
- 每条可包含嵌套子点补充细节

### 2. 买入/加仓/卖出点位提取
从文本中提取博主明确提到的操作建议：
- **买入点位**：股票代码 + 建议买入价格/区间 + 理由
- **加仓点位**：股票代码 + 建议加仓价格/区间 + 理由
- **卖出点位**：股票代码 + 建议卖出价格/区间 + 理由

{search_rules}

## 输出格式

请使用 Markdown 格式输出，严格按照以下模板。不要输出转录文本，不要添加思考过程或前言。

---

# {channel_name} — 视频标题

**日期：** {date}

## 市场观点

- 主题一：概述
  - 补充细节
  - 补充细节
- 主题二：概述
  - 补充细节

## 买入建议

| 股票代码 | 买入价格/区间 | 理由 |
|---------|-------------|------|
| XXXX | $xxx - $xxx | 理由 |

（如果没有买入建议，写"无（简短原因，例如：本期为宏观展望，未涉及具体标的）"，不需要表格）

## 加仓建议

| 股票代码 | 加仓价格/区间 | 理由 |
|---------|-------------|------|
| XXXX | $xxx - $xxx | 理由 |

（如果没有加仓建议，写"无（简短原因）"，不需要表格）

## 卖出建议

| 股票代码 | 卖出价格/区间 | 理由 |
|---------|-------------|------|
| XXXX | $xxx - $xxx | 理由 |

（如果没有卖出建议且没有买入/加仓建议，写"无（简短原因）"，不需要表格）
{sell_note}

## 转录文本

{text}
"""

BATCH_PROMPT_TEMPLATE = """\
你是一个专业的财经视频内容分析助手。以下是来自多个 YouTube 频道的多期视频转录文本。

## 任务

### 第一部分：综合交易信号汇总
先汇总所有频道/视频中提到的买入、加仓、卖出建议，合并到统一的表格中，表格需包含"来源频道"列标注出处。

### 第二部分：逐期市场观点
然后对每一期视频**逐期单独**总结市场观点（不再重复交易信号，已在第一部分汇总）。
- 使用 Markdown 无序列表（bullet list）分点总结，按主题分条
- 每条可包含嵌套子点补充细节

{search_rules}

## 输出格式

请使用 Markdown 格式输出，严格按照以下模板。不要输出转录文本，不要添加思考过程或前言。

# 每日美股总结

**日期：** {date}

## 综合交易信号

### 买入建议

| 股票代码 | 价格/区间 | 理由 | 来源频道 |
|---------|----------|------|---------|
| XXXX | $xxx - $xxx | 理由 | 频道名 |

（如果所有频道都没有买入建议，写"无——本期各频道均为宏观市场展望，未涉及具体操作建议。"，不需要表格）

### 加仓建议

| 股票代码 | 价格/区间 | 理由 | 来源频道 |
|---------|----------|------|---------|
| XXXX | $xxx - $xxx | 理由 | 频道名 |

（如果没有加仓建议，写"无（简短原因）"，不需要表格）

### 卖出建议

| 股票代码 | 价格/区间 | 理由 | 来源频道 |
|---------|----------|------|---------|
| XXXX | $xxx - $xxx | 理由 | 频道名 |

（如果没有卖出建议且没有买入/加仓建议，写"无（简短原因）"，不需要表格）
{sell_note}

---

# 频道名 — 视频标题

**日期：** YYYY-MM-DD

## 市场观点

- 主题一：概述
  - 补充细节
- 主题二：概述

（此处只写市场观点，买入/加仓/卖出已在上方综合交易信号中汇总，不再重复）

---

（下一期视频，同上格式）

## 转录文本

{texts}
"""

MODEL = "deepseek-chat"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# Price per 1M tokens (USD) — DeepSeek V3.2
MODEL_PRICING = {
    "deepseek-chat": {"input": 0.28, "output": 0.42},
}

REPORT_DIR = ".storage/reports"


MAX_SEARCH_CALLS = 10


def _is_search_enabled() -> bool:
    return os.getenv("TAVILY_SEARCH_ENABLED", "true").lower() in ("true", "1", "yes")


@dataclass
class SummaryResult:
    success: bool
    output_path: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    search_calls: int = 0
    error: str | None = None
    prompt: str | None = None


def _calc_cost(input_tokens: int, output_tokens: int) -> float:
    pricing = MODEL_PRICING[MODEL]
    return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000


def _create_capped_search_tool(max_calls: int = MAX_SEARCH_CALLS):
    tavily_key = os.getenv("TAVILY_API_KEY")
    if not tavily_key:
        raise ValueError("TAVILY_API_KEY not set in .env")
    client = TavilyClient(api_key=tavily_key)
    call_count = 0

    @tool
    def capped_search(query: str) -> str:
        """Search the web for stock target prices and resistance levels. Only use this for looking up sell/exit price targets."""
        nonlocal call_count
        if call_count >= max_calls:
            return "搜索次数已达上限，请根据已有信息完成分析。"
        call_count += 1
        response = client.search(query, max_results=5, topic="finance")
        results = response.get("results", [])
        if not results:
            return "未找到相关结果。"
        return "\n\n".join(
            f"{r['title']}\n{r['content']}\n{r['url']}" for r in results
        )

    return capped_search, lambda: call_count


def _run_agent(prompt: str, search_enabled: bool) -> tuple[str, int, int, int]:
    """Run the ReAct agent with the given prompt. Returns (content, input_tokens, output_tokens, search_calls)."""
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY not set in .env")

    llm = ChatOpenAI(
        model=MODEL,
        base_url=DEEPSEEK_BASE_URL,
        api_key=api_key,
    )

    if search_enabled:
        search_tool, get_call_count = _create_capped_search_tool()
        tools = [search_tool]
    else:
        get_call_count = lambda: 0
        tools = []

    agent = create_react_agent(llm, tools=tools)

    result = agent.invoke({"messages": [
        {"role": "system", "content": prompt},
        {"role": "user", "content": "请开始分析。"},
    ]})

    total_input = 0
    total_output = 0
    for msg in result["messages"]:
        usage = getattr(msg, "usage_metadata", None)
        if usage:
            total_input += usage.get("input_tokens", 0)
            total_output += usage.get("output_tokens", 0)

    final_content = result["messages"][-1].content
    return final_content, total_input, total_output, get_call_count()


def extract_info_from_path(parsed_path: str) -> tuple[str, str]:
    """Extract channel_name and date from parsed file path.

    Path structure: .storage/{channel_name}/transcript/{timestamp_dir}/{title}_parsed.txt
    timestamp_dir format: YYYY-MM-DD-HH-MM-SS -> date = first 10 chars
    """
    parts = Path(parsed_path).parts
    for i, part in enumerate(parts):
        if part == "transcript" and i > 0 and i + 1 < len(parts):
            channel_name = parts[i - 1]
            timestamp_dir = parts[i + 1]
            date = timestamp_dir[:10]  # YYYY-MM-DD
            return channel_name, date
    return "Unknown", "Unknown"


def _get_title(parsed_path: str) -> str:
    """Extract video title from parsed file path stem."""
    title = Path(parsed_path).stem
    if title.endswith("_parsed"):
        title = title[: -len("_parsed")]
    return title


def _blockquote(text: str) -> str:
    """Wrap text in Markdown blockquote."""
    return "\n".join(f"> {line}" if line.strip() else ">" for line in text.splitlines())


def _append_transcript_single(agent_output: str, text: str) -> str:
    """Append a single transcript to agent's output."""
    return f"{agent_output}\n\n## 转录文本\n\n{_blockquote(text)}"


def _append_transcripts_multi(agent_output: str, parsed_files: list[tuple[str, str, str]], texts: list[str]) -> str:
    """Append all transcripts at the end of agent's output."""
    sections = []
    for (parsed_path, channel_name, _date), text in zip(parsed_files, texts):
        title = _get_title(parsed_path)
        sections.append(f"### {channel_name} — {title}\n\n{_blockquote(text)}")

    transcript_block = "\n\n".join(sections)
    return f"{agent_output}\n\n---\n\n## 转录文本\n\n{transcript_block}"


def summarize_transcript(
    parsed_path: str,
    channel_name: str,
    date: str,
    verbose: bool = False,
) -> SummaryResult | None:
    """Single-file mode: one _parsed.txt -> one _summary.txt in the same directory.
    Returns SummaryResult on success/failure, None if skipped."""
    load_dotenv()

    path = Path(parsed_path)
    if not path.exists():
        return SummaryResult(success=False, error=f"File not found: {parsed_path}")

    stem = path.stem
    if stem.endswith("_parsed"):
        output_name = stem[: -len("_parsed")] + "_summary.txt"
    else:
        output_name = stem + "_summary.txt"
    output_path = path.with_name(output_name)

    if output_path.exists():
        if verbose:
            print(f"  Skipping (already exists): {output_path}")
        return None

    text = path.read_text(encoding="utf-8")
    if not text.strip():
        if verbose:
            print(f"  Skipping (empty file): {parsed_path}")
        return None

    try:
        search_enabled = _is_search_enabled()
        prompt = SUMMARY_PROMPT_TEMPLATE.format(
            channel_name=channel_name,
            date=date,
            text=text,
            search_rules=_SINGLE_SEARCH_RULES_ON if search_enabled else _SINGLE_SEARCH_RULES_OFF,
            sell_note=_SELL_NOTE_ON if search_enabled else _SELL_NOTE_OFF,
        )
        content, total_input, total_output, search_calls = _run_agent(prompt, search_enabled)
        cost = _calc_cost(total_input, total_output)

        # Append transcript via f-string (not generated by agent)
        final_output = _append_transcript_single(content, text)
        output_path.write_text(final_output, encoding="utf-8")
        return SummaryResult(
            success=True,
            output_path=str(output_path),
            input_tokens=total_input,
            output_tokens=total_output,
            cost=cost,
            search_calls=search_calls,
            prompt=prompt,
        )
    except Exception as e:
        return SummaryResult(success=False, error=str(e))


def summarize_batch(
    parsed_files: list[tuple[str, str, str]],
    verbose: bool = False,
) -> SummaryResult:
    """Batch mode: multiple _parsed.txt -> one report in .storage/reports/summary_{timestamp}.txt.

    parsed_files: list of (parsed_path, channel_name, date)
    """
    load_dotenv()

    # Read all texts upfront
    texts = []
    for parsed_path, _channel_name, _date in parsed_files:
        texts.append(Path(parsed_path).read_text(encoding="utf-8"))

    search_enabled = _is_search_enabled()

    if len(parsed_files) == 1:
        parsed_path, channel_name, date = parsed_files[0]
        prompt = SUMMARY_PROMPT_TEMPLATE.format(
            channel_name=channel_name,
            date=date,
            text=texts[0],
            search_rules=_SINGLE_SEARCH_RULES_ON if search_enabled else _SINGLE_SEARCH_RULES_OFF,
            sell_note=_SELL_NOTE_ON if search_enabled else _SELL_NOTE_OFF,
        )
    else:
        # Build concatenated text block for multi-video prompt
        sections = []
        for (parsed_path, channel_name, date), text in zip(parsed_files, texts):
            title = _get_title(parsed_path)
            sections.append(
                f"--- 频道：{channel_name} | 日期：{date} | 标题：{title} ---\n\n{text}"
            )
        combined_texts = "\n\n".join(sections)

        # Use the date from the first file for the report header
        report_date = parsed_files[0][2]
        prompt = BATCH_PROMPT_TEMPLATE.format(
            date=report_date,
            texts=combined_texts,
            search_rules=_BATCH_SEARCH_RULES_ON if search_enabled else _BATCH_SEARCH_RULES_OFF,
            sell_note=_SELL_NOTE_ON if search_enabled else _SELL_NOTE_OFF,
        )

    try:
        content, total_input, total_output, search_calls = _run_agent(prompt, search_enabled)
        cost = _calc_cost(total_input, total_output)

        # Append transcripts via f-string (not generated by agent)
        if len(parsed_files) == 1:
            final_output = _append_transcript_single(content, texts[0])
        else:
            final_output = _append_transcripts_multi(content, parsed_files, texts)

        # Output to .storage/reports/summary_{timestamp}.txt
        report_dir = Path(REPORT_DIR)
        report_dir.mkdir(parents=True, exist_ok=True)
        ts_report = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
        output_path = report_dir / f"summary_{ts_report}.txt"
        output_path.write_text(final_output, encoding="utf-8")

        return SummaryResult(
            success=True,
            output_path=str(output_path),
            input_tokens=total_input,
            output_tokens=total_output,
            cost=cost,
            search_calls=search_calls,
            prompt=prompt,
        )
    except Exception as e:
        return SummaryResult(success=False, error=str(e))
