"""导出检索、拒答与 Agent 评测中的失败案例，生成可公开的结构化案例集。

用法示例：
    python scripts/export_failure_cases.py \
        --rag data/eval_rag_v3.json \
        --agent data/eval_agent_full_qwen35.json \
        --out data/eval_failure_cases.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "data" / "eval_failure_cases.json"

AGENT_CHECKS = (
    ("tool_match", "工具选择错误"),
    ("constraint_ok", "约束槽位错误"),
    ("constraint_effect_ok", "约束未生效"),
    ("dish_validity_ok", "推荐了不存在的菜品"),
    ("refusal_ok", "应拒答未拒答"),
    ("answer_ok", "答复判定不通过"),
)


def load_json(path: Path, required: bool) -> dict[str, Any] | None:
    if not path.exists():
        if required:
            raise FileNotFoundError(f"找不到评测结果文件：{path}")
        return None
    with path.open(encoding="utf-8") as fp:
        return json.load(fp)


def relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def build_retrieval(payload: dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {"available": False}

    top_k = int(payload.get("top_k") or 3)
    misses: list[dict[str, Any]] = []
    for item in payload.get("details", []):
        expected = item.get("expected") or []
        retrieved = item.get("hybrid") or []
        if not expected or any(name in retrieved for name in expected):
            continue
        misses.append(
            {
                "query": item.get("query"),
                "type": item.get("type"),
                "expected": expected,
                "retrieved": retrieved[:top_k],
                "top_score": item.get("hybrid_score"),
            }
        )

    by_type = payload.get("by_type") or {}
    return {
        "available": True,
        "config": {
            "top_k": top_k,
            "corpus_size": payload.get("corpus_size"),
            "query_count": payload.get("query_count"),
            "rerank_enabled": payload.get("rerank_enabled"),
            "rerank_provider": payload.get("rerank_provider"),
            "rerank_model": payload.get("rerank_model"),
        },
        "metrics": payload.get("hybrid") or {},
        "by_type": by_type,
        "miss_count": len(misses),
        "miss_cases": misses,
    }


def build_refusal(payload: dict[str, Any] | None) -> dict[str, Any]:
    refusal = (payload or {}).get("refusal") or {}
    if not refusal.get("enabled"):
        return {"available": False}

    threshold = refusal.get("threshold")
    false_accepts = [
        {"query": query, "score": score}
        for query, score in (refusal.get("negative_scores") or {}).items()
        if threshold is not None and score >= threshold
    ]
    return {
        "available": True,
        "threshold": threshold,
        "summary": {
            "positive_total": refusal.get("positive_total"),
            "positive_accept_rate": refusal.get("positive_accept_rate"),
            "positive_misreject_count": refusal.get("positive_misreject_count"),
            "negative_total": refusal.get("negative_total"),
            "negative_refusal_rate": refusal.get("negative_refusal_rate"),
            "negative_false_accepts": refusal.get("negative_false_accepts"),
            "negative_by_type": refusal.get("negative_by_type"),
        },
        "misrejects": refusal.get("misrejects") or [],
        "false_accepts": false_accepts,
    }


def build_agent(payload: dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {"available": False}

    failures: list[dict[str, Any]] = []
    for item in payload.get("details", []):
        issues = [label for key, label in AGENT_CHECKS if item.get(key) is False]
        if not issues:
            continue
        failures.append(
            {
                "id": item.get("id"),
                "issues": issues,
                "turns": item.get("turns"),
                "expected_tools": item.get("expected_tools"),
                "actual_tools": item.get("actual_tools"),
                "expected_constraint": item.get("expected_constraint"),
                "constraint_slot": item.get("constraint_slot"),
                "ghost_dishes": item.get("ghost_dishes"),
                "answer": item.get("answer"),
            }
        )

    return {
        "available": True,
        "config": payload.get("meta") or {},
        "metrics": payload.get("summary") or {},
        "failure_count": len(failures),
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="导出检索/拒答/Agent 失败案例集")
    parser.add_argument("--rag", default=str(ROOT / "data" / "eval_rag_v3.json"),
                        help="eval_rag.py 输出的 JSON")
    parser.add_argument("--agent", default=str(ROOT / "data" / "eval_agent_full_qwen35.json"),
                        help="eval_agent.py 输出的 JSON")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出 JSON 路径")
    args = parser.parse_args()

    rag_path = Path(args.rag)
    agent_path = Path(args.agent)
    rag = load_json(rag_path, required=False)
    agent = load_json(agent_path, required=False)
    if rag is None and agent is None:
        raise SystemExit("至少需要提供一个存在的评测结果文件")

    report = {
        "meta": {
            "description": "AiMenu 检索与 Agent 护栏失败案例集，由 scripts/export_failure_cases.py 生成",
            "sources": [relative(path) for path in (rag_path, agent_path) if path.exists()],
        },
        "retrieval": build_retrieval(rag),
        "refusal": build_refusal(rag),
        "agent": build_agent(agent),
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2)

    counts = {
        "检索未命中": report["retrieval"].get("miss_count", 0),
        "正例误拒": len(report["refusal"].get("misrejects", [])),
        "域外误收": len(report["refusal"].get("false_accepts", [])),
        "Agent 失败": report["agent"].get("failure_count", 0),
    }
    print(f"已写入 {relative(out_path)}：" + "，".join(f"{k} {v} 条" for k, v in counts.items()))


if __name__ == "__main__":
    main()
