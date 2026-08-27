"""
ChromaDB向量数据库工具模块

该模块提供ChromaDB向量数据库的连接和操作功能，
用于存储和查询菜品信息的向量化数据，支持语义搜索
"""

import os
import dashscope
from dotenv import load_dotenv
from typing import List, Dict, Any
import chromadb
from chromadb.config import Settings
from http import HTTPStatus
import logging

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ChromaVectorDB:
    """ChromaDB向量数据库的操作"""

    def __init__(self):
        self.dashscope_api_key = os.getenv("DASHSCOPE_API_KEY")
        self.persist_directory = os.path.join(os.path.dirname(os.path.dirname(__file__)), "chroma_db")
        
        # 配置集合名字、嵌入模型名字、嵌入模型的维度
        self.collection_name = "menu-item-collection"
        self.embedding_model = "text-embedding-v4"
        self.dimension = 1536

        # 配置chroma客户端对象以及集合对象
        self.client = None
        self.collection = None

    def initialize_connection(self) -> bool:
        """初始化ChromaDB客户端对象以及集合对象"""
        try:
            # 1. 初始化客户端对象
            self.client = chromadb.PersistentClient(path=self.persist_directory)

            # 2. 获取或创建集合对象
            self.collection = self.client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"}
            )
            
            logger.info("初始化向量数据库ChromaDB客户端以及集合对象成功")
            return True
        except Exception as e:
            logger.error(f"初始化向量数据库ChromaDB客户端以及集合对象失败:{e}")
            return False

    def clear_index_vectors(self) -> bool:
        """清空指定集合下的向量数据"""
        try:
            if not self.collection and not self.initialize_connection():
                logger.error("集合不存在")
                return False

            # ChromaDB 删除集合中所有数据
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
        """对文本进行向量化"""
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
                logger.info(f"文本向量化成功")
                return resp.get('output').get('embeddings')[0].get('embedding')
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
            if not menu_data:
                from smart_diancan.tools.db_tool import get_all_menu_items
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
                    
                    ids.append(str(line_num))
                    embeddings.append(vector)
                    metadatas.append({
                        "content": chunk,
                        "line_number": line_num,
                        "dish_id": f"菜品ID:{line_num}",
                        "type": "menu_item"
                    })

                if ids:
                    self.collection.add(
                        ids=ids,
                        embeddings=embeddings,
                        metadatas=metadatas
                    )
                
                logger.info("切分之后的文本内容成功存储到ChromaDB中...")
                return True
            return False
        except Exception as e:
            logger.error(f"同步数据到ChromaDB失败{e}")
            return False

    def search_similar_menu_item(self, query: str, top_k: int = 2) -> List[Dict[str, Any]]:
        """相似性检索"""
        try:
            if not self.collection and not self.initialize_connection():
                return []

            query_vector = self._embedding_content(query)
            if not query_vector:
                return []

            results = self.collection.query(
                query_embeddings=[query_vector],
                n_results=top_k,
                include=["metadatas", "distances"]
            )

            final_results = []
            if results['ids'] and results['ids'][0]:
                for i in range(len(results['ids'][0])):
                    final_results.append({
                        "id": results['ids'][0][i],
                        "score": 1 - results['distances'][0][i], # Convert distance to similarity score
                        "content": results['metadatas'][0][i]['content'],
                        "line_number": results['metadatas'][0][i]['line_number']
                    })
            
            logger.info(f"查询到相似的文档命中个数{len(final_results)}")
            return final_results
        except Exception as e:
            logger.error(f"相似性检索失败：{e}")
            return []

# 定义全局实例
chroma_db = ChromaVectorDB()

def search_menu_items(query: str, top_k: int = 2) -> List[str]:
    match_result = chroma_db.search_similar_menu_item(query, top_k=top_k)
    return [item['content'] for item in match_result]

def search_menu_items_with_ids(query: str, top_k: int = 2) -> Dict[str, Any]:
    match_result = chroma_db.search_similar_menu_item(query=query, top_k=top_k)
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
