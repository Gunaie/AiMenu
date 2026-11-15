"""检索查询改写：口语同义扩展与价格约束解析。

点餐场景里用户常用口语表达（"红亮亮的甜口红烧肉"、"四十块以内的下饭菜"），
这类 query 既没有标准菜名，也缺少菜谱里常用的描述词，向量与 BM25 都容易跑偏。
本模块提供两个确定性、可单测的轻量改写能力：

1. ``expand_query``：把口语词映射为标准菜品描述词（如 "烧腊" -> "叉烧/烧鹅"），
   供 BM25 二次打分与查询向量拼接使用；
2. ``parse_price_constraint``：从 query 中解析预算区间（"四十块以内" -> (0, 40)），
   供检索阶段按价格过滤候选。

改写只依赖通用中文餐饮词汇，不依赖评测标注集，避免针对评测数据过拟合。
"""
import re
from typing import Dict, List, Optional, Tuple

# 口语表达 -> 标准菜品描述词（键为触发词，值为补充检索词）
SYNONYM_RULES: Dict[Tuple[str, ...], Tuple[str, ...]] = {
    ("红烧肉", "东坡肉"): ("五花肉", "酱香", "红烧"),
    ("红亮", "红亮亮", "色泽红亮"): ("糖色", "蜜汁", "红烧"),
    ("甜口", "偏甜", "甜的", "甜甜的"): ("蜜汁", "糖醋", "拔丝"),
    ("烧腊", "烧味"): ("叉烧", "烧鹅", "烧鸭", "腊味"),
    ("下饭", "下饭菜", "下饭神菜"): ("家常", "小炒", "下饭"),
    ("清淡", "不辣", "口味轻"): ("清蒸", "白灼", "清炒"),
    ("辣的", "辣一点", "重口", "够味"): ("麻辣", "香辣", "剁椒"),
    ("素菜", "吃素", "素的"): ("素", "蔬菜", "豆腐"),
    ("海鲜", "水产"): ("鱼", "虾", "蟹", "贝"),
    ("硬菜", "招牌菜", "特色菜"): ("招牌", "特色", "大菜"),
    ("暖胃", "养胃"): ("汤", "粥", "煲"),
    ("解腻", "开胃"): ("酸辣", "凉拌", "酸"),
    ("锅气", "有锅气"): ("爆炒", "干锅", "小炒"),
    ("主食", "吃点饭"): ("面", "饭", "粉", "饼"),
    ("甜品", "甜点"): ("豆沙", "拔丝", "甜"),
}

# 口语虚词：拼接到查询文本里会稀释向量语义，改写时剔除
_FILLER_WORDS = ("那种", "这种", "一种", "一些", "那个", "这个", "有点", "比较", "稍微", "想吃点", "来点")

# 重排前的意图前缀：CrossEncoder 对「想吃土豆」这类陈述句打分极低
# （同一道菜「想吃土豆」0.01 分，「土豆」0.40 分），重排输入需还原成名词短语
_INTENT_PREFIXES = (
    "我想吃", "我想喝", "我想", "想吃", "想喝", "想要", "想点", "想",
    "来一份", "来一碗", "来碗", "来点", "来几串", "来几个", "来个", "来条", "来一",
    "点一份", "点个", "点一条", "点条", "推荐一下", "推荐", "有没有", "有什么",
    "帮我", "给我", "麻烦", "要一份",
)
# 名词短语前的量词（「一条鱼」->「鱼」）
_MEASURE_PREFIXES = (
    "一条", "一份", "一碗", "一盘", "一杯", "一瓶", "一个", "一只", "一把", "一块", "几串", "几个",
)
# 网络/方言词 -> 菜单里的标准词，只在重排输入里补充，不参与向量召回
_RERANK_SLANG = {"撸": ("烧烤", "烤串", "羊肉串")}

_CN_DIGITS = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}

_NUM = r"(\d+(?:\.\d+)?|[零一二两三四五六七八九十百千]+)"
_PRICE_MAX_PATTERNS = (
    _NUM + r"\s*(?:块钱|块|元|rmb)?\s*(?:钱)?\s*(?:以内|以下|之内|封顶)",
    r"不超过\s*" + _NUM + r"\s*(?:块钱|块|元)?",
    _NUM + r"\s*(?:块钱|块|元)\s*内",
)
_PRICE_MIN_PATTERNS = (
    _NUM + r"\s*(?:块钱|块|元)?\s*(?:以上|往上|起步)",
    r"至少\s*" + _NUM + r"\s*(?:块钱|块|元)?",
)
_DISH_PRICE_PATTERN = re.compile(r"价格[:：]\s*[¥￥]?\s*(\d+(?:\.\d+)?)")


def _cn_to_number(text: str) -> Optional[float]:
    """把「四十」「一百二」「两百五」这类中文数字转成数值；纯数字直接返回"""
    if not text:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text)

    total = 0
    current = 0
    for char in text:
        if char in _CN_DIGITS:
            current = _CN_DIGITS[char]
        elif char in _CN_UNITS:
            unit = _CN_UNITS[char]
            total += (current or 1) * unit
            current = 0
        else:
            return None
    return float(total + current)


def parse_price_constraint(query: str) -> Optional[Tuple[float, float]]:
    """解析 query 中的预算区间，返回 (最低价, 最高价)；未提及价格返回 None"""
    if not query:
        return None

    lower, upper = 0.0, float("inf")
    matched = False

    for pattern in _PRICE_MAX_PATTERNS:
        match = re.search(pattern, query, flags=re.IGNORECASE)
        if match:
            value = _cn_to_number(match.group(1))
            if value is not None and value > 0:
                upper = min(upper, value)
                matched = True
                break

    for pattern in _PRICE_MIN_PATTERNS:
        match = re.search(pattern, query, flags=re.IGNORECASE)
        if match:
            value = _cn_to_number(match.group(1))
            if value is not None and value > 0:
                lower = max(lower, value)
                matched = True
                break

    return (lower, upper) if matched else None


def extract_price(content: str) -> Optional[float]:
    """从菜品文本（菜品ID:1|菜品名称:x|价格:¥45.00）中提取价格"""
    match = _DISH_PRICE_PATTERN.search(content or "")
    return float(match.group(1)) if match else None


def strip_fillers(query: str) -> str:
    """去掉对检索无帮助的口语虚词"""
    cleaned = (query or "").strip()
    for word in _FILLER_WORDS:
        cleaned = cleaned.replace(word, "")
    return cleaned.strip() or (query or "").strip()


def expand_query(query: str) -> List[str]:
    """返回 query 的标准化改写词列表（不含 query 自身已出现的词）"""
    cleaned = strip_fillers(query)
    if not cleaned:
        return []

    expansions: List[str] = []
    for triggers, terms in SYNONYM_RULES.items():
        if any(trigger in cleaned for trigger in triggers):
            for term in terms:
                if term not in cleaned and term not in expansions:
                    expansions.append(term)
    return expansions


def build_search_text(query: str) -> str:
    """拼接用于向量化的文本：去掉虚词的原句 + 同义扩展词"""
    cleaned = strip_fillers(query)
    expansions = expand_query(query)
    if not expansions:
        return cleaned
    return " ".join([cleaned] + expansions)


def build_rerank_query(query: str) -> str:
    """构造 CrossEncoder 的重排输入：名词短语 + 网络词的标准写法。

    本地 bge-reranker 对完整陈述句（「想喝汤」「想吃土豆」）给出的分数接近 0，
    即便候选里就是对应菜品；剥掉意图词与量词后分数才回到可用区间
    （「想喝汤」0.03 ->「汤」0.62）。这里只改重排输入，不影响召回与返回给用户的 query。
    """
    text = strip_fillers(query)
    changed = True
    while changed:
        changed = False
        for prefix in _INTENT_PREFIXES:
            if text.startswith(prefix) and len(text) > len(prefix):
                text = text[len(prefix):].strip()
                changed = True
                break

    for measure in _MEASURE_PREFIXES:
        if text.startswith(measure) and len(text) > len(measure):
            text = text[len(measure):].strip()
            break

    extras: List[str] = []
    for trigger, terms in _RERANK_SLANG.items():
        if trigger in query:
            extras.extend(term for term in terms if term not in text and term not in extras)

    return " ".join([text] + extras) if extras else text
