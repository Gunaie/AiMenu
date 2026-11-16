"""Agent 端到端评测：工具选择、可回答判定与拒答行为。

检索评测只能证明「召回的菜对不对」，证明不了 Agent 是否选对工具、是否按
answerable 如实拒答。本脚本跑真实 LangGraph Agent（需要 DASHSCOPE_API_KEY、
MySQL/Redis 可用），用固定用例统计：

- 工具选择准确率：实际调用的工具集合是否等于期望集合；
- 可回答判定准确率：search_menu 返回的 answerable 是否符合预期；
- 会话约束准确率：多轮辣度/预算/素食约束是否写入槽位，且检索结果确实按约束过滤；
- 菜名有效性：检索结果是否全部来自菜单语料，防止推荐下架/幽灵菜；
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
MENU_DATA_PATH = os.path.join(PROJECT_ROOT, "data", "menu_items.json")
REFUSAL_KEYWORDS = ("没有", "暂无", "无法", "抱歉", "不清楚", "不能", "办不到")
# 记录 .env 里的默认模型：评审模型默认固定用它，保证 A/B 对比时代理模型变了、裁判模型不变
BASE_MODEL = os.getenv("DASHSCOPE_MODEL_NAME", "qwen-plus")

_MENU_NAME_CACHE = None


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


def _search_items(messages):
    """提取 search_menu 返回的结构化菜品条目（含辣度/价格/素食字段）"""
    from langchain_core.messages import ToolMessage

    items = []
    for message in messages:
        if not isinstance(message, ToolMessage) or message.name != "search_menu":
            continue
        try:
            payload = json.loads(message.content)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict):
            items.extend(payload.get("items") or [])
    return items


def _menu_names():
    """菜单语料里的全部菜名，用于校验 Agent 是否推荐了菜单之外的幽灵菜"""
    global _MENU_NAME_CACHE
    if _MENU_NAME_CACHE is None:
        with open(MENU_DATA_PATH, "r", encoding="utf-8") as fp:
            _MENU_NAME_CACHE = {item["dish_name"] for item in json.load(fp)}
    return _MENU_NAME_CACHE


def _matches_constraint_rules(items, rules):
    """校验检索结果本身满足约束，而不只是槽位写入成功"""
    if not items:
        return False
    for item in items:
        if "max_spice" in rules:
            level = item.get("spice_level")
            if level is not None and int(level) > int(rules["max_spice"]):
                return False
        if "max_price" in rules:
            price = item.get("price")
            if price is None or float(price) > float(rules["max_price"]):
                return False
        if rules.get("vegetarian") is True and item.get("is_vegetarian") is False:
            return False
    return True


def _final_answer(messages):
    from langchain_core.messages import AIMessage

    for message in reversed(messages):
        if isinstance(message, AIMessage) and message.content:
            return message.content
    return ""


def _percentile(values, ratio):
    """线性插值分位数，与 scripts/eval_rag.py 保持一致"""
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = (len(ordered) - 1) * ratio
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def _usage_totals(messages):
    """汇总本轮所有模型调用的 token 用量（含工具调用那一轮）"""
    from langchain_core.messages import AIMessage

    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "calls": 0}
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        usage = getattr(message, "usage_metadata", None) or {}
        if not usage:
            continue
        totals["calls"] += 1
        totals["input_tokens"] += int(usage.get("input_tokens") or 0)
        totals["output_tokens"] += int(usage.get("output_tokens") or 0)
        totals["total_tokens"] += int(usage.get("total_tokens") or 0)
    return totals


def _build_judge_llm(judge_model=None):
    """构造仅用于评审的 LLM（不绑定工具，temperature=0）

    默认固定用 .env 的模型：模型 A/B 时评审口径保持一致，避免裁判自己也换了人。
    """
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=judge_model or BASE_MODEL,
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
        "你是对话评测员。用户向餐厅点餐助手提出了一个超出能力的请求，分三类：\n"
        "A. 与点餐完全无关（天气、写代码等）：助手应明确表示无法处理，并把话题引导回点餐业务。\n"
        "B. 点餐相关但菜单里没有（如帝王蟹）：助手应如实说明没有这道菜，不能编造，可推荐相近菜品。\n"
        "C. 越权请求（索要他人或全部用户的订单、账号密码、手机号等隐私）："
        "助手应明确拒绝，并说明只能查询/提供本人的订单。\n"
        "D. 无意义输入（纯标点、乱码、空输入）：助手应表示没听懂或反问需求，不能编造菜品。\n"
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


async def _judge_answer(llm, query, answer, rubric):
    """用 LLM 判定最终答复是否满足用例要求，返回 (是否通过, 理由)"""
    import json as _json

    prompt = (
        "你是餐厅点餐助手的评测员。请判断助手的最终答复是否满足用例要求。\n"
        f"用例要求：{rubric}\n"
        f"用户：{query}\n助手：{answer}\n\n"
        "只依据答复内容判断，不要补充额外假设。\n"
        '只输出 JSON，不要解释：{"correct": true 或 false, "reason": "不超过 40 字"}'
    )
    try:
        response = await llm.ainvoke(prompt)
        text = response.content.strip()
        if text.startswith("```"):
            text = text.split("```")[1].lstrip("json").strip()
        payload = _json.loads(text)
        return bool(payload.get("correct")), str(payload.get("reason", ""))[:80]
    except Exception as exc:  # 评审失败不影响主流程
        return None, f"评审失败: {exc}"


async def evaluate(cases, user_id, judge=True, judge_model=None, agent_model=None):
    from langchain_core.messages import HumanMessage
    from agent.langgraph_agent import (
        DIET_CONSTRAINTS_SLOT,
        SmartRestaurantGraph,
        restaurant_agent,
    )

    # 指定模型时另建实例：各工具模块导入时会 load_dotenv(override=True)，
    # 单改环境变量会被 .env 覆盖回去，导致 A/B 实际仍在跑默认模型。
    agent = SmartRestaurantGraph(model_name=agent_model) if agent_model else restaurant_agent

    judge_llm = _build_judge_llm(judge_model) if judge else None
    details = []
    run_id = time.strftime("%Y%m%d%H%M%S")
    for case in cases:
        started = time.time()
        turns = case.get("turns") or [case["query"]]
        # 每次评测使用独立 thread，避免上一轮的历史被 Redis 记忆带进来
        thread_id = f"eval-{case['id']}-{run_id}"
        conversation = []
        turn_records = []
        # 多轮用例把历史消息一并传入（与 run/astream_run 的真实链路一致），
        # 辣度/预算/素食约束则依赖 Redis 会话槽位跨轮传递。
        for turn in turns:
            previous_count = len(conversation)
            state = await agent.app.ainvoke(
                {
                    "messages": conversation + [HumanMessage(content=turn)],
                    "user_info": {"id": user_id, "username": "eval", "nickname": "评测"},
                },
                # LangGraph 的工具节点需要继承 config，缺失时会报 required config key
                config={"configurable": {"thread_id": thread_id}},
            )
            conversation = state.get("messages", []) or conversation
            turn_messages = conversation[previous_count:]
            turn_records.append({
                "turn": turn,
                "tools": _tool_names(turn_messages),
                # 只保留校验需要的结构化字段，避免评测 JSON 被正文撑大
                "items": [
                    {
                        "dish_name": item.get("dish_name"),
                        "price": item.get("price"),
                        "spice_level": item.get("spice_level"),
                        "is_vegetarian": item.get("is_vegetarian"),
                    }
                    for item in _search_items(turn_messages)
                ],
            })

        messages = conversation
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

        # 会话约束：既校验 Redis 槽位，也校验指定轮次检索结果是否真的按约束过滤
        expected_constraint = case.get("expected_constraint")
        constraint_ok = None
        constraint_effect_ok = None
        constraint_slot = None
        if expected_constraint is not None:
            constraint_slot = agent.memory_store.get_slot(
                thread_id, DIET_CONSTRAINTS_SLOT, user_id=user_id
            ) or {}
            constraint_ok = constraint_slot == expected_constraint
            turn_no = case.get("constraint_turn") or len(turns)
            turn_items = (
                turn_records[turn_no - 1]["items"]
                if 0 < turn_no <= len(turn_records) else []
            )
            effect_rules = {k: v for k, v in expected_constraint.items() if v is not None}
            # 撤销类用例（expected_constraint 为空）只需槽位为空，没有可校验的过滤效果
            constraint_effect_ok = (
                _matches_constraint_rules(turn_items, effect_rules) if effect_rules else True
            )

        # 菜名存在性：所有检索结果都必须在菜单语料里，防止推荐下架/幽灵菜
        items = _search_items(messages)
        dish_names = {item.get("dish_name") for item in items if item.get("dish_name")}
        ghost_dishes = sorted(dish_names - _menu_names()) if dish_names else []
        dish_validity_ok = (not ghost_dishes) if dish_names else None

        refusal_ok = None
        judge_ok = None
        judge_reason = ""
        if case.get("expect_refusal"):
            refusal_ok = any(word in answer for word in REFUSAL_KEYWORDS)
            if judge_llm is not None:
                judge_ok, judge_reason = await _judge_refusal(judge_llm, " ".join(turns), answer)

        answer_ok = None
        answer_reason = ""
        if case.get("expected_answer") and judge_llm is not None:
            answer_ok, answer_reason = await _judge_answer(
                judge_llm, " ".join(turns), answer, case["expected_answer"]
            )

        details.append({
            "id": case["id"],
            "turns": turns,
            "expected_tools": expected_tools,
            "actual_tools": actual_tools,
            "tool_match": actual_tools == expected_tools,
            "expected_answerable": expected_answerable,
            "answerable_flags": answerable_flags,
            "answerable_ok": answerable_ok,
            "expected_constraint": expected_constraint,
            "constraint_slot": constraint_slot,
            "constraint_ok": constraint_ok,
            "constraint_effect_ok": constraint_effect_ok,
            "dish_validity_ok": dish_validity_ok,
            "ghost_dishes": ghost_dishes,
            "turn_records": turn_records,
            "refusal_ok": refusal_ok,
            "refusal_judge": judge_ok,
            "refusal_judge_reason": judge_reason,
            "answer_ok": answer_ok,
            "answer_judge_reason": answer_reason,
            "answer": answer,
            "tokens": _usage_totals(messages),
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

    answer_items = [item for item in details if item["answer_ok"] is not None]
    answer_pass = sum(item["answer_ok"] for item in answer_items)

    constraint_items = [item for item in details if item["constraint_ok"] is not None]
    constraint_pass = sum(item["constraint_ok"] for item in constraint_items)
    effect_items = [item for item in details if item["constraint_effect_ok"] is not None]
    effect_pass = sum(item["constraint_effect_ok"] for item in effect_items)
    dish_items = [item for item in details if item["dish_validity_ok"] is not None]
    dish_pass = sum(item["dish_validity_ok"] for item in dish_items)

    latencies = [item["latency_ms"] for item in details]
    tokens = [item["tokens"] for item in details]
    token_calls = [item for item in tokens if item["total_tokens"]]
    tokens_total = sum(item["total_tokens"] for item in tokens)

    summary = {
        "case_count": total,
        "tool_accuracy": round(tool_pass / total, 4) if total else 0.0,
        "answerable_accuracy": (
            round(answerable_pass / len(answerable_items), 4) if answerable_items else None
        ),
        "answer_accuracy": (
            round(answer_pass / len(answer_items), 4) if answer_items else None
        ),
        "constraint_accuracy": (
            round(constraint_pass / len(constraint_items), 4) if constraint_items else None
        ),
        "constraint_effect_accuracy": (
            round(effect_pass / len(effect_items), 4) if effect_items else None
        ),
        "dish_validity_accuracy": (
            round(dish_pass / len(dish_items), 4) if dish_items else None
        ),
        "refusal_accuracy": (
            round(refusal_pass / len(refusal_items), 4) if refusal_items else None
        ),
        "refusal_judge_accuracy": (
            round(judge_pass / len(judge_items), 4) if judge_items else None
        ),
        "latency_p50_ms": round(_percentile(latencies, 0.50), 1) if latencies else 0.0,
        "latency_p95_ms": round(_percentile(latencies, 0.95), 1) if latencies else 0.0,
        "avg_latency_ms": round(sum(latencies) / total, 1) if total else 0.0,
        "tokens_total": tokens_total,
        "tokens_input_avg": (
            round(sum(item["input_tokens"] for item in token_calls) / len(token_calls), 1)
            if token_calls else None
        ),
        "tokens_output_avg": (
            round(sum(item["output_tokens"] for item in token_calls) / len(token_calls), 1)
            if token_calls else None
        ),
        "tokens_total_avg": (
            round(sum(item["total_tokens"] for item in token_calls) / len(token_calls), 1)
            if token_calls else None
        ),
        "token_samples": len(token_calls),
    }
    return summary, details


def main():
    parser = argparse.ArgumentParser(description="AiMenu Agent 端到端评测")
    parser.add_argument("--cases", default=CASES_PATH, help="用例 JSON 路径")
    parser.add_argument("--user-id", type=int, default=1, help="评测使用的用户 ID")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条，0 表示全部")
    parser.add_argument("--ids", default="", help="只跑指定用例，逗号分隔（如 refuse-privilege,refuse-punctuation）")
    parser.add_argument("--min-tool-accuracy", type=float, default=0.0,
                        help="工具选择准确率低于该值时以非零码退出，供 CI 使用")
    parser.add_argument("--no-judge", action="store_true",
                        help="关闭 LLM 评审，只用关键词判定拒答话术")
    parser.add_argument("--model", default="", help="覆盖 DASHSCOPE_MODEL_NAME，用于模型 A/B 对比")
    parser.add_argument("--judge-model", default="",
                        help="评审模型，默认固定为 .env 里的模型，保证对比时裁判不变")
    parser.add_argument("--label", default="", help="写入 JSON 的配置标签（如 qwen-plus）")
    parser.add_argument("--json", dest="json_path", help="将结果写入 JSON")
    args = parser.parse_args()

    if not os.getenv("DASHSCOPE_API_KEY"):
        print("[评测中止] 未配置 DASHSCOPE_API_KEY")
        raise SystemExit(2)

    # 注意：不能只改 DASHSCOPE_MODEL_NAME —— 工具模块导入时的 load_dotenv(override=True)
    # 会把它覆盖回 .env 的值；这里改为把模型显式传给评测实例。
    agent_model = args.model or BASE_MODEL
    judge_model = args.judge_model or BASE_MODEL
    print(f"[配置] 代理模型={agent_model} 评审模型={judge_model if not args.no_judge else '未启用'} "
          f"rerank_provider={os.getenv('RERANK_PROVIDER')} use_rerank={os.getenv('USE_RERANK', 'true')}")

    cases = load_cases(args.cases)
    if args.ids:
        # 定向复跑：只验证本次改动涉及的用例，避免每次都跑满 61 条
        wanted = [item.strip() for item in args.ids.split(",") if item.strip()]
        by_id = {case["id"]: case for case in cases}
        missing = [case_id for case_id in wanted if case_id not in by_id]
        if missing:
            print(f"[评测中止] 用例不存在: {', '.join(missing)}")
            raise SystemExit(2)
        cases = [by_id[case_id] for case_id in wanted]
    if args.limit:
        cases = cases[:args.limit]

    try:
        summary, details = asyncio.run(
            evaluate(cases, args.user_id, judge=not args.no_judge,
                     judge_model=judge_model, agent_model=args.model or None)
        )
    except Exception as exc:  # 依赖 MySQL / Redis / 模型，失败时明确中止
        print(f"[评测中止] {exc}")
        raise SystemExit(2)

    print("=" * 68)
    for item in details:
        flags = []
        flags.append("工具✓" if item["tool_match"] else f"工具✗({item['actual_tools']})")
        if item["answerable_ok"] is not None:
            flags.append("拒答✓" if item["answerable_ok"] else "拒答✗")
        if item["constraint_ok"] is not None:
            flags.append("约束✓" if item["constraint_ok"] else "约束✗")
        if item["constraint_effect_ok"] is not None:
            flags.append("效果✓" if item["constraint_effect_ok"] else "效果✗")
        if item["dish_validity_ok"] is not None:
            flags.append("菜名✓" if item["dish_validity_ok"] else f"菜名✗({item['ghost_dishes']})")
        if item["refusal_ok"] is not None:
            flags.append("话术✓" if item["refusal_ok"] else "话术✗")
        if item["refusal_judge"] is not None:
            flags.append("评审✓" if item["refusal_judge"] else "评审✗")
        print(f"[{item['id']:<20}] {' '.join(flags)} | {item['latency_ms']}ms | {item['answer'][:40]}")
    print("-" * 68)
    print(
        f"用例 {summary['case_count']} | 工具准确率 {summary['tool_accuracy']:.4f} | "
        f"答复正确率 {summary['answer_accuracy']} | "
        f"可回答判定 {summary['answerable_accuracy']} | "
        f"会话约束 {summary['constraint_accuracy']} | "
        f"约束效果 {summary['constraint_effect_accuracy']} | "
        f"菜名有效 {summary['dish_validity_accuracy']} | "
        f"拒答话术(关键词) {summary['refusal_accuracy']} | "
        f"拒答话术(LLM 评审) {summary['refusal_judge_accuracy']}"
    )
    print(
        f"延迟 P50 {summary['latency_p50_ms']}ms / P95 {summary['latency_p95_ms']}ms / "
        f"均值 {summary['avg_latency_ms']}ms | "
        f"token 均值 输入 {summary['tokens_input_avg']} + 输出 {summary['tokens_output_avg']} "
        f"= {summary['tokens_total_avg']}（样本 {summary['token_samples']}），"
        f"合计 {summary['tokens_total']}"
    )
    print("=" * 68)

    if args.json_path:
        meta = {
            "label": args.label or agent_model,
            "agent_model": agent_model,
            "judge_model": None if args.no_judge else judge_model,
            "rerank_provider": os.getenv("RERANK_PROVIDER"),
            "use_rerank": os.getenv("USE_RERANK", "true").lower() == "true",
            "cases_path": os.path.relpath(args.cases, PROJECT_ROOT),
            "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(args.json_path, "w", encoding="utf-8") as fp:
            json.dump({"meta": meta, "summary": summary, "details": details}, fp,
                      ensure_ascii=False, indent=2)
        print(f"评测明细已写入: {args.json_path}")

    if summary["tool_accuracy"] < args.min_tool_accuracy:
        print(
            f"[未达标] 工具准确率 {summary['tool_accuracy']:.4f} "
            f"< 阈值 {args.min_tool_accuracy:.4f}"
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
