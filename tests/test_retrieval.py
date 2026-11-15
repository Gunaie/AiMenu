"""检索标注集校验 + 可选的真实链路评测。

默认只做离线校验（数据文件与标注合法性），不会调用外部接口。
如需跑真实检索指标：
    RUN_INTEGRATION=1 pytest tests/test_retrieval.py -s
"""
import os

import pytest

from scripts.eval_rag import evaluate, load_labels, load_menu_dishes


def test_menu_dataset_is_valid():
    dishes = load_menu_dishes()
    assert len(dishes) >= 100
    for name, item in dishes.items():
        assert name.strip()
        assert float(item["price"]) > 0
        assert int(item["spice_level"]) in (0, 1, 2, 3)
        assert isinstance(item["is_vegetarian"], bool)
        assert item["category"].strip()


def test_labeled_dataset_is_valid():
    labels = load_labels()
    dishes = load_menu_dishes()
    assert len(labels) >= 100

    for item in labels:
        assert item["query"].strip()
        assert item["expected"], f"{item['query']} 缺少期望菜品"
        assert set(item["expected"]) <= set(dishes)


@pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1",
    reason="需要 DashScope 与本地 chroma_db，设置 RUN_INTEGRATION=1 后运行",
)
def test_hybrid_search_recall_on_labeled_set():
    result = evaluate(top_k=3, verbose=False)

    assert result["hybrid"]["recall_at_k"] >= 0.6
