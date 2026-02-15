from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
import warnings

from ddgs import DDGS
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    from langgraph.prebuilt import create_react_agent

SUMMARY_PROMPT_TEMPLATE = """\
你是一个专业的财经视频内容分析助手。以下是来自 YouTube 频道「{channel_name}」于 {date} 发布的视频转录文本。

## 任务

请对以下转录内容进行结构化总结：

### 1. 市场观点总结
总结博主对当前市场的整体观点和判断，包括对大盘走势、宏观经济、政策影响等方面的看法。

### 2. 买入/加仓/卖出点位提取
从文本中提取博主明确提到的操作建议：
- **买入点位**：股票代码 + 建议买入价格/区间 + 理由
- **加仓点位**：股票代码 + 建议加仓价格/区间 + 理由
- **卖出点位**：股票代码 + 建议卖出价格/区间 + 理由

### 3. 卖出点位补充规则
- **如果博主没有推荐任何具体的买入/加仓/卖出操作**，则不使用搜索工具，只总结内容和观点即可。
- **如果博主推荐了买入或加仓但没有给出卖出点位**，请对每只缺少卖出点位的股票**逐只**使用 web search 查询该股票的目标价（target price）或阻力位（resistance level），并在结果中标注"（网络搜索补充）"。
- Web search 仅用于查询卖出点位，不用于其他用途。

## 输出格式

请使用以下格式输出（纯文本，不使用 Markdown 标记）：

频道：{channel_name}
日期：{date}

【市场观点】
（总结博主的整体市场观点）

【买入建议】
- 股票代码 | 买入价格/区间 | 理由
（如果没有买入建议，写"无"）

【加仓建议】
- 股票代码 | 加仓价格/区间 | 理由
（如果没有加仓建议，写"无"）

【卖出建议】
- 股票代码 | 卖出价格/区间 | 理由
（如果没有卖出建议且没有买入/加仓建议，写"无"）
（如果有买入/加仓但缺少卖出建议，使用搜索补充，标注"（网络搜索补充）"）

## 转录文本

{text}
"""

BATCH_PROMPT_TEMPLATE = """\
你是一个专业的财经视频内容分析助手。以下是来自 YouTube 频道的多期视频转录文本。

## 任务

请对每一期视频**逐期单独总结**，不要合并。每期视频的总结包含：

### 1. 市场观点总结
总结该期博主对当前市场的整体观点和判断，包括对大盘走势、宏观经济、政策影响等方面的看法。

### 2. 买入/加仓/卖出点位提取
从该期文本中提取博主明确提到的操作建议：
- **买入点位**：股票代码 + 建议买入价格/区间 + 理由
- **加仓点位**：股票代码 + 建议加仓价格/区间 + 理由
- **卖出点位**：股票代码 + 建议卖出价格/区间 + 理由

### 3. 卖出点位补充规则
- **如果该期博主没有推荐任何具体的买入/加仓/卖出操作**，则不使用搜索工具，只总结内容和观点即可。
- **如果博主推荐了买入或加仓但没有给出卖出点位**，请对每只缺少卖出点位的股票**逐只**使用 web search 查询该股票的目标价（target price）或阻力位（resistance level），并在结果中标注"（网络搜索补充）"。
- Web search 仅用于查询卖出点位，不用于其他用途。

## 输出格式

请对每期视频按以下格式逐期输出（纯文本，不使用 Markdown 标记），各期之间用分隔线隔开：

================================================================================
频道：XXX
日期：YYYY-MM-DD
标题：XXX

【市场观点】
（总结该期博主的整体市场观点）

【买入建议】
- 股票代码 | 买入价格/区间 | 理由
（如果没有买入建议，写"无"）

【加仓建议】
- 股票代码 | 加仓价格/区间 | 理由
（如果没有加仓建议，写"无"）

【卖出建议】
- 股票代码 | 卖出价格/区间 | 理由
（如果没有卖出建议且没有买入/加仓建议，写"无"）
（如果有买入/加仓但缺少卖出建议，使用搜索补充，标注"（网络搜索补充）"）
================================================================================

## 转录文本

{texts}
"""

MODEL = "gpt-5.1"

MODEL_PRICING = {
    "gpt-5.1": {"input": 1.25, "output": 10.00},
}

REPORT_DIR = ".report"


@dataclass
class SummaryResult:
    success: bool
    output_path: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    error: str | None = None


def _calc_cost(input_tokens: int, output_tokens: int) -> float:
    pricing = MODEL_PRICING[MODEL]
    return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000


def _create_capped_search_tool(max_calls: int = 5):
    ddgs = DDGS()
    call_count = 0

    @tool
    def capped_search(query: str) -> str:
        """Search the web for stock target prices and resistance levels. Only use this for looking up sell/exit price targets."""
        nonlocal call_count
        if call_count >= max_calls:
            return "搜索次数已达上限，请根据已有信息完成分析。"
        call_count += 1
        results = ddgs.text(query, max_results=5)
        if not results:
            return "未找到相关结果。"
        return "\n\n".join(
            f"{r['title']}\n{r['body']}\n{r['href']}" for r in results
        )

    return capped_search


def _run_agent(prompt: str) -> tuple[str, int, int]:
    """Run the ReAct agent with the given prompt. Returns (content, input_tokens, output_tokens)."""
    llm = ChatOpenAI(model=MODEL)
    search_tool = _create_capped_search_tool(max_calls=5)
    agent = create_react_agent(llm, tools=[search_tool])

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
    return final_content, total_input, total_output


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
        prompt = SUMMARY_PROMPT_TEMPLATE.format(
            channel_name=channel_name,
            date=date,
            text=text,
        )
        content, total_input, total_output = _run_agent(prompt)
        cost = _calc_cost(total_input, total_output)

        output_path.write_text(content, encoding="utf-8")
        return SummaryResult(
            success=True,
            output_path=str(output_path),
            input_tokens=total_input,
            output_tokens=total_output,
            cost=cost,
        )
    except Exception as e:
        return SummaryResult(success=False, error=str(e))


def summarize_batch(
    parsed_files: list[tuple[str, str, str]],
    verbose: bool = False,
) -> SummaryResult:
    """Batch mode: multiple _parsed.txt -> one report in .report/summary_{timestamp}.txt.

    parsed_files: list of (parsed_path, channel_name, date)
    """
    load_dotenv()

    # Build concatenated text block
    sections = []
    for parsed_path, channel_name, date in parsed_files:
        path = Path(parsed_path)
        text = path.read_text(encoding="utf-8")
        title = path.stem
        if title.endswith("_parsed"):
            title = title[: -len("_parsed")]
        sections.append(
            f"--- 频道：{channel_name} | 日期：{date} | 标题：{title} ---\n\n{text}"
        )

    combined_texts = "\n\n".join(sections)

    try:
        prompt = BATCH_PROMPT_TEMPLATE.format(texts=combined_texts)
        content, total_input, total_output = _run_agent(prompt)
        cost = _calc_cost(total_input, total_output)

        # Output to .report/summary_{timestamp}.txt
        report_dir = Path(REPORT_DIR)
        report_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
        output_path = report_dir / f"summary_{timestamp}.txt"
        output_path.write_text(content, encoding="utf-8")

        return SummaryResult(
            success=True,
            output_path=str(output_path),
            input_tokens=total_input,
            output_tokens=total_output,
            cost=cost,
        )
    except Exception as e:
        return SummaryResult(success=False, error=str(e))
