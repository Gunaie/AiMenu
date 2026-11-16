"""Held-out 阈值标定与验证。

原做法是在全部 150 条标注 + 18 条域外问题上选出 RETRIEVAL_MIN_SCORE，再在同一批
数据上报告「0 误拒、100% 拒答」，属于在测试集上调参。本脚本把数据按固定种子
切分为 dev / test：

1. 只在 dev 上网格搜索阈值（正例可回答率 + 域外拒答率最大）；
2. 再到 test 上报告误拒率与拒答率；
3. 同时输出「全量数据」的结果，用于对比原来的乐观数值。

用法：
    python scripts/eval_threshold.py --json data/eval_threshold.json
    python scripts/eval_threshold.py --from-eval data/eval_rag_local.json   # 复用已有评测分数

需要 DashScope Embedding + Rerank 可用，且本地 chroma_db 已完成初始化。

提醒：搜索阈值时只使用 dev 切分，test 仅用于报告，避免在测试集上调参。
"""
import argparse
import json
import os
import random
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(override=True)

DATA_DIR = os.path.join(PROJECT_ROOT, "data")
LABELS_PATH = os.path.join(DATA_DIR, "eval_labels.json")
NEGATIVES_PATH = os.path.join(DATA_DIR, "eval_negative.json")


def _load_json(path, description):
    if not os.path.exists(path):
        raise FileNotFoundError(f"{description}不存在: {path}")
    with open(path, "r", encoding="utf-8") as fp:
        return json.load(fp)


def _split(items, ratio, seed):
    """按固定种子切分，保证脚本可复现"""
    shuffled = list(items)
    random.Random(seed).shuffle(shuffled)
    cut = max(1, int(len(shuffled) * ratio))
    return shuffled[:cut], shuffled[cut:]


def _grid(min_score, max_score, step):
    count = int(round((max_score - min_score) / step))
    return [round(min_score + i * step, 4) for i in range(count + 1)]


def _best_threshold(pos_scores, neg_scores, grid):
    """在 dev 上选阈值：先最大化正例接受率 + 域外拒答率，再最小化误拒数"""
    best_key = None
    best_threshold = grid[0]
    for threshold in grid:
        accept = sum(score >= threshold for score in pos_scores) / len(pos_scores)
        refuse = sum(score < threshold for score in neg_scores) / len(neg_scores)
        misreject = sum(score < threshold for score in pos_scores)
        key = (round(accept + refuse, 6), -misreject, threshold)
        if best_key is None or key > best_key:
            best_key = key
            best_threshold = threshold
    return best_threshold


def _report(name, pos_scores, neg_scores, threshold):
    misrejects = sum(score < threshold for score in pos_scores)
    false_accepts = sum(score >= threshold for score in neg_scores)
    return {
        "split": name,
        "threshold": threshold,
        "positive_total": len(pos_scores),
        "positive_accept_rate": round(1 - misrejects / len(pos_scores), 4),
        "positive_misreject_count": misrejects,
        "negative_total": len(neg_scores),
        "negative_refusal_rate": round(1 - false_accepts / len(neg_scores), 4),
        "negative_false_accept_count": false_accepts,
    }


def _collect_scores(labels, negatives, top_k):
    """逐条取 Rerank 最高分；Rerank 不可用时直接中止，避免输出失真结论"""
    from tools.chroma_tool import chroma_db, search_menu_items_with_ids

    if not chroma_db.use_rerank:
        raise RuntimeError("USE_RERANK 未启用，无法基于 Rerank 分数标定阈值")

    def _score(query):
        result = search_menu_items_with_ids(query, top_k=top_k)
        if result.get("answerable") is None:
            raise RuntimeError("Rerank 不可用（额度/鉴权失败），已中止标定")
        return float(result.get("top_score") or 0.0)

    pos = [(item["query"], _score(item["query"])) for item in labels]
    neg = [(item["query"], _score(item["query"])) for item in negatives]
    return pos, neg


def main():
    parser = argparse.ArgumentParser(description="AiMenu 拒答阈值 held-out 标定")
    parser.add_argument("--labels", default=LABELS_PATH, help="标注集 JSON 路径")
    parser.add_argument("--negatives", default=NEGATIVES_PATH, help="域外问题集 JSON 路径")
    parser.add_argument("--top-k", type=int, default=1, help="取 Top-K 中的最高分，默认 1")
    parser.add_argument("--dev-ratio", type=float, default=0.5, help="dev 集占比，默认 0.5")
    parser.add_argument("--seed", type=int, default=42, help="切分随机种子")
    parser.add_argument("--min-score", type=float, default=0.0, help="网格下界")
    parser.add_argument("--max-score", type=float, default=0.90, help="网格上界")
    parser.add_argument("--step", type=float, default=0.005, help="网格步长")
    parser.add_argument("--json", dest="json_path", help="将结果写入 JSON")
    parser.add_argument("--from-eval", dest="from_eval",
                        help="复用 eval_rag.py 输出 JSON 中的 Rerank 最高分，跳过重新检索")
    args = parser.parse_args()

    if args.from_eval:
        data = _load_json(args.from_eval, "评测结果")
        pos = [(item["query"], float(item["hybrid_score"]))
               for item in data.get("details", []) if item.get("hybrid_score") is not None]
        neg = [(query, float(score))
               for query, score in (data.get("refusal", {}).get("negative_scores") or {}).items()
               if score is not None]
        if not pos or not neg:
            print("\n[标定中止] 评测结果缺少 hybrid_score 或 negative_scores")
            raise SystemExit(2)
        print(f"复用 {args.from_eval} 的分数：正例 {len(pos)} 条，域外 {len(neg)} 条")
    else:
        labels = _load_json(args.labels, "评测标注")
        negatives = _load_json(args.negatives, "域外问题集")
        try:
            pos, neg = _collect_scores(labels, negatives, args.top_k)
        except (RuntimeError, FileNotFoundError, ValueError) as exc:
            print(f"\n[标定中止] {exc}")
            raise SystemExit(2)

    dev_pos, test_pos = _split([score for _, score in pos], args.dev_ratio, args.seed)
    dev_neg, test_neg = _split([score for _, score in neg], args.dev_ratio, args.seed)

    grid = _grid(args.min_score, args.max_score, args.step)
    threshold = _best_threshold(dev_pos, dev_neg, grid)

    dev_report = _report("dev", dev_pos, dev_neg, threshold)
    test_report = _report("test(held-out)", test_pos, test_neg, threshold)
    all_report = _report("all", [score for _, score in pos], [score for _, score in neg], threshold)
    configured = _report(
        "all@configured",
        [score for _, score in pos],
        [score for _, score in neg],
        float(os.getenv("RETRIEVAL_MIN_SCORE", "0.64")),
    )

    result = {
        "seed": args.seed,
        "dev_ratio": args.dev_ratio,
        "grid": {"min": args.min_score, "max": args.max_score, "step": args.step},
        "selected_threshold": threshold,
        "dev": dev_report,
        "test": test_report,
        "all": all_report,
        "configured_threshold": float(os.getenv("RETRIEVAL_MIN_SCORE", "0.64")),
        "configured_on_all": configured,
        "positive_scores": {query: score for query, score in pos},
        "negative_scores": {query: score for query, score in neg},
    }

    print("=" * 68)
    print(f"dev 占比 {args.dev_ratio} | 种子 {args.seed} | 选中阈值 {threshold}")
    for report in (dev_report, test_report, all_report, configured):
        print(
            f"{report['split']:<18} 阈值 {report['threshold']:.3f} | "
            f"正例可回答 {report['positive_accept_rate']:.4f} "
            f"(误拒 {report['positive_misreject_count']}/{report['positive_total']}) | "
            f"域外拒答 {report['negative_refusal_rate']:.4f} "
            f"(误收 {report['negative_false_accept_count']}/{report['negative_total']})"
        )
    print("=" * 68)

    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as fp:
            json.dump(result, fp, ensure_ascii=False, indent=2)
        print(f"标定明细已写入: {args.json_path}")


if __name__ == "__main__":
    main()
