"""Agent 端到端评测：工具选择、可回答判定与拒答行为。

检索评测只能证明「召回的菜对不对」，证明不了 Agent 是否选对工具、是否按
answerable 如实拒答。本脚本跑真实 LangGraph Agent（需要 DASHSCOPE_API_KEY、
MySQL/Redis 可用），用固定用例统计：

- 工具选择准确率：实际调用的工具集合是否等于期望集合；
- 可回答判定准确率：search_menu 返回的 answerable 是否符合预期；
- 拒答命中率：域外问题是否给出「没有 / 无法」类回复。

用法：
    python scripts/eval_agent.py --json data/eval_agent.json
"""
import argparse
import asyncio
import json
import os
import sys
import time

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

load_dotenv(override=True)

CASES_PATH = os.path.join(PROJECT_ROOT, "data", "eval_agent_cases.json")
REFUSAL_KEYWORDS = ("没有", "暂无", "无法", "抱歉", "不清楚", "不能", "办不到")


def load_cases(path=CASES_PATH):
    with open(path, "r", encoding="utf-8") as fp:
        return json.load(fp)


def _tool_names(messages):
    """收集本轮实际调用过的工具名"""
    from langchain_core.messages import AIMessage

    names = []
    for message in messages:
        if isinstance(message, AIMessage) and message.tool_calls:
            names.extend(call["name"] for call in message.tool_calls)
    return sorted(set(names))


def _search_answerable(messages):
    """取 search_menu 工具返回的 answerable 判定"""
    from langchain_core.messages import ToolMessage

    flags = []
    for message in messages:
        if not isinstance(message, ToolMessage) or message.name != "search_menu":
            continue
        try:
            payload = json.loads(message.content)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict) and "answerable" in payload:
            flags.append(payload.get("answerable"))
    return flags


def _final_answer(messages):
    from langchain_core.messages import AIMessage

    for message in reversed(messages):
        if isinstance(message, AIMessage) and message.content:
            return message.content
    return ""


def _build_judge_llm():
    """构造仅用于评审的 LLM（不绑定工具，temperature=0）"""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=os.getenv("DASHSCOPE_MODEL_NAME", "qwen-plus"),
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        base_url=os.getenv("DASHSCOPE_API_BASE"),
        temperature=0,
        timeout=30,
        max_retries=1,
    )


async def _judge_refusal(llm, query, answer):
    """用 LLM 判定回复是否如实处理了无法满足的请求，返回 (是否通过, 理由)"""
    import json as _json

    prompt = (
        "你是对话评测员。用户向餐厅点餐助手提出了一个超出能力的请求，分两类：\n"
        "A. 与点餐完全无关（天气、写代码等）：助手应明确表示无法处理，并把话题引导回点餐业务。\n"
        "B. 点餐相关但菜单里没有（如帝王蟹）：助手应如实说明没有这道菜，不能编造，可推荐相近菜品。\n"
        "判断助手是否按对应类别如实处理，措辞不限。\n"
        f"用户：{query}\n助手：{answer}\n\n"
        '只输出 JSON，不要解释：{"refused": true 或 false, "reason": "不超过 30 字"}'
    )
    try:
        response = await llm.ainvoke(prompt)
        text = response.content.strip()
        if text.startswith("```"):
            text = text.split("```")[1].lstrip("json").strip()
        payload = _json.loads(text)
        return bool(payload.get("refused")), str(payload.get("reason", ""))[:60]
    except Exception as exc:  # 评审失败不影响主流程
        return None, f"评审失败: {exc}"


async def evaluate(cases, user_id, judge=True):
    from langchain_core.messages import HumanMessage
    from agent.langgraph_agent import restaurant_agent

    judge_llm = _build_judge_llm() if judge else None
    details = []
    for case in cases:
        started = time.time()
        turns = case.get("turns") or [case["query"]]
        thread_id = f"eval-{case['id']}"
        messages = []
        # 多轮用例在同一 thread 内依次对话，工具集合按整通对话统计
        for turn in turns:
            state = await restaurant_agent.app.ainvoke(
                {
                    "messages": [HumanMessage(content=turn)],
                    "user_info": {"id": user_id, "username": "eval", "nickname": "评测"},
                },
                # LangGraph 的工具节点需要继承 config，缺失时会报 required config key
                config={"configurable": {"thread_id": thread_id}},
            )
            messages = state.get("messages", [])

        actual_tools = _tool_names(messages)
        expected_tools = sorted(case.get("expected_tools", []))
        answer = _final_answer(messages)

        answerable_flags = _search_answerable(messages)
        expected_answerable = case.get("expected_answerable")
        answerable_ok = None
        # Rerank 不可用时 answerable 为 None，此时无法判断，不计入准确率
        definite_flags = [flag for flag in answerable_flags if flag is not None]
        if expected_answerable is not None and definite_flags:
            answerable_ok = definite_flags[-1] == expected_answerable

        refusal_ok = None
        judge_ok = None
        judge_reason = ""
        if case.get("expect_refusal"):
            refusal_ok = any(word in answer for word in REFUSAL_KEYWORDS)
            if judge_llm is not None:
                judge_ok, judge_reason = await _judge_refusal(judge_llm, " ".join(turns), answer)

        details.append({
            "id": case["id"],
            "turns": turns,
            "expected_tools": expected_tools,
            "actual_tools": actual_tools,
            "tool_match": actual_tools == expected_tools,
            "expected_answerable": expected_answerable,
            "answerable_flags": answerable_flags,
            "answerable_ok": answerable_ok,
            "refusal_ok": refusal_ok,
            "refusal_judge": judge_ok,
            "refusal_judge_reason": judge_reason,
            "answer": answer,
            "latency_ms": round((time.time() - started) * 1000, 1),
        })

    total = len(details)
    tool_pass = sum(item["tool_match"] for item in details)
    answerable_items = [item for item in details if item["answerable_ok"] is not None]
    answerable_pass = sum(item["answerable_ok"] for item in answerable_items)
    refusal_items = [item for item in details if item["refusal_ok"] is not None]
    refusal_pass = sum(item["refusal_ok"] for item in refusal_items)
    judge_items = [item for item in details if item["refusal_judge"] is not None]
    judge_pass = sum(item["refusal_judge"] for item in judge_items)

    summary = {
        "case_count": total,
        "tool_accuracy": round(tool_pass / total, 4) if total else 0.0,
        "answerable_accuracy": (
            round(answerable_pass / len(answerable_items), 4) if answerable_items else None
        ),
        "refusal_accuracy": (
            round(refusal_pass / len(refusal_items), 4) if refusal_items else None
        ),
        "refusal_judge_accuracy": (
            round(judge_pass / len(judge_items), 4) if judge_items else None
        ),
        "avg_latency_ms": round(sum(item["latency_ms"] for item in details) / total, 1) if total else 0.0,
    }
    return summary, details


def main():
    parser = argparse.ArgumentParser(description="AiMenu Agent 端到端评测")
    parser.add_argument("--cases", default=CASES_PATH, help="用例 JSON 路径")
    parser.add_argument("--user-id", type=int, default=1, help="评测使用的用户 ID")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条，0 表示全部")
    parser.add_argument("--min-tool-accuracy", type=float, default=0.0,
                        help="工具选择准确率低于该值时以非零码退出，供 CI 使用")
    parser.add_argument("--no-judge", action="store_true",
                        help="关闭 LLM 评审，只用关键词判定拒答话术")
    parser.add_argument("--json", dest="json_path", help="将结果写入 JSON")
    args = parser.parse_args()

    if not os.getenv("DASHSCOPE_API_KEY"):
        print("[评测中止] 未配置 DASHSCOPE_API_KEY")
        raise SystemExit(2)

    cases = load_cases(args.cases)
    if args.limit:
        cases = cases[:args.limit]

    try:
        summary, details = asyncio.run(evaluate(cases, args.user_id, judge=not args.no_judge))
    except Exception as exc:  # 依赖 MySQL / Redis / 模型，失败时明确中止
        print(f"[评测中止] {exc}")
        raise SystemExit(2)

    print("=" * 68)
    for item in details:
        flags = []
        flags.append("工具✓" if item["tool_match"] else f"工具✗({item['actual_tools']})")
        if item["answerable_ok"] is not None:
            flags.append("拒答✓" if item["answerable_ok"] else "拒答✗")
        if item["refusal_ok"] is not None:
            flags.append("话术✓" if item["refusal_ok"] else "话术✗")
        if item["refusal_judge"] is not None:
            flags.append("评审✓" if item["refusal_judge"] else "评审✗")
        print(f"[{item['id']:<20}] {' '.join(flags)} | {item['latency_ms']}ms | {item['answer'][:40]}")
    print("-" * 68)
    print(
        f"用例 {summary['case_count']} | 工具准确率 {summary['tool_accuracy']:.4f} | "
        f"可回答判定 {summary['answerable_accuracy']} | "
        f"拒答话术(关键词) {summary['refusal_accuracy']} | "
        f"拒答话术(LLM 评审) {summary['refusal_judge_accuracy']} | "
        f"平均耗时 {summary['avg_latency_ms']}ms"
    )
    print("=" * 68)

    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as fp:
            json.dump({"summary": summary, "details": details}, fp, ensure_ascii=False, indent=2)
        print(f"评测明细已写入: {args.json_path}")

    if summary["tool_accuracy"] < args.min_tool_accuracy:
        print(
            f"[未达标] 工具准确率 {summary['tool_accuracy']:.4f} "
            f"< 阈值 {args.min_tool_accuracy:.4f}"
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
