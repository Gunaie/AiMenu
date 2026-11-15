"""RAG 检索评测脚本。

在标注问答集上对比两种检索策略的 Top-K 命中率与 MRR：
1. 纯向量检索（ChromaDB + text-embedding-v4）
2. 混合检索（向量 + BM25 + DashScope Rerank）

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

# 菜单中的 12 道菜，用于校验标注数据的合法性
KNOWN_DISHES = {
    "东坡肉", "佛跳墙", "剁椒鱼头", "回锅肉", "地三鲜", "宫保鸡丁",
    "广式肠粉", "清蒸鲈鱼", "番茄炒蛋", "鱼香肉丝", "麻婆豆腐", "龙井虾仁",
}

# 标注集：query -> 期望命中的菜品（命中任意一个即算该条召回成功）
LABELED_QUERIES = [
    {"query": "我想吃东坡肉", "expected": ["东坡肉"]},
    {"query": "红烧肉有吗", "expected": ["东坡肉", "回锅肉"]},
    {"query": "有没有佛跳墙", "expected": ["佛跳墙"]},
    {"query": "佛跳墙多少钱", "expected": ["佛跳墙"]},
    {"query": "剁椒鱼头辣不辣", "expected": ["剁椒鱼头"]},
    {"query": "推荐几个川菜", "expected": ["回锅肉", "宫保鸡丁", "鱼香肉丝", "麻婆豆腐"]},
    {"query": "麻婆豆腐", "expected": ["麻婆豆腐"]},
    {"query": "想吃麻辣的豆腐", "expected": ["麻婆豆腐"]},
    {"query": "宫保鸡丁", "expected": ["宫保鸡丁"]},
    {"query": "鱼香肉丝", "expected": ["鱼香肉丝"]},
    {"query": "回锅肉", "expected": ["回锅肉"]},
    {"query": "不辣的菜", "expected": ["东坡肉", "清蒸鲈鱼", "广式肠粉", "龙井虾仁", "地三鲜", "番茄炒蛋"]},
    {"query": "有什么素食", "expected": ["地三鲜", "番茄炒蛋"]},
    {"query": "想吃清淡一点的", "expected": ["广式肠粉", "清蒸鲈鱼", "番茄炒蛋", "龙井虾仁"]},
    {"query": "推荐一个汤品", "expected": ["佛跳墙"]},
    {"query": "有什么海鲜", "expected": ["佛跳墙", "清蒸鲈鱼", "龙井虾仁"]},
    {"query": "想吃鱼", "expected": ["剁椒鱼头", "清蒸鲈鱼"]},
    {"query": "有虾吗", "expected": ["龙井虾仁"]},
    {"query": "早餐吃什么", "expected": ["广式肠粉"]},
    {"query": "杭州菜有什么", "expected": ["东坡肉", "龙井虾仁"]},
    {"query": "浙江菜推荐", "expected": ["东坡肉", "龙井虾仁"]},
    {"query": "福建菜有什么", "expected": ["佛跳墙"]},
    {"query": "湖南菜推荐", "expected": ["剁椒鱼头"]},
    {"query": "四川菜有什么", "expected": ["回锅肉", "宫保鸡丁", "鱼香肉丝", "麻婆豆腐"]},
    {"query": "东北菜有什么", "expected": ["地三鲜"]},
    {"query": "广东菜推荐", "expected": ["广式肠粉"]},
    {"query": "番茄炒蛋", "expected": ["番茄炒蛋"]},
    {"query": "地三鲜", "expected": ["地三鲜"]},
    {"query": "清蒸鱼", "expected": ["清蒸鲈鱼"]},
    {"query": "龙井虾仁", "expected": ["龙井虾仁"]},
    {"query": "想吃便宜点的", "expected": ["广式肠粉", "番茄炒蛋", "地三鲜", "麻婆豆腐"]},
    {"query": "请客想点硬菜", "expected": ["佛跳墙", "东坡肉"]},
    {"query": "有什么不辣的肉菜", "expected": ["东坡肉"]},
    {"query": "酸甜口的菜", "expected": ["番茄炒蛋", "宫保鸡丁"]},
    {"query": "有没有带汤的菜", "expected": ["佛跳墙"]},
    {"query": "适合两个人吃的家常菜", "expected": ["地三鲜", "番茄炒蛋", "鱼香肉丝"]},
]


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


def evaluate(top_k=3, verbose=True):
    """运行完整评测，返回两组指标与逐条明细"""
    from tools.chroma_tool import search_menu_items_with_ids

    labels = [item["expected"] for item in LABELED_QUERIES]
    hybrid_ranked = []
    vector_ranked = []
    details = []

    for item in LABELED_QUERIES:
        query = item["query"]
        hybrid_names = _dish_names(search_menu_items_with_ids(query, top_k=top_k)["contents"])
        vector_names = _dish_names(_vector_only_search(query, top_k))

        hybrid_ranked.append(hybrid_names)
        vector_ranked.append(vector_names)
        details.append({
            "query": query,
            "expected": item["expected"],
            "hybrid": hybrid_names,
            "vector_only": vector_names,
        })

        if verbose:
            print(f"[{query}] 期望={item['expected']} 混合={hybrid_names} 向量={vector_names}")

    hybrid_metrics = _score(hybrid_ranked, labels, top_k)
    vector_metrics = _score(vector_ranked, labels, top_k)

    from tools.chroma_tool import chroma_db

    return {
        "top_k": top_k,
        "query_count": len(LABELED_QUERIES),
        "rerank_enabled": bool(chroma_db.use_rerank and chroma_db._rerank_available),
        "hybrid": hybrid_metrics,
        "vector_only": vector_metrics,
        "details": details,
    }


def main():
    parser = argparse.ArgumentParser(description="AiMenu RAG 检索评测")
    parser.add_argument("--top-k", type=int, default=3, help="评测使用的 Top-K，默认 3")
    parser.add_argument("--json", dest="json_path", help="将评测结果写入指定 JSON 文件")
    parser.add_argument("--quiet", action="store_true", help="只输出汇总指标")
    args = parser.parse_args()

    try:
        result = evaluate(top_k=args.top_k, verbose=not args.quiet)
    except RuntimeError as exc:
        print(f"\n[评测中止] {exc}")
        raise SystemExit(2)
    print("\n" + "=" * 56)
    print(f"评测样本数: {result['query_count']} | Top-K: {result['top_k']} | Rerank: {'启用' if result['rerank_enabled'] else '未启用（降级）'}")
    print(f"纯向量检索: Recall@{args.top_k} = {result['vector_only']['recall_at_k']:.4f}, "
          f"MRR = {result['vector_only']['mrr']:.4f}")
    print(f"混合检索  : Recall@{args.top_k} = {result['hybrid']['recall_at_k']:.4f}, "
          f"MRR = {result['hybrid']['mrr']:.4f}")
    print("=" * 56)

    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as fp:
            json.dump(result, fp, ensure_ascii=False, indent=2)
        print(f"评测明细已写入: {args.json_path}")


if __name__ == "__main__":
    main()
