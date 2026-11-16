"""检索改写与拒答判定单测。

覆盖：口语同义扩展、预算约束解析、价格过滤、无答案判定。
用例全部离线执行，不访问 DashScope。
"""
import pytest

from tools import chroma_tool
from tools.chroma_tool import ChromaVectorDB, _judge_answerable
from tools.local_reranker import format_rerank_document
from tools.query_expand import (
    build_rerank_query,
    build_search_text,
    expand_query,
    extract_price,
    parse_diet_constraints,
    parse_price_constraint,
    strip_fillers,
    update_session_constraints,
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
    ("想吃土豆", "土豆"),
    ("想点一条鱼", "鱼"),
    ("来一份和牛刺身", "和牛刺身"),
    ("有什么清淡不辣的汤", "清淡不辣的汤"),
    ("麻婆豆腐", "麻婆豆腐"),
])
def test_build_rerank_query_returns_noun_phrase(query, expected):
    assert build_rerank_query(query) == expected


def test_build_rerank_query_adds_slang_terms():
    text = build_rerank_query("想撸串")

    assert "烧烤" in text and "羊肉串" in text


def test_format_rerank_document_drops_noise_fields():
    text = format_rerank_document(
        "菜品ID:146|菜品名称:烤羊肉串|价格:¥12.00|菜品描述:炭火烤制|分类:烧烤|辣度:中辣"
    )

    assert text.startswith("烤羊肉串：炭火烤制")
    assert "菜品ID" not in text and "价格" not in text
    assert "分类：烧烤" in text


def test_format_rerank_document_keeps_plain_text():
    assert format_rerank_document("回锅肉") == "回锅肉"


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


@pytest.mark.parametrize("query, expected", [
    ("来点不辣的", {"max_spice": 0}),
    ("微辣的就行", {"max_spice": 1}),
    ("来点中辣的", {"max_spice": 2}),
    ("来点辣的", {"max_spice": 3}),
    ("想吃素菜", {"vegetarian": True}),
    ("推荐点川菜", {}),
])
def test_parse_diet_constraints(query, expected):
    assert parse_diet_constraints(query) == expected


@pytest.mark.parametrize("query, previous, expected", [
    ("来点不辣的", {}, {"max_spice": 0}),
    ("预算50元以内", {}, {"max_price": 50.0}),
    ("想吃素菜", {"max_spice": 1}, {"max_spice": 1, "vegetarian": True}),
    ("来点辣的", {"max_spice": 0}, {}),
    ("推荐个麻辣香锅", {"max_spice": 0}, {"max_spice": 0}),
    ("预算不限", {"max_price": 50.0}, {}),
    ("不用素了", {"vegetarian": True}, {}),
])
def test_update_session_constraints(query, previous, expected):
    assert update_session_constraints(previous, query) == expected


class _DietCollection:
    """带结构化 metadata 的替身集合，用于验证硬过滤"""

    def query(self, **kwargs):
        return {
            "ids": [["1", "2", "3"]],
            "metadatas": [[
                {
                    "content": "菜品ID:1|菜品名称:麻婆豆腐|价格:¥28.00",
                    "line_number": 1, "dish_id": "1", "dish_name": "麻婆豆腐",
                    "spice_level": 2, "is_vegetarian": False,
                },
                {
                    "content": "菜品ID:2|菜品名称:清炒时蔬|价格:¥18.00",
                    "line_number": 2, "dish_id": "2", "dish_name": "清炒时蔬",
                    "spice_level": 0, "is_vegetarian": False,
                },
                {
                    "content": "菜品ID:3|菜品名称:白灼菜心|价格:¥22.00",
                    "line_number": 3, "dish_id": "3", "dish_name": "白灼菜心",
                    "spice_level": 0, "is_vegetarian": True,
                },
            ]],
        }


def _diet_db(monkeypatch):
    db = ChromaVectorDB()
    db.collection = _DietCollection()
    db.bm25 = None
    db.use_rerank = False
    db.use_rewrite = True
    monkeypatch.setattr(db, "_embedding_content", lambda query: [0.1, 0.2])
    return db


def test_hybrid_search_filters_by_spice_metadata(monkeypatch):
    results = _diet_db(monkeypatch).hybrid_search("来点不辣的", top_k=5)

    assert [item["dish_name"] for item in results] == ["清炒时蔬", "白灼菜心"]


def test_hybrid_search_filters_vegetarian_metadata(monkeypatch):
    results = _diet_db(monkeypatch).hybrid_search("想吃素菜", top_k=5)

    assert [item["dish_name"] for item in results] == ["白灼菜心"]


def test_hybrid_search_explicit_constraints_override_query(monkeypatch):
    """会话槽位传入的约束优先于 query 文本解析"""
    results = _diet_db(monkeypatch).hybrid_search(
        "推荐川菜", top_k=5, diet_constraints={"max_spice": 0}
    )

    assert [item["dish_name"] for item in results] == ["清炒时蔬", "白灼菜心"]


def test_hybrid_search_explicit_price_range(monkeypatch):
    results = _diet_db(monkeypatch).hybrid_search(
        "推荐川菜", top_k=5, price_range=(0.0, 20.0)
    )

    assert [item["dish_name"] for item in results] == ["清炒时蔬"]


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
