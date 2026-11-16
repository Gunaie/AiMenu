"""RAG 检索评测脚本。

在标注问答集（data/eval_labels.json）上对比三种检索策略的 Top-K 命中率与 MRR：
1. 纯向量检索（ChromaDB + text-embedding-v3/v4，由 EMBEDDING_MODEL 指定）
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
import time
from math import comb

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

DATA_DIR = os.path.join(PROJECT_ROOT, "data")
MENU_DATA_PATH = os.path.join(DATA_DIR, "menu_items.json")
LABELS_PATH = os.path.join(DATA_DIR, "eval_labels.json")
NEGATIVES_PATH = os.path.join(DATA_DIR, "eval_negative.json")
EMBEDDING_CACHE_PATH = os.path.join(DATA_DIR, "eval_embedding_cache.npz")


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
    """计算 Top-1 / Top-3 / Recall@K 与 MRR

    labels 传入标注条目（含 expected 与 type），便于按类型分组复用同一套计算。
    """
    top1 = top3 = hits = 0
    reciprocal_rank = 0.0
    for ranked, item in zip(ranked_lists, labels):
        expected = item["expected"]
        rank = next((i + 1 for i, name in enumerate(ranked) if name in expected), None)
        if rank == 1:
            top1 += 1
        if rank and rank <= 3:
            top3 += 1
        if rank and rank <= top_k:
            hits += 1
            reciprocal_rank += 1 / rank
    total = len(labels)
    return {
        "top1": round(top1 / total, 4) if total else 0.0,
        "top3": round(top3 / total, 4) if total else 0.0,
        "recall_at_k": round(hits / total, 4) if total else 0.0,
        "mrr": round(reciprocal_rank / total, 4) if total else 0.0,
        "query_count": total,
    }


def _random_baseline(labels, corpus_size, top_k):
    """随机排序基线：从语料里随机取 K 道菜，期望命中率（按标准答案条数精确计算）"""
    if not corpus_size:
        return None
    top1 = top3 = 0.0
    for item in labels:
        relevant = min(len(item["expected"]), corpus_size)
        top1 += relevant / corpus_size
        k = min(3, corpus_size)
        if corpus_size - relevant < k:
            top3 += 1.0
        else:
            top3 += 1 - comb(corpus_size - relevant, k) / comb(corpus_size, k)
    total = len(labels)
    return {
        "corpus_size": corpus_size,
        "top1": round(top1 / total, 4) if total else 0.0,
        "top3": round(top3 / total, 4) if total else 0.0,
        "recall_at_k": round(top1 / total, 4) if total else 0.0,
    }


def _percentile(values, q):
    """线性插值求分位数"""
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def _latency_summary(samples):
    if not samples:
        return {"count": 0, "p50_ms": 0.0, "p95_ms": 0.0, "mean_ms": 0.0}
    return {
        "count": len(samples),
        "p50_ms": round(_percentile(samples, 0.5), 1),
        "p95_ms": round(_percentile(samples, 0.95), 1),
        "mean_ms": round(sum(samples) / len(samples), 1),
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


def _evaluate_refusal(labels, positive_flags, top_k, negatives_path):
    """在标注集（应回答）与域外问题（应拒答）上评测拒答能力

    positive_flags 复用主循环中混合检索的 answerable 判定，避免对同一批 query 重跑一遍。
    """
    from tools.chroma_tool import chroma_db

    if not (chroma_db.use_rerank and chroma_db._rerank_available):
        return {"enabled": False, "reason": "Rerank 不可用，无法基于相关度分数判定拒答"}

    negatives = _load_json(negatives_path, "域外问题集")
    misrejects = [item["query"] for item, flag in zip(labels, positive_flags) if flag is False]

    negative_results = []
    negative_scores = {}
    for item in negatives:
        payload = _hybrid_search(item["query"], top_k, rerank_enabled=True)
        negative_results.append((item["query"], payload.get("answerable")))
        negative_scores[item["query"]] = payload.get("top_score")

    summary = _refusal_summary(positive_flags, negative_results, chroma_db.min_relevance_score)
    summary["misrejects"] = misrejects
    negative_by_type = {}
    for item, (_, flag) in zip(negatives, negative_results):
        key = item.get("type") or "untyped"
        stats = negative_by_type.setdefault(key, {"total": 0, "refused": 0})
        stats["total"] += 1
        if flag is False:
            stats["refused"] += 1
    for stats in negative_by_type.values():
        stats["refusal_rate"] = round(stats["refused"] / stats["total"], 4)
    summary["negative_by_type"] = negative_by_type
    summary["negative_scores"] = negative_scores
    return summary


def evaluate(top_k=3, verbose=True, labels_path=LABELS_PATH,
             negatives_path=NEGATIVES_PATH, with_refusal=True, with_ablation=True):
    """运行完整评测，返回检索指标、拒答指标与逐条明细；with_ablation=False 时跳过改写消融"""
    from tools.chroma_tool import chroma_db

    labels = load_labels(labels_path)
    ranked = {"vector_only": [], "fusion_only": [], "hybrid": [], "hybrid_no_rewrite": []}
    timings = {"vector_only": [], "fusion_only": [], "hybrid": [], "hybrid_no_rewrite": []}
    details = []
    positive_flags = []

    def _timed(key, func):
        start = time.perf_counter()
        try:
            return func()
        finally:
            timings[key].append((time.perf_counter() - start) * 1000)

    for item in labels:
        query = item["query"]
        vector_names = _dish_names(_timed("vector_only", lambda: _vector_only_search(query, top_k)))
        fusion_names = _dish_names(
            _timed("fusion_only", lambda: _hybrid_search(query, top_k, rerank_enabled=False))["contents"]
        )
        no_rewrite_names = None
        if with_ablation:
            no_rewrite_names = _dish_names(
                _timed(
                    "hybrid_no_rewrite",
                    lambda: _hybrid_search(query, top_k, rerank_enabled=True, rewrite_enabled=False),
                )["contents"]
            )
        hybrid_payload = _timed("hybrid", lambda: _hybrid_search(query, top_k, rerank_enabled=True))
        hybrid_names = _dish_names(hybrid_payload["contents"])
        positive_flags.append(hybrid_payload.get("answerable"))

        ranked["vector_only"].append(vector_names)
        ranked["fusion_only"].append(fusion_names)
        ranked["hybrid"].append(hybrid_names)
        detail = {
            "query": query,
            "type": item.get("type"),
            "expected": item["expected"],
            "vector_only": vector_names,
            "fusion_only": fusion_names,
            "hybrid": hybrid_names,
            "hybrid_score": hybrid_payload.get("top_score"),
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

    type_groups = {}
    for index, item in enumerate(labels):
        type_groups.setdefault(item.get("type") or "untyped", []).append(index)
    by_type = {}
    for type_name, indexes in sorted(type_groups.items()):
        by_type[type_name] = _score(
            [ranked["hybrid"][i] for i in indexes],
            [labels[i] for i in indexes],
            top_k,
        )

    result = {
        "top_k": top_k,
        "query_count": len(labels),
        "corpus_size": corpus_size,
        "rerank_enabled": bool(chroma_db.use_rerank and chroma_db._rerank_available),
        "rerank_model": (
            os.getenv("LOCAL_RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
            if os.getenv("RERANK_PROVIDER", "dashscope").strip().lower() == "local"
            else os.getenv("RERANK_MODEL", "qwen3-vl-rerank")
        ),
        "rerank_provider": os.getenv("RERANK_PROVIDER", "dashscope").strip().lower(),
        "type_counts": {name: len(indexes) for name, indexes in sorted(type_groups.items())},
        "random_baseline": _random_baseline(labels, corpus_size, top_k),
        "vector_only": _score(ranked["vector_only"], labels, top_k),
        "fusion_only": _score(ranked["fusion_only"], labels, top_k),
        "hybrid": _score(ranked["hybrid"], labels, top_k),
        "by_type": by_type,
        "latency_ms": {
            "vector_only": _latency_summary(timings["vector_only"]),
            "fusion_only": _latency_summary(timings["fusion_only"]),
            "hybrid": _latency_summary(timings["hybrid"]),
        },
        "details": details,
    }
    if with_ablation:
        result["hybrid_no_rewrite"] = _score(ranked["hybrid_no_rewrite"], labels, top_k)
        result["latency_ms"]["hybrid_no_rewrite"] = _latency_summary(timings["hybrid_no_rewrite"])
    if with_refusal:
        result["refusal"] = _evaluate_refusal(labels, positive_flags, top_k, negatives_path)
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


def _install_embedding_cache(path, refresh=False):
    """把向量化替换成「磁盘缓存优先」的版本

    同一段文本只向 Embedding 接口请求一次，之后重跑直接读缓存：
    指标因此可复现（不受服务端浮点漂移影响），CI 里也不会因为额度失败。
    """
    import hashlib

    import numpy as np

    from tools.chroma_tool import chroma_db

    stored = {}
    if not refresh and os.path.exists(path):
        with np.load(path) as data:
            stored = {key: data[key] for key in data.files}

    original = chroma_db._embedding_content
    stats = {"hit": 0, "miss": 0, "loaded": len(stored)}

    def cached(content):
        key = hashlib.sha1(content.encode("utf-8")).hexdigest()
        if key in stored:
            stats["hit"] += 1
            return stored[key].tolist()
        vector = original(content)
        if vector:
            stored[key] = np.asarray(vector, dtype=np.float32)
            stats["miss"] += 1
        return vector

    chroma_db._embedding_content = cached
    return stored, stats, path


def _save_embedding_cache(stored, path):
    import numpy as np

    np.savez_compressed(path, **stored)


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
    parser.add_argument("--embedding-cache", default=EMBEDDING_CACHE_PATH,
                        help="Embedding 磁盘缓存路径（默认 data/eval_embedding_cache.npz）")
    parser.add_argument("--no-embedding-cache", action="store_true",
                        help="关闭磁盘缓存，每次真实调用 Embedding 接口")
    parser.add_argument("--refresh-embedding-cache", action="store_true",
                        help="忽略已有缓存重新计算（语料或标注改动后使用）")
    args = parser.parse_args()

    cache_store = None
    if not args.no_embedding_cache:
        cache_store, cache_stats, cache_path = _install_embedding_cache(
            args.embedding_cache, refresh=args.refresh_embedding_cache
        )
        print(f"Embedding 缓存: {cache_path}（已加载 {cache_stats['loaded']} 条文本）")

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
    finally:
        if cache_store is not None:
            _save_embedding_cache(cache_store, cache_path)
            print(f"Embedding 缓存命中 {cache_stats['hit']} 次 / 新增 {cache_stats['miss']} 条，"
                  f"共 {len(cache_store)} 条已写入 {cache_path}")
    print("\n" + "=" * 68)
    print(f"样本数: {result['query_count']} | 语料数: {result['corpus_size']} | "
          f"Top-K: {result['top_k']} | Rerank: {'启用' if result['rerank_enabled'] else '未启用（降级）'}")
    if result.get("type_counts"):
        print("标注类型分布: " + ", ".join(f"{k}={v}" for k, v in result["type_counts"].items()))
    random_baseline = result.get("random_baseline")
    if random_baseline:
        print(f"随机基线（{random_baseline['corpus_size']} 选 3）: "
              f"Top-1 = {random_baseline['top1']:.4f}, Top-3 = {random_baseline['top3']:.4f}")
    for key, name in (("vector_only", "纯向量检索"), ("fusion_only", "向量+BM25 融合"),
                      ("hybrid_no_rewrite", "混合检索(无改写)"), ("hybrid", "混合检索(+改写)")):
        metrics = result.get(key)
        if not metrics:
            continue
        print(f"{name:<18}: Top-1 = {metrics['top1']:.4f}, Top-3 = {metrics['top3']:.4f}, "
              f"Recall@{args.top_k} = {metrics['recall_at_k']:.4f}, MRR = {metrics['mrr']:.4f}")

    by_type = result.get("by_type") or {}
    if by_type:
        print("-" * 68)
        print("混合检索分类型指标:")
        for type_name, metrics in by_type.items():
            print(f"  {type_name:<12}({metrics['query_count']:>3} 条): Top-1 = {metrics['top1']:.4f}, "
                  f"Top-3 = {metrics['top3']:.4f}, MRR = {metrics['mrr']:.4f}")

    latency = result.get("latency_ms") or {}
    if latency:
        print("-" * 68)
        print("检索耗时（单次 query 端到端，含 Embedding 调用）:")
        for key, name in (("vector_only", "纯向量检索"), ("fusion_only", "向量+BM25 融合"),
                          ("hybrid", "混合检索(+改写)")):
            stats = latency.get(key)
            if stats and stats.get("count"):
                print(f"  {name:<18}: P50 = {stats['p50_ms']:.1f} ms, "
                      f"P95 = {stats['p95_ms']:.1f} ms, 均值 = {stats['mean_ms']:.1f} ms")

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
        for type_name, stats in (refusal.get("negative_by_type") or {}).items():
            print(f"  域外分类型 {type_name:<10}: 拒答 {stats['refused']}/{stats['total']} "
                  f"= {stats['refusal_rate']:.4f}")
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
