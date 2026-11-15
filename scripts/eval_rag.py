"""RAG 检索评测脚本。

在标注问答集（data/eval_labels.json）上对比三种检索策略的 Top-K 命中率与 MRR：
1. 纯向量检索（ChromaDB + text-embedding-v4）
2. 向量 + BM25 融合（不重排）
3. 向量 + BM25 + DashScope Rerank 混合检索

用法：
    python scripts/eval_rag.py
    python scripts/eval_rag.py --top-k 3 --json eval_result.json

说明：脚本会真实调用 DashScope 的 Embedding / Rerank 接口，需要 .env 中配置
DASHSCOPE_API_KEY；同时要求本地 chroma_db 已完成初始化（python setup_db.py）。
"""
import argparse
import json
import os
import re
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

DATA_DIR = os.path.join(PROJECT_ROOT, "data")
MENU_DATA_PATH = os.path.join(DATA_DIR, "menu_items.json")
LABELS_PATH = os.path.join(DATA_DIR, "eval_labels.json")
NEGATIVES_PATH = os.path.join(DATA_DIR, "eval_negative.json")


def _load_json(path, description):
    if not os.path.exists(path):
        raise FileNotFoundError(f"{description}不存在: {path}")
    with open(path, "r", encoding="utf-8") as fp:
        return json.load(fp)


def load_menu_dishes(path=MENU_DATA_PATH):
    """加载菜品数据，返回 {菜名: 菜品字典}"""
    return {item["dish_name"]: item for item in _load_json(path, "菜品数据")}


def load_labels(path=LABELS_PATH):
    """加载标注集，并校验 expected 中的菜名都存在于菜单数据中"""
    labels = _load_json(path, "评测标注")
    dishes = load_menu_dishes()
    unknown = sorted({
        name
        for item in labels
        for name in item["expected"]
        if name not in dishes
    })
    if unknown:
        raise ValueError(f"标注集中存在菜单之外的菜名: {unknown[:5]}")
    return labels


def _dish_names(contents):
    """从检索结果文本中提取菜名，保持原始排序"""
    names = []
    for content in contents:
        match = re.search(r"菜品名称:([^|]+)", content)
        if match:
            names.append(match.group(1).strip())
    return names


def _vector_only_search(query, top_k):
    """纯向量检索基线：仅使用 embedding + ChromaDB"""
    from tools.chroma_tool import chroma_db

    if not chroma_db.collection and not chroma_db.initialize_connection():
        raise RuntimeError("ChromaDB 初始化失败，请先执行 python setup_db.py")
    vector = chroma_db._embedding_content(query)
    if not vector:
        raise RuntimeError(
            "Embedding 接口不可用，评测结果无效。请检查 DASHSCOPE_API_KEY 与账户余额后重试。"
        )
    result = chroma_db.collection.query(
        query_embeddings=[vector],
        n_results=top_k,
        include=["metadatas"],
    )
    return [metadata["content"] for metadata in result["metadatas"][0]]


def _hybrid_search(query, top_k, rerank_enabled, rewrite_enabled=None):
    """混合检索；rerank_enabled=False 时只做向量 + BM25 融合

    rewrite_enabled=None 时沿用当前配置，传 False 用于消融实验对比改写收益。
    """
    from tools.chroma_tool import chroma_db, search_menu_items_with_ids

    original_rerank = chroma_db.use_rerank
    original_rewrite = chroma_db.use_rewrite
    chroma_db.use_rerank = rerank_enabled
    if rewrite_enabled is not None:
        chroma_db.use_rewrite = rewrite_enabled
    try:
        return search_menu_items_with_ids(query, top_k=top_k)
    finally:
        chroma_db.use_rerank = original_rerank
        chroma_db.use_rewrite = original_rewrite


def _score(ranked_lists, labels, top_k):
    """计算 Recall@K 与 MRR"""
    hits = 0
    reciprocal_rank = 0.0
    for ranked, expected in zip(ranked_lists, labels):
        rank = next((i + 1 for i, name in enumerate(ranked[:top_k]) if name in expected), None)
        if rank:
            hits += 1
            reciprocal_rank += 1 / rank
    total = len(labels)
    return {
        "recall_at_k": round(hits / total, 4) if total else 0.0,
        "mrr": round(reciprocal_rank / total, 4) if total else 0.0,
    }


def _refusal_summary(positive_flags, negative_results, threshold):
    """汇总无答案拒答指标

    positive_flags: 标注集每条 query 的 answerable 判定（None 表示无法判定）
    negative_results: [(query, answerable), ...] 域外问题
    """
    judged = [flag for flag in positive_flags if flag is not None]
    positives_accepted = sum(1 for flag in judged if flag)
    negatives_refused = sum(1 for _, flag in negative_results if flag is False)
    false_accepts = [query for query, flag in negative_results if flag is not False]
    return {
        "enabled": True,
        "threshold": threshold,
        "positive_total": len(judged),
        "positive_accept_rate": round(positives_accepted / len(judged), 4) if judged else 0.0,
        "positive_misreject_count": len(judged) - positives_accepted,
        "negative_total": len(negative_results),
        "negative_refusal_rate": round(negatives_refused / len(negative_results), 4) if negative_results else 0.0,
        "negative_false_accepts": false_accepts,
    }


def _evaluate_refusal(labels, top_k, negatives_path):
    """在标注集（应回答）与域外问题（应拒答）上评测拒答能力"""
    from tools.chroma_tool import chroma_db

    if not (chroma_db.use_rerank and chroma_db._rerank_available):
        return {"enabled": False, "reason": "Rerank 不可用，无法基于相关度分数判定拒答"}

    negatives = _load_json(negatives_path, "域外问题集")
    positive_flags = []
    misrejects = []
    for item in labels:
        flag = _hybrid_search(item["query"], top_k, rerank_enabled=True).get("answerable")
        positive_flags.append(flag)
        if flag is False:
            misrejects.append(item["query"])

    negative_results = []
    for item in negatives:
        flag = _hybrid_search(item["query"], top_k, rerank_enabled=True).get("answerable")
        negative_results.append((item["query"], flag))

    summary = _refusal_summary(positive_flags, negative_results, chroma_db.min_relevance_score)
    summary["misrejects"] = misrejects
    return summary


def evaluate(top_k=3, verbose=True, labels_path=LABELS_PATH,
             negatives_path=NEGATIVES_PATH, with_refusal=True, with_ablation=True):
    """运行完整评测，返回检索指标、拒答指标与逐条明细；with_ablation=False 时跳过改写消融"""
    from tools.chroma_tool import chroma_db

    labels = load_labels(labels_path)
    expectations = [item["expected"] for item in labels]
    ranked = {"vector_only": [], "fusion_only": [], "hybrid": [], "hybrid_no_rewrite": []}
    details = []

    for item in labels:
        query = item["query"]
        vector_names = _dish_names(_vector_only_search(query, top_k))
        fusion_names = _dish_names(_hybrid_search(query, top_k, rerank_enabled=False)["contents"])
        no_rewrite_names = None
        if with_ablation:
            no_rewrite_names = _dish_names(
                _hybrid_search(query, top_k, rerank_enabled=True, rewrite_enabled=False)["contents"]
            )
        hybrid_names = _dish_names(_hybrid_search(query, top_k, rerank_enabled=True)["contents"])

        ranked["vector_only"].append(vector_names)
        ranked["fusion_only"].append(fusion_names)
        ranked["hybrid"].append(hybrid_names)
        detail = {
            "query": query,
            "expected": item["expected"],
            "vector_only": vector_names,
            "fusion_only": fusion_names,
            "hybrid": hybrid_names,
        }
        if with_ablation:
            ranked["hybrid_no_rewrite"].append(no_rewrite_names)
            detail["hybrid_no_rewrite"] = no_rewrite_names
        details.append(detail)

        if verbose:
            print(f"[{query}] 期望={item['expected']} 向量={vector_names} "
                  f"融合={fusion_names} 混合={hybrid_names}")

    corpus_size = None
    if chroma_db.collection:
        corpus_size = chroma_db.collection.count()

    result = {
        "top_k": top_k,
        "query_count": len(labels),
        "corpus_size": corpus_size,
        "rerank_enabled": bool(chroma_db.use_rerank and chroma_db._rerank_available),
        "rerank_model": os.getenv("RERANK_MODEL", "qwen3-vl-rerank"),
        "vector_only": _score(ranked["vector_only"], expectations, top_k),
        "fusion_only": _score(ranked["fusion_only"], expectations, top_k),
        "hybrid": _score(ranked["hybrid"], expectations, top_k),
        "details": details,
    }
    if with_ablation:
        result["hybrid_no_rewrite"] = _score(ranked["hybrid_no_rewrite"], expectations, top_k)
    if with_refusal:
        result["refusal"] = _evaluate_refusal(labels, top_k, negatives_path)
    return result


def _compare_baseline(result, baseline, max_drop):
    """对比基线与当前结果

    返回 (回退明细列表, 是否因 Rerank 不可用而跳过混合检索对比)。
    基线基于 Rerank、但当前 Rerank 已降级时，混合检索指标不可比，直接跳过而不是误报回退。
    """
    baseline_rerank = bool(baseline.get("rerank_enabled"))
    current_rerank = bool(result.get("rerank_enabled"))
    skip_hybrid = baseline_rerank and not current_rerank

    pairs = [("vector_only", "纯向量"), ("fusion_only", "向量+BM25 融合")]
    if not skip_hybrid:
        pairs.append(("hybrid", "混合检索(+改写)"))

    regressions = []
    for key, label in pairs:
        current = result.get(key) or {}
        expected = baseline.get(key) or {}
        for metric in ("recall_at_k", "mrr"):
            cur_val = current.get(metric)
            base_val = expected.get(metric)
            if cur_val is None or base_val is None:
                continue
            if cur_val < base_val - max_drop:
                regressions.append((label, metric, base_val, cur_val))
    return regressions, skip_hybrid


def main():
    parser = argparse.ArgumentParser(description="AiMenu RAG 检索评测")
    parser.add_argument("--top-k", type=int, default=3, help="评测使用的 Top-K，默认 3")
    parser.add_argument("--labels", default=LABELS_PATH, help="标注集 JSON 路径")
    parser.add_argument("--negatives", default=NEGATIVES_PATH, help="域外问题集 JSON 路径")
    parser.add_argument("--no-refusal", action="store_true", help="跳过无答案拒答评测")
    parser.add_argument("--no-ablation", action="store_true", help="跳过改写消融，减少一半检索调用")
    parser.add_argument("--baseline", help="基线结果 JSON 路径，用于检测检索效果回退")
    parser.add_argument("--max-drop", type=float, default=0.02, help="允许的最大指标跌幅，默认 0.02")
    parser.add_argument("--json", dest="json_path", help="将评测结果写入指定 JSON 文件")
    parser.add_argument("--quiet", action="store_true", help="只输出汇总指标")
    args = parser.parse_args()

    try:
        result = evaluate(
            top_k=args.top_k,
            verbose=not args.quiet,
            labels_path=args.labels,
            negatives_path=args.negatives,
            with_refusal=not args.no_refusal,
            with_ablation=not args.no_ablation,
        )
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        print(f"\n[评测中止] {exc}")
        raise SystemExit(2)
    print("\n" + "=" * 68)
    print(f"样本数: {result['query_count']} | 语料数: {result['corpus_size']} | "
          f"Top-K: {result['top_k']} | Rerank: {'启用' if result['rerank_enabled'] else '未启用（降级）'}")
    for key, name in (("vector_only", "纯向量检索"), ("fusion_only", "向量+BM25 融合"),
                      ("hybrid_no_rewrite", "混合检索(无改写)"), ("hybrid", "混合检索(+改写)")):
        metrics = result.get(key)
        if not metrics:
            continue
        print(f"{name:<18}: Recall@{args.top_k} = {metrics['recall_at_k']:.4f}, "
              f"MRR = {metrics['mrr']:.4f}")

    refusal = result.get("refusal")
    if refusal and refusal.get("enabled"):
        print("-" * 68)
        print(f"无答案拒答（阈值 {refusal['threshold']}）: "
              f"正例可回答率 = {refusal['positive_accept_rate']:.4f} "
              f"(误拒 {refusal['positive_misreject_count']}/{refusal['positive_total']}), "
              f"域外拒答率 = {refusal['negative_refusal_rate']:.4f} "
              f"({refusal['negative_total'] - len(refusal['negative_false_accepts'])}"
              f"/{refusal['negative_total']})")
        if refusal["negative_false_accepts"]:
            print(f"未拒答的域外问题: {refusal['negative_false_accepts']}")
    elif refusal:
        print("-" * 68)
        print(f"无答案拒答: 未评测（{refusal.get('reason')}）")
    print("=" * 68)

    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as fp:
            json.dump(result, fp, ensure_ascii=False, indent=2)
        print(f"评测明细已写入: {args.json_path}")

    if args.baseline:
        with open(args.baseline, encoding="utf-8") as fp:
            baseline = json.load(fp)
        regressions, skip_hybrid = _compare_baseline(result, baseline, args.max_drop)
        print("-" * 68)
        if skip_hybrid:
            print("基线基于 Rerank，当前 Rerank 不可用（已降级为融合排序），跳过混合检索指标对比")
        if regressions:
            print(f"检索效果回退（允许跌幅 {args.max_drop}）：")
            for label, metric, base_val, cur_val in regressions:
                print(f"  {label} {metric}: {base_val:.4f} -> {cur_val:.4f}")
            raise SystemExit(3)
        print(f"检索效果与基线对比：无回退（允许跌幅 {args.max_drop}）")


if __name__ == "__main__":
    main()
