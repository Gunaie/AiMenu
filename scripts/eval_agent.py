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


async def evaluate(cases, user_id):
    from langchain_core.messages import HumanMessage
    from agent.langgraph_agent import restaurant_agent

    details = []
    for case in cases:
        started = time.time()
        state = await restaurant_agent.app.ainvoke(
            {
                "messages": [HumanMessage(content=case["query"])],
                "user_info": {"id": user_id, "username": "eval", "nickname": "评测"},
            },
            # LangGraph 的工具节点需要继承 config，缺失时会报 required config key
            config={"configurable": {"thread_id": f"eval-{case['id']}"}},
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
            answerable_ok = expected_answerable in definite_flags

        refusal_ok = None
        if case.get("expect_refusal"):
            refusal_ok = any(word in answer for word in REFUSAL_KEYWORDS)

        details.append({
            "id": case["id"],
            "query": case["query"],
            "expected_tools": expected_tools,
            "actual_tools": actual_tools,
            "tool_match": actual_tools == expected_tools,
            "expected_answerable": expected_answerable,
            "answerable_flags": answerable_flags,
            "answerable_ok": answerable_ok,
            "refusal_ok": refusal_ok,
            "answer": answer,
            "latency_ms": round((time.time() - started) * 1000, 1),
        })

    total = len(details)
    tool_pass = sum(item["tool_match"] for item in details)
    answerable_items = [item for item in details if item["answerable_ok"] is not None]
    answerable_pass = sum(item["answerable_ok"] for item in answerable_items)
    refusal_items = [item for item in details if item["refusal_ok"] is not None]
    refusal_pass = sum(item["refusal_ok"] for item in refusal_items)

    summary = {
        "case_count": total,
        "tool_accuracy": round(tool_pass / total, 4) if total else 0.0,
        "answerable_accuracy": (
            round(answerable_pass / len(answerable_items), 4) if answerable_items else None
        ),
        "refusal_accuracy": (
            round(refusal_pass / len(refusal_items), 4) if refusal_items else None
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
    parser.add_argument("--json", dest="json_path", help="将结果写入 JSON")
    args = parser.parse_args()

    if not os.getenv("DASHSCOPE_API_KEY"):
        print("[评测中止] 未配置 DASHSCOPE_API_KEY")
        raise SystemExit(2)

    cases = load_cases(args.cases)
    if args.limit:
        cases = cases[:args.limit]

    try:
        summary, details = asyncio.run(evaluate(cases, args.user_id))
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
        print(f"[{item['id']:<20}] {' '.join(flags)} | {item['latency_ms']}ms | {item['answer'][:40]}")
    print("-" * 68)
    print(
        f"用例 {summary['case_count']} | 工具准确率 {summary['tool_accuracy']:.4f} | "
        f"可回答判定 {summary['answerable_accuracy']} | "
        f"拒答命中 {summary['refusal_accuracy']} | 平均耗时 {summary['avg_latency_ms']}ms"
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
