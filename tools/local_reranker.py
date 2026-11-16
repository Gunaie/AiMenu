"""本地 Cross-Encoder 重排序实现（可选依赖）。

默认使用 BAAI/bge-reranker-v2-m3，通过 sentence-transformers 在本地推理，
不依赖 DashScope 免费额度。模型按需懒加载，未安装可选依赖时由调用方降级。
"""
import logging
import os
import threading
import time
from typing import List, Optional

logger = logging.getLogger(__name__)

# 菜品元数据里对相关性判断有用的字段；菜品ID/价格/过敏原等噪声字段在重排输入中丢弃
_RERANK_DOC_FIELDS = ("菜品名称", "菜品描述", "分类", "辣度", "口味", "主要食材", "烹饪方法", "素食")


def format_rerank_document(content: str) -> str:
    """把「菜品ID:1|菜品名称:x|价格:¥9.9|...」转成更适合 CrossEncoder 的自然文本。

    原始串里的 ID、价格、竖线分隔符会稀释语义，实测同一道菜的相关度分数能差一倍
    （「烤串」对原始串 0.56，对「菜名：描述 分类：…」0.94）。
    """
    if not content or "|" not in content:
        return content

    fields = {}
    for segment in content.split("|"):
        if ":" in segment:
            key, value = segment.split(":", 1)
            fields[key.strip()] = value.strip()

    name = fields.get(_RERANK_DOC_FIELDS[0])
    if not name:
        return content

    description = fields.get("菜品描述", "")
    attributes = " ".join(
        f"{key}：{fields[key]}" for key in _RERANK_DOC_FIELDS[2:] if fields.get(key)
    )
    return f"{name}：{description} {attributes}".strip()


class LocalReranker:
    """线程安全的懒加载 CrossEncoder 封装"""

    def __init__(self):
        self.model_name = os.getenv("LOCAL_RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
        self.device = os.getenv("LOCAL_RERANK_DEVICE", "auto")
        self.batch_size = int(os.getenv("LOCAL_RERANK_BATCH_SIZE", "16"))
        self.max_length = int(os.getenv("LOCAL_RERANK_MAX_LENGTH", "512"))
        self.use_fp16 = os.getenv("LOCAL_RERANK_FP16", "false").lower() == "true"
        self._model = None
        self._lock = threading.Lock()

    def _resolve_device(self) -> str:
        if self.device != "auto":
            return self.device
        try:
            import torch
            return "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            return "cpu"

    def _load(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                import torch
                from sentence_transformers import CrossEncoder
            except ImportError as exc:
                raise RuntimeError(
                    "未安装本地 Rerank 依赖，请先执行 pip install -r requirements-local-rerank.txt"
                ) from exc

            device = self._resolve_device()
            model_kwargs = None
            if self.use_fp16 and device == "cuda":
                model_kwargs = {"torch_dtype": torch.float16}

            logger.info("加载本地 Rerank 模型: %s (device=%s)", self.model_name, device)
            self._model = CrossEncoder(
                self.model_name,
                device=device,
                max_length=self.max_length,
                activation_fn=torch.nn.Sigmoid(),
                model_kwargs=model_kwargs,
            )
            logger.info("本地 Rerank 模型加载完成")
            return self._model

    def warmup(self) -> bool:
        """预加载权重，避免第一个检索请求承担冷启动（实测首次对话会被拖慢 100s+）

        预热失败不抛出：真正检索时会再尝试一次，仍失败则按既有逻辑降级为融合排序。
        """
        if self._model is not None:
            return True
        try:
            started = time.perf_counter()
            self._load()
            loaded_at = time.perf_counter()
            # 只加载权重不够：第一次推理还要初始化 torch 线程池与算子，实测多花 5~7s，
            # 因此这里跑一次最小批量的真实推理，把首次请求的「首推理」开销也挪到启动阶段。
            self.rerank("预热查询", ["菜品名称：番茄蛋汤 分类：汤类"])
            logger.info(
                "本地 Rerank 预热完成，加载 %.1fs + 首推理 %.1fs",
                loaded_at - started,
                time.perf_counter() - loaded_at,
            )
            return True
        except Exception as exc:
            logger.warning("本地 Rerank 预热失败，将在首次检索时重试: %s", exc)
            return False

    def rerank(self, query: str, documents: List[str]) -> List[float]:
        """返回与 documents 顺序一致的 0~1 相关度分数"""
        if not documents:
            return []
        model = self._load()
        scores = model.predict(
            [(query, doc) for doc in documents],
            batch_size=self.batch_size,
            show_progress_bar=False,
        )
        return [float(score) for score in scores]

    @property
    def loaded(self) -> bool:
        return self._model is not None


_local_reranker: Optional[LocalReranker] = None
_reranker_lock = threading.Lock()


def get_local_reranker() -> LocalReranker:
    """全局单例，避免每个请求重复加载模型"""
    global _local_reranker
    if _local_reranker is None:
        with _reranker_lock:
            if _local_reranker is None:
                _local_reranker = LocalReranker()
    return _local_reranker
