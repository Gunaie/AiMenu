"""检索改写与拒答判定单测。

覆盖：口语同义扩展、预算约束解析、价格过滤、无答案判定。
用例全部离线执行，不访问 DashScope。
"""
import pytest

from tools import chroma_tool
from tools.chroma_tool import ChromaVectorDB, _judge_answerable
from tools.query_expand import (
    build_search_text,
    expand_query,
    extract_price,
    parse_price_constraint,
    strip_fillers,
)


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


def test_expand_query_maps_colloquial_terms():
    expansions = expand_query("广式烧腊里的甜口猪肉")

    assert "叉烧" in expansions
    assert "蜜汁" in expansions
    # 不扩展与 query 无关的类别词
    assert "清蒸" not in expansions


def test_expand_query_skips_when_no_colloquial_trigger():
    assert expand_query("麻婆豆腐") == []


def test_strip_fillers_removes_oral_filler():
    assert strip_fillers("那种红亮亮的甜口红烧肉") == "红亮亮的甜口红烧肉"


def test_build_search_text_contains_original_and_expansions():
    text = build_search_text("想吃点烧腊")

    assert text.startswith("烧腊")
    assert "叉烧" in text


@pytest.mark.parametrize("query, expected", [
    ("四十块以内的下饭菜", (0, 40)),
    ("30元以内", (0, 30)),
    ("不超过八十元的菜", (0, 80)),
    ("一百块以上的硬菜", (100, float("inf"))),
    ("有什么推荐", None),
])
def test_parse_price_constraint(query, expected):
    assert parse_price_constraint(query) == expected


def test_extract_price_from_menu_content():
    assert extract_price("菜品ID:1|菜品名称:回锅肉|价格:¥45.00") == 45.0
    assert extract_price("菜品ID:1|菜品名称:回锅肉") is None


def test_hybrid_search_filters_by_price(vector_db):
    results = vector_db.hybrid_search("三十块以内的菜", top_k=2)

    assert [item["content"] for item in results] == [
        "菜品ID:2|菜品名称:麻婆豆腐|价格:¥28.00"
    ]


def test_judge_answerable_returns_none_without_rerank(monkeypatch):
    monkeypatch.setattr(chroma_tool.chroma_db, "use_rerank", True)
    monkeypatch.setattr(chroma_tool.chroma_db, "_rerank_available", False)

    assert _judge_answerable([0.0, 0.0]) is None


def test_judge_answerable_uses_threshold(monkeypatch):
    monkeypatch.setattr(chroma_tool.chroma_db, "use_rerank", True)
    monkeypatch.setattr(chroma_tool.chroma_db, "_rerank_available", True)
    monkeypatch.setattr(chroma_tool.chroma_db, "min_relevance_score", 0.13)

    assert _judge_answerable([0.006, 0.006]) is False
    assert _judge_answerable([0.14, 0.01]) is True
