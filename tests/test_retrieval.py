"""检索标注集校验 + 可选的真实链路评测。

默认只做离线校验（标注数量、菜名合法性），不会调用外部接口。
如需跑真实检索指标：
    RUN_INTEGRATION=1 pytest tests/test_retrieval.py -s
"""
import os

import pytest

from scripts.eval_rag import KNOWN_DISHES, LABELED_QUERIES, evaluate


def test_labeled_dataset_is_valid():
    assert len(LABELED_QUERIES) >= 30

    for item in LABELED_QUERIES:
        assert item["query"].strip()
        assert item["expected"], f"{item['query']} 缺少期望菜品"
        assert set(item["expected"]) <= KNOWN_DISHES


@pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1",
    reason="需要 DashScope 与本地 chroma_db，设置 RUN_INTEGRATION=1 后运行",
)
def test_hybrid_search_recall_on_labeled_set():
    result = evaluate(top_k=3, verbose=False)

    assert result["hybrid"]["recall_at_k"] >= 0.6
