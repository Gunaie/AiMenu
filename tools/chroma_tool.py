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
from typing import List, Dict, Any, Optional
import chromadb
from chromadb.config import Settings
from http import HTTPStatus
import logging

load_dotenv()

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
        self.rerank_model = "gte-rerank" # DashScope 的重排序模型
        self.dimension = 1536
        self.use_rerank = os.getenv("USE_RERANK", "true").lower() == "true"
        self._rerank_available = True  # 记录 Rerank 是否可用

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
            # 1. 初始化客户端对象
            self.client = chromadb.PersistentClient(path=self.persist_directory)

            # 2. 获取或创建集合对象
            self.collection = self.client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"}
            )
            
            # 3. 初始化 BM25 语料
            self._init_bm25_corpus()
            
            logger.info("初始化向量数据库ChromaDB及 BM25 语料成功")
            return True
        except Exception as e:
            logger.error(f"初始化向量数据库ChromaDB失败:{e}")
            return False

    def _init_bm25_corpus(self):
        """从 ChromaDB 中加载所有数据到 BM25 语料库"""
        if not self.collection:
            return
        
        results = self.collection.get(include=["metadatas", "documents"])
        if results['ids']:
            self.corpus = []
            self.corpus_metadatas = []
            tokenized_corpus = []
            
            for i in range(len(results['ids'])):
                content = results['metadatas'][i]['content']
                self.corpus.append(content)
                self.corpus_metadatas.append(results['metadatas'][i])
                # 使用 jieba 进行分词
                tokenized_corpus.append(list(jieba.cut(content)))
            
            if tokenized_corpus:
                self.bm25 = BM25Okapi(tokenized_corpus)
                logger.info(f"BM25 语料库加载完成，共 {len(self.corpus)} 条记录")

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
        """切割加载到的菜品信息"""
        try:
            from langchain_text_splitters import RecursiveCharacterTextSplitter
            text_spliter = RecursiveCharacterTextSplitter(chunk_size=100, chunk_overlap=0, separators=["\n"], length_function=len)
            docs = text_spliter.create_documents([splite_content])
            clearn_docs = [doc.page_content.strip() for doc in docs]
            print(f"菜品信息切割后的块个数：{len(clearn_docs)}")
            return clearn_docs
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

    def _rerank_with_dashscope(self, query: str, candidates: List[Dict[str, Any]], top_k: int) -> List[Dict[str, Any]]:
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
                if "Access denied" in resp.message:
                    logger.warning(f"Rerank API 访问被拒绝，已禁用重排序功能。请检查阿里云 DashScope 控制台是否开通了 {self.rerank_model} 服务。")
                    self._rerank_available = False
                else:
                    logger.warning(f"Rerank API 调用失败: {resp.message}，使用原始排序")
                
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

    def hybrid_search(self, query: str, top_k: int = 2) -> List[Dict[str, Any]]:
        """高级混合检索：向量检索 + BM25关键词检索 + Rerank重排序"""
        try:
            if not self.collection and not self.initialize_connection():
                return []

            # 1. 向量搜索 (Top 10 候选)
            query_vector = self._embedding_content(query)
            vector_candidates = []
            if query_vector:
                vector_results = self.collection.query(
                    query_embeddings=[query_vector],
                    n_results=10,
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
                tokenized_query = list(jieba.cut(query))
                scores = self.bm25.get_scores(tokenized_query)
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

            # 3. 合并去重
            all_candidates = []
            seen_contents = set()
            for c in vector_candidates + bm25_candidates:
                if c['content'] not in seen_contents:
                    all_candidates.append(c)
                    seen_contents.add(c['content'])

            # 4. Rerank 重排序 (取前 top_k)
            if all_candidates:
                logger.info(f"进入重排序阶段，候选数量: {len(all_candidates)}")
                return self._rerank_with_dashscope(query, all_candidates, top_k)
            
            return []
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

def search_menu_items_with_ids(query: str, top_k: int = 2) -> Dict[str, Any]:
    match_result = chroma_db.hybrid_search(query=query, top_k=top_k)
    if not match_result:
        return {"contents": [], "ids": [], "scores": []}

    ids = []
    import re
    for item in match_result:
        content = item['content']
        re_match = re.search(r"菜品ID:(\d+)", content)
        dish_id = re_match.group(1) if re_match else item['id']
        ids.append(dish_id)

    return {
        "contents": [item['content'] for item in match_result],
        "ids": ids,
        "scores": [item['score'] for item in match_result]
    }

if __name__ == "__main__":
    # 初始化并同步数据
    db = ChromaVectorDB()
    if db.upsert_menu_data():
        print("数据同步成功！")
    else:
        print("数据同步失败！")
