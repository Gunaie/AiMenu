"""混合检索单测：融合去重、Top-K 截断与 Rerank 失败降级。

用例全部使用替身对象，不访问 ChromaDB、DashScope 或数据库。
"""
import dashscope
import pytest

from tools.chroma_tool import ChromaVectorDB, search_menu_items_with_ids


class _FakeCollection:
    def query(self, **kwargs):
        return {
            "ids": [["1", "2"]],
            "metadatas": [[
                {"content": "菜品ID:1|菜品名称:回锅肉|价格:¥45.00", "line_number": 1},
                {"content": "菜品ID:2|菜品名称:麻婆豆腐|价格:¥28.00", "line_number": 2},
            ]],
        }


class _FakeBM25:
    def get_scores(self, tokens):
        return [2.0, 0.5]


@pytest.fixture
def vector_db(monkeypatch):
    db = ChromaVectorDB()
    db.collection = _FakeCollection()
    db.corpus = [
        "菜品ID:2|菜品名称:麻婆豆腐|价格:¥28.00",
        "菜品ID:1|菜品名称:回锅肉|价格:¥45.00",
    ]
    db.corpus_metadatas = [
        {"content": db.corpus[0], "line_number": 2},
        {"content": db.corpus[1], "line_number": 1},
    ]
    db.bm25 = _FakeBM25()
    db.use_rerank = False
    monkeypatch.setattr(db, "_embedding_content", lambda query: [0.1, 0.2])
    return db


def test_hybrid_search_deduplicates_candidates(vector_db):
    results = vector_db.hybrid_search("麻婆豆腐", top_k=5)

    contents = [item["content"] for item in results]
    assert len(contents) == 2
    assert len(set(contents)) == 2


def test_hybrid_search_respects_top_k(vector_db):
    results = vector_db.hybrid_search("麻婆豆腐", top_k=1)

    assert len(results) == 1
    assert results[0]["content"].startswith("菜品ID:1")


def test_hybrid_search_falls_back_when_rerank_fails(vector_db, monkeypatch):
    vector_db.use_rerank = True
    vector_db._rerank_available = True

    def _boom(**kwargs):
        raise RuntimeError("network error")

    monkeypatch.setattr(dashscope.TextReRank, "call", _boom)

    results = vector_db.hybrid_search("麻婆豆腐", top_k=2)

    assert len(results) == 2
    assert all(item["score"] == 0.0 for item in results)
    assert vector_db._rerank_available is False


def test_search_menu_items_with_ids_parses_dish_id(monkeypatch):
    monkeypatch.setattr(
        "tools.chroma_tool.chroma_db.hybrid_search",
        lambda query, top_k=3: [
            {
                "id": "1",
                "content": "菜品ID:4|菜品名称:回锅肉|价格:¥45.00",
                "score": 0.9,
                "line_number": 4,
            }
        ],
    )

    result = search_menu_items_with_ids("回锅肉", top_k=1)

    assert result["ids"] == ["4"]
    assert "回锅肉" in result["contents"][0]
