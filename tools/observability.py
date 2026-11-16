"""轻量可观测性：trace_id 透传、结构化日志与 LLM 成本累计

不引入 LangSmith / OpenTelemetry 这类额外依赖，先用「一个 trace_id 贯穿全链路 + 一份
进程内 token 账本」把现有日志串起来：HTTP 请求进来生成 trace_id，日志行自动带出，
LangGraph 每次模型调用的 token 用量按 trace_id 归属，`/metrics/cost` 直接读累计值。

环境变量：
    LOG_FORMAT=json   # 输出单行 JSON 日志，便于采集；默认 text
"""
import contextvars
import json
import logging
import os
import threading
import time
import uuid

_trace_id = contextvars.ContextVar("trace_id", default="-")
_started_at = time.time()

_usage_lock = threading.Lock()
_usage = {
    "calls": 0,
    "input_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0,
    "by_model": {},
}


def new_trace_id():
    return uuid.uuid4().hex[:16]


def set_trace_id(value):
    return _trace_id.set(value or "-")


def get_trace_id():
    return _trace_id.get()


class TraceIdFilter(logging.Filter):
    """给每条日志补上当前 trace_id"""

    def filter(self, record):
        record.trace_id = get_trace_id()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": getattr(record, "trace_id", "-"),
            "message": record.getMessage(),
        }
        return json.dumps(payload, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def format(self, record):
        record.trace_id = getattr(record, "trace_id", "-")
        return (
            f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<5} "
            f"[{record.trace_id}] {record.name}: {record.getMessage()}"
        )


def setup_logging(level=logging.INFO):
    """在现有 handler 上挂 trace_id 过滤器，并按 LOG_FORMAT 切换格式"""
    root = logging.getLogger()
    root.setLevel(level)
    as_json = os.getenv("LOG_FORMAT", "text").lower() == "json"
    formatter = JsonFormatter() if as_json else TextFormatter()

    if not root.handlers:
        handler = logging.StreamHandler()
        root.addHandler(handler)
    for handler in root.handlers:
        handler.setFormatter(formatter)
        if not any(isinstance(item, TraceIdFilter) for item in handler.filters):
            handler.addFilter(TraceIdFilter())

    # uvicorn 自己的 logger 不继承 root 的 handler，这里单独挂一次
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        for handler in logger.handlers:
            handler.setFormatter(formatter)
            if not any(isinstance(item, TraceIdFilter) for item in handler.filters):
                handler.addFilter(TraceIdFilter())


def record_usage(model, usage):
    """累计一次模型调用的 token 用量（线程安全，供 /metrics/cost 读取）

    只在上游唯一的模型调用点（agent._call_llm）记账，避免流式链路里
    「节点内调用 + 事件聚合」两处都记导致翻倍。
    """
    if not usage:
        return
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or input_tokens + output_tokens)
    with _usage_lock:
        _usage["calls"] += 1
        _usage["input_tokens"] += input_tokens
        _usage["output_tokens"] += output_tokens
        _usage["total_tokens"] += total_tokens
        model_usage = _usage["by_model"].setdefault(
            model or "unknown", {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        )
        model_usage["calls"] += 1
        model_usage["input_tokens"] += input_tokens
        model_usage["output_tokens"] += output_tokens
        model_usage["total_tokens"] += total_tokens


def _estimate_cost(input_tokens, output_tokens):
    """按 .env 配置的每千 token 单价估算成本；未配置则返回 None（不编造价格）"""
    try:
        input_price = float(os.getenv("COST_INPUT_PER_1K", "0"))
        output_price = float(os.getenv("COST_OUTPUT_PER_1K", "0"))
    except ValueError:
        return None
    if not input_price and not output_price:
        return None
    return round(input_tokens / 1000 * input_price + output_tokens / 1000 * output_price, 6)


def usage_snapshot():
    with _usage_lock:
        snapshot = json.loads(json.dumps(_usage))
    snapshot["estimated_cost"] = _estimate_cost(
        snapshot["input_tokens"], snapshot["output_tokens"]
    )
    snapshot["cost_currency"] = os.getenv("COST_CURRENCY", "CNY")
    snapshot["uptime_seconds"] = round(time.time() - _started_at, 1)
    return snapshot
