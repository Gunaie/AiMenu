"""对比评测结果与基线，检测指标回退，供 CI 与本地使用。

支持两类结果文件：
- 检索评测（scripts/eval_rag.py 输出）：hybrid.top1 / top3 / mrr、refusal 拒答率
- Agent 评测（scripts/eval_agent.py 输出）：summary 中的工具、约束、拒答等准确率

用法示例：
    python scripts/check_eval_regression.py \
        --result eval_result.json --baseline data/eval_baseline.json
    python scripts/check_eval_regression.py \
        --result agent_eval.json --baseline data/eval_agent_full_qwen35.json --max-drop 0.05
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

RAG_METRICS = (
    ("hybrid", "top1", "混合检索 Top-1"),
    ("hybrid", "top3", "混合检索 Top-3"),
    ("hybrid", "mrr", "混合检索 MRR"),
    ("refusal", "negative_refusal_rate", "域外拒答率"),
    ("refusal", "positive_accept_rate", "正例接受率"),
)

AGENT_METRICS = (
    ("summary", "tool_accuracy", "工具选择准确率"),
    ("summary", "constraint_accuracy", "约束槽位准确率"),
    ("summary", "constraint_effect_accuracy", "约束生效准确率"),
    ("summary", "dish_validity_accuracy", "菜名有效性准确率"),
    ("summary", "refusal_accuracy", "拒答准确率"),
    ("summary", "answer_accuracy", "答复正确率"),
)


def load(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"找不到文件：{path}")
    with path.open(encoding="utf-8") as fp:
        return json.load(fp)


def metric_specs(payload: dict[str, Any]) -> tuple[tuple[str, str, str], ...]:
    if "summary" in payload:
        return AGENT_METRICS
    return RAG_METRICS


def read_metric(payload: dict[str, Any], section: str, key: str) -> float | None:
    block = payload.get(section)
    if not isinstance(block, dict):
        return None
    value = block.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def main() -> None:
    parser = argparse.ArgumentParser(description="检测评测指标相对基线是否回退")
    parser.add_argument("--result", required=True, help="本次评测结果 JSON")
    parser.add_argument("--baseline", required=True, help="基线结果 JSON")
    parser.add_argument("--max-drop", type=float, default=0.02,
                        help="允许的最大跌幅，默认 0.02（Agent 小样本建议 0.05）")
    parser.add_argument("--json", dest="json_path", help="将对比结果写入 JSON")
    args = parser.parse_args()

    result = load(Path(args.result))
    baseline = load(Path(args.baseline))

    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    regressions: list[str] = []

    for section, key, label in metric_specs(result):
        current = read_metric(result, section, key)
        base = read_metric(baseline, section, key)
        if current is None or base is None:
            skipped.append(label)
            continue
        delta = round(current - base, 4)
        passed = delta >= -args.max_drop
        rows.append(
            {
                "metric": label,
                "baseline": base,
                "current": current,
                "delta": delta,
                "passed": passed,
            }
        )
        if not passed:
            regressions.append(f"{label} 从 {base:.4f} 降到 {current:.4f}（跌幅 {abs(delta):.4f}）")

    print(f"{'指标':<20}{'基线':>10}{'本次':>10}{'变化':>10}  结果")
    for row in rows:
        status = "OK" if row["passed"] else "REGRESSED"
        print(
            f"{row['metric']:<20}{row['baseline']:>10.4f}{row['current']:>10.4f}"
            f"{row['delta']:>+10.4f}  {status}"
        )
    if skipped:
        print("跳过（结果或基线缺失）：" + "、".join(skipped))

    if args.json_path:
        with Path(args.json_path).open("w", encoding="utf-8") as fp:
            json.dump(
                {
                    "max_drop": args.max_drop,
                    "rows": rows,
                    "skipped": skipped,
                    "regressions": regressions,
                },
                fp,
                ensure_ascii=False,
                indent=2,
            )

    if regressions:
        print("\n检测到指标回退：")
        for item in regressions:
            print(f"- {item}")
        sys.exit(1)

    print("\n全部指标未超过允许跌幅")


if __name__ == "__main__":
    main()
