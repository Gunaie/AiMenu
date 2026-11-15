# ---------- 构建阶段：只负责安装依赖，不进入最终镜像 ----------
FROM python:3.10-slim AS builder

WORKDIR /app

RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---------- 运行阶段：精简镜像，非 root 运行 ----------
FROM python:3.10-slim

WORKDIR /app

# 只创建非 root 用户；健康检查用 Python 标准库（见 docker-compose.yml），
# 因此不依赖 apt 装 curl，构建阶段无需访问 deb.debian.org
RUN groupadd -r app && useradd -r -g app app

COPY --from=builder /install /usr/local
COPY . .

# 应用运行期需要写入日志与向量库，统一交给非 root 用户
RUN mkdir -p /app/logs /app/chroma_db && chown -R app:app /app

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV WEB_CONCURRENCY=2

USER app
EXPOSE 8001

# 多 worker 运行，worker 数量可通过 WEB_CONCURRENCY 调整
CMD ["sh", "-c", "gunicorn api.main:app -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY} -b 0.0.0.0:8001 --timeout 120 --access-logfile -"]
