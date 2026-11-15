"""
ChromaDB向量数据库工具模块

该模块提供ChromaDB向量数据库的连接和操作功能，
用于存储和查询菜品信息的向量化数据，支持语义搜索
"""

import os
import dashscope
import jieba
from rank_bm25 import BM25Okapi
from dotenv import load_dotenv
from typing import List, Dict, Any, Optional, Tuple
import chromadb
from chromadb.config import Settings
from http import HTTPStatus
import logging
from tools.query_expand import (
    build_search_text,
    expand_query,
    extract_price,
    parse_price_constraint,
    strip_fillers,
)

# 以 .env 为准：避免系统中残留的旧 Key 覆盖项目配置
load_dotenv(override=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ChromaVectorDB:
    """ChromaDB向量数据库的操作，支持混合检索与重排序"""

    def __init__(self):
        self.dashscope_api_key = os.getenv("DASHSCOPE_API_KEY")
        self.persist_directory = os.path.join(os.path.dirname(os.path.dirname(__file__)), "chroma_db")
        
        # 配置集合名字、嵌入模型名字、嵌入模型的维度
        self.collection_name = "menu-item-collection"
        self.embedding_model = "text-embedding-v4"
        self.rerank_model = os.getenv("RERANK_MODEL", "qwen3-rerank")  # DashScope 的重排序模型
        self.dimension = 1536
        self.use_rerank = os.getenv("USE_RERANK", "true").lower() == "true"
        self._rerank_available = True  # 记录 Rerank 是否可用

        # 阈值参数（qwen3-rerank 实测：正例 top1 最低 0.42，域外最高 0.39）
        # rewrite_score_threshold：首轮重排置信度低于该值时启用同义扩展二次召回
        # min_relevance_score：最高相关度低于该值则判定为「无答案」，用于兜底拒答
        self.rewrite_score_threshold = float(os.getenv("RETRIEVAL_REWRITE_SCORE", "0.55"))
        self.min_relevance_score = float(os.getenv("RETRIEVAL_MIN_SCORE", "0.40"))
        self.BM25_EXPANSION_WEIGHT = float(os.getenv("BM25_EXPANSION_WEIGHT", "0.5"))

        # 配置chroma客户端对象以及集合对象
        self.client = None
        self.collection = None
        
        # BM25 相关
        self.bm25 = None
        self.corpus = []
        self.corpus_metadatas = []

    def initialize_connection(self) -> bool:
        """初始化ChromaDB客户端对象以及集合对象，并加载 BM25 语料"""
        try:
            # 1. 初始化客户端对象 (禁用匿名遥测以避免 posthog 版本冲突)
            self.client = chromadb.PersistentClient(
                path=self.persist_directory,
                settings=Settings(anonymized_telemetry=False)
            )

            # 2. 获取或创建集合对象
            self.collection = self.client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"}
            )
            
            # 3. 初始化 BM25 语料
            try:
                self._init_bm25_corpus()
            except Exception as e:
                logger.error(f"加载 BM25 语料库失败: {e}")
                # BM25 失败不影响基础向量搜索，但记录日志
            
            logger.info("初始化向量数据库ChromaDB及 BM25 语料成功")
            return True
        except Exception as e:
            logger.error(f"初始化向量数据库ChromaDB失败:{e}")
            self.collection = None # 确保 collection 为 None 表示不可用
            return False

    def _init_bm25_corpus(self):
        """从 ChromaDB 中加载所有数据到 BM25 语料库"""
        if not self.collection:
            return
        
        try:
            results = self.collection.get(include=["metadatas", "documents"])
            if results['ids']:
                self.corpus = []
                self.corpus_metadatas = []
                tokenized_corpus = []
                
                for i in range(len(results['ids'])):
                    metadata = results['metadatas'][i]
                    if not metadata or 'content' not in metadata:
                        continue
                        
                    content = metadata['content']
                    if not isinstance(content, str):
                        content = str(content)
                        
                    self.corpus.append(content)
                    self.corpus_metadatas.append(metadata)
                    # 使用 jieba 进行分词
                    tokenized_corpus.append(list(jieba.cut(content)))
                
                if tokenized_corpus:
                    self.bm25 = BM25Okapi(tokenized_corpus)
                    logger.info(f"BM25 语料库加载完成，共 {len(self.corpus)} 条记录")
        except Exception as e:
            logger.error(f"BM25 语料库初始化内部失败: {e}")
            raise e

    def clear_index_vectors(self) -> bool:
        """清空指定集合下的向量数据"""
        try:
            if not self.collection and not self.initialize_connection():
                logger.error("集合不存在")
                return False

            ids = self.collection.get()["ids"]
            if ids:
                self.collection.delete(ids=ids)
                logger.info("成功删除集合下所有的向量数据")
            else:
                logger.info("该集合下不存在任何向量数据")

            return True
        except Exception as e:
            logger.error(f"删除集合下的向量数据失败:{e}")
            return False

    def _embedding_content(self, content: str) -> List[float] or None:
        """对文本进行向量化 (带缓存)"""
        if not hasattr(self, '_embedding_cache'):
            self._embedding_cache = {}
        
        if content in self._embedding_cache:
            return self._embedding_cache[content]

        try:
            if not self.dashscope_api_key:
                logger.error("dashscope api_key not found!")
                return None

            resp = dashscope.TextEmbedding.call(
                api_key=self.dashscope_api_key,
                model=self.embedding_model,
                input=content,
                dimension=self.dimension,
            )

            if resp.status_code == HTTPStatus.OK:
                embedding = resp.get('output').get('embeddings')[0].get('embedding')
                self._embedding_cache[content] = embedding
                return embedding
            else:
                logger.error(f"发送文本嵌入模型请求处理失败: {resp.message}")
                return None
        except Exception as e:
            logger.error(f"发送文本嵌入模型请求处理失败,原因:{e}")
            return None

    def _validate_datasource(self, validation_content: str) -> bool:
        """校验数据源"""
        if not validation_content:
            logger.error("数据源不存在")
            return False
        validate_result_str = ("当前无可用的菜品信息", "查询菜品信息失败")
        return not validation_content.startswith(validate_result_str)

    def _splite_content(self, splite_content: str) -> List[str]:
        """切割加载到的菜品信息 (按行切分，确保每道菜是一个独立单元)"""
        try:
            lines = [line.strip() for line in splite_content.split('\n') if line.strip()]
            print(f"菜品信息切割后的块个数：{len(lines)}")
            return lines
        except Exception as e:
            logger.error(f"文本切割失败:{e}")
            return []

    def upsert_menu_data(self, menu_data: str = None, clear_existing: bool = True) -> bool:
        """将文本向量存储到ChromaDB向量数据库"""
        try:
            from tools.db_tool import get_all_menu_items
            if clear_existing:
                self.clear_index_vectors()
            
            menu_item_str = get_all_menu_items()
            if not self._validate_datasource(menu_item_str):
                return False

            embedding_chunks = self._splite_content(menu_item_str)
            if not embedding_chunks:
                return False

            if not self.collection and not self.initialize_connection():
                return False

            ids = []
            embeddings = []
            metadatas = []
            
            for line_num, chunk in enumerate(embedding_chunks, 1):
                vector = self._embedding_content(chunk)
                if not vector:
                    continue
                
                # 提取关键词用于混合搜索
                keywords = ",".join(jieba.cut_for_search(chunk))
                
                ids.append(str(line_num))
                embeddings.append(vector)
                metadatas.append({
                    "content": chunk,
                    "line_number": line_num,
                    "dish_id": f"菜品ID:{line_num}",
                    "type": "menu_item",
                    "keywords": keywords
                })

            if ids:
                self.collection.add(
                    ids=ids,
                    embeddings=embeddings,
                    metadatas=metadatas
                )
            
            # 更新 BM25 语料库
            self._init_bm25_corpus()
            
            logger.info("切分之后的文本内容成功存储到ChromaDB中...")
            return True
        except Exception as e:
            logger.error(f"同步数据到ChromaDB失败{e}")
            return False

    def _rerank_with_dashscope(self, query: str, candidates: List[Dict[str, Any]],
                               top_k: int) -> List[Dict[str, Any]]:
        """使用 DashScope Rerank API 进行重排序"""
        try:
            if not candidates:
                return []
            
            if not self.use_rerank or not self._rerank_available:
                # 补充默认得分以防报错
                for c in candidates:
                    if 'score' not in c:
                        c['score'] = 0.0
                return candidates[:top_k]

            docs = [c['content'] for c in candidates]
            
            # 调用 DashScope Rerank API
            resp = dashscope.TextReRank.call(
                api_key=self.dashscope_api_key,
                model=self.rerank_model,
                query=query,
                documents=docs,
                top_n=top_k
            )
            
            if resp.status_code == HTTPStatus.OK:
                reranked_results = []
                for item in resp.output.results:
                    idx = item.index
                    reranked_results.append({
                        "id": candidates[idx]['id'],
                        "score": float(item.relevance_score),
                        "content": candidates[idx]['content'],
                        "line_number": candidates[idx]['line_number']
                    })
                return reranked_results
            else:
                # 额度耗尽/权限不足等失败都会让返回分数全为 0，此时必须停用重排序，
                # 否则「无答案拒答」会把正常 query 误判为不可回答
                if "Access denied" in resp.message:
                    logger.warning(f"Rerank API 访问被拒绝，已禁用重排序功能。请检查阿里云 DashScope 控制台是否开通了 {self.rerank_model} 服务。")
                else:
                    logger.warning(f"Rerank API 调用失败: {resp.message}，已禁用重排序并降级为融合排序")
                self._rerank_available = False
                
                # 补充默认得分以防报错
                for c in candidates:
                    if 'score' not in c:
                        c['score'] = 0.0
                return candidates[:top_k]
        except Exception as e:
            logger.error(f"重排序失败: {e}")
            self._rerank_available = False # 发生异常也暂时禁用
            for c in candidates:
                if 'score' not in c:
                    c['score'] = 0.0
            return candidates[:top_k]

    @staticmethod
    def _fuse_candidates(
        vector_candidates: List[Dict[str, Any]],
        bm25_candidates: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """RRF 融合两路召回结果，按内容去重"""
        candidates: Dict[str, Dict[str, Any]] = {}
        fusion_scores: Dict[str, float] = {}
        for source_candidates in (vector_candidates, bm25_candidates):
            for rank, candidate in enumerate(source_candidates, 1):
                content = candidate['content']
                candidates.setdefault(content, candidate)
                fusion_scores[content] = fusion_scores.get(content, 0.0) + 1.0 / (60 + rank)
        return sorted(
            candidates.values(),
            key=lambda c: fusion_scores[c['content']],
            reverse=True,
        )

    @staticmethod
    def _filter_by_price(candidates: List[Dict[str, Any]],
                         price_range: Tuple[float, float]) -> List[Dict[str, Any]]:
        """按预算区间过滤候选菜品；全部被过滤时退回原始候选，避免无结果"""
        lower, upper = price_range
        filtered = []
        for candidate in candidates:
            price = extract_price(candidate.get('content', ''))
            if price is not None and lower <= price <= upper:
                filtered.append(candidate)
        if not filtered:
            logger.info(f"预算区间 {price_range} 未命中任何菜品，已退回未过滤候选")
            return candidates
        return filtered

    def _collect_candidates(self, query: str, expand: bool = False,
                            price_range: Optional[Tuple[float, float]] = None) -> List[Dict[str, Any]]:
        """向量 + BM25 双路召回并做 RRF 融合

        expand=True 时用同义扩展后的文本做向量召回，并按 0.5 权重叠加扩展词的 BM25 分数，
        用于口语化 query 的兜底召回。
        """
        # 1. 向量搜索（带预算约束时扩大候选池，避免过滤后数量不足）
        search_text = build_search_text(query) if expand else strip_fillers(query)
        query_vector = self._embedding_content(search_text)
        vector_candidates = []
        if query_vector:
            vector_results = self.collection.query(
                query_embeddings=[query_vector],
                n_results=30 if price_range else 10,
                include=["metadatas", "distances"]
            )
            if vector_results['ids'] and vector_results['ids'][0]:
                for i in range(len(vector_results['ids'][0])):
                    metadata = vector_results['metadatas'][0][i]
                    vector_candidates.append({
                        "id": vector_results['ids'][0][i],
                        "content": metadata['content'],
                        "line_number": metadata['line_number'],
                        "source": "vector"
                    })

        # 2. BM25 搜索 (Top 10 候选)
        bm25_candidates = []
        if self.bm25:
            tokenized_query = list(jieba.cut(strip_fillers(query)))
            scores = list(self.bm25.get_scores(tokenized_query))
            if expand:
                extra_terms = expand_query(query)
                if extra_terms:
                    extra_scores = self.bm25.get_scores(list(jieba.cut(" ".join(extra_terms))))
                    scores = [
                        base + self.BM25_EXPANSION_WEIGHT * extra
                        for base, extra in zip(scores, extra_scores)
                    ]
            # 获取前 10 个得分最高的索引
            top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:10]
            for idx in top_indices:
                if scores[idx] > 0:
                    metadata = self.corpus_metadatas[idx]
                    bm25_candidates.append({
                        "id": str(metadata['line_number']),
                        "content": self.corpus[idx],
                        "line_number": metadata['line_number'],
                        "source": "bm25"
                    })

        # 3. 合并去重 + RRF 融合排序
        candidates = self._fuse_candidates(vector_candidates, bm25_candidates)

        # 4. 预算约束过滤
        if price_range:
            candidates = self._filter_by_price(candidates, price_range)
        return candidates

    def _should_rewrite(self, results: List[Dict[str, Any]]) -> bool:
        """Rerank 置信度偏低时，才用同义扩展做二次召回"""
        if not (self.use_rerank and self._rerank_available and results):
            return False
        top_score = max((item.get("score", 0.0) for item in results), default=0.0)
        return top_score < self.rewrite_score_threshold

    def hybrid_search(self, query: str, top_k: int = 2) -> List[Dict[str, Any]]:
        """高级混合检索：向量检索 + BM25关键词检索 + Rerank重排序

        改写策略：
        - 命中预算约束（"四十块以内"）时先按价格过滤候选；
        - 首轮 Rerank 置信度低于阈值时，用同义扩展再做一次召回，合并候选池后重排；
        - Rerank 始终以用户原始 query 为准，改写只用于扩大召回。
        """
        try:
            if not self.collection and not self.initialize_connection():
                return []

            price_range = parse_price_constraint(query)
            candidates = self._collect_candidates(query, price_range=price_range)
            if not candidates:
                return []

            if self.use_rerank:
                logger.info(f"进入重排序阶段，候选数量: {len(candidates)}")
                results = self._rerank_with_dashscope(query, candidates, top_k)
            else:
                results = candidates[:top_k]

            if self._should_rewrite(results) and expand_query(query):
                expanded_candidates = self._collect_candidates(
                    query, expand=True, price_range=price_range
                )
                known = {item['content'] for item in candidates}
                additions = [item for item in expanded_candidates if item['content'] not in known]
                if additions:
                    logger.info(f"Rerank 置信度偏低，同义扩展补充 {len(additions)} 个候选后重排")
                    results = self._rerank_with_dashscope(query, candidates + additions, top_k)
            return results
        except Exception as e:
            logger.error(f"高级混合检索失败：{e}")
            return []

    def search_similar_menu_item(self, query: str, top_k: int = 2) -> List[Dict[str, Any]]:
        """回退到混合检索"""
        return self.hybrid_search(query, top_k)

# 定义全局实例
chroma_db = ChromaVectorDB()

def search_menu_items(query: str, top_k: int = 2) -> List[str]:
    match_result = chroma_db.hybrid_search(query, top_k=top_k)
    return [item['content'] for item in match_result]


def _judge_answerable(scores: List[float]) -> Optional[bool]:
    """根据 Rerank 最高分判断菜单能否回答该问题

    返回 None 表示无法判断（Rerank 未启用/不可用），调用方应按可回答处理。
    """
    if not (chroma_db.use_rerank and chroma_db._rerank_available):
        return None
    if not scores:
        return False
    return max(scores) >= chroma_db.min_relevance_score


def search_menu_items_with_ids(query: str, top_k: int = 2) -> Dict[str, Any]:
    match_result = chroma_db.hybrid_search(query=query, top_k=top_k)
    if not match_result:
        return {
            "contents": [],
            "ids": [],
            "scores": [],
            "answerable": _judge_answerable([]),
            "top_score": 0.0,
        }

    ids = []
    import re
    for item in match_result:
        content = item['content']
        re_match = re.search(r"菜品ID:(\d+)", content)
        dish_id = re_match.group(1) if re_match else item['id']
        ids.append(dish_id)

    scores = [item.get('score', 0.0) for item in match_result]
    return {
        "contents": [item['content'] for item in match_result],
        "ids": ids,
        "scores": scores,
        "answerable": _judge_answerable(scores),
        "top_score": round(max(scores), 4) if scores else 0.0,
    }

if __name__ == "__main__":
    # 初始化并同步数据
    db = ChromaVectorDB()
    if db.upsert_menu_data():
        print("数据同步成功！")
    else:
        print("数据同步失败！")
