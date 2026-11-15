# AiMenu 智能点餐系统 (AiMenu Smart Ordering System)

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Vue](https://img.shields.io/badge/vue-3.3-green)](https://vuejs.org/)
[![Docker](https://img.shields.io/badge/docker-middleware-blue)](https://www.docker.com/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

AiMenu 是一个可本地运行的全栈 AI 点餐项目：FastAPI 提供业务接口，LangGraph 编排工具调用型 Agent，
ChromaDB + BM25 + Rerank 完成菜品检索，高德地图负责配送范围校验，Vue 3 + Element Plus 提供点餐界面。

> 指标说明：以下检索指标由 `scripts/eval_rag.py` 在 12 道菜品的本地语料上实测（36 条标注），
> 可通过同一脚本复现；小规模语料下的绝对值不代表大规模场景效果。

---

## 核心能力

- **工具调用型 Agent**：基于 LangGraph 构建「决策 → 调用工具 → 生成回复」循环，接入菜单检索、配送校验、订单查询三类工具，支持 SSE 流式输出与多轮上下文。
- **混合检索**：向量召回（text-embedding-v4）+ BM25 关键词召回，合并去重后由 DashScope Rerank 精排；Rerank 不可用时自动降级为融合结果。
- **配送范围校验**：高德地址补全、地理编码与路径规划，按距离计算阶梯配送费并拦截超范围下单。
- **订单闭环**：JWT 登录、菜单、购物车、下单、模拟支付、订单历史；金额与配送费由服务端重算。
- **缓存与一致性**：Redis 缓存地理编码、距离计算结果与菜单热点数据；菜品更新采用延迟双删策略。

---

## 技术栈

| 层次 | 技术 |
| --- | --- |
| 后端 | FastAPI、Uvicorn、Pydantic |
| AI | LangGraph、LangChain、Qwen-Plus（DashScope）、text-embedding-v4、gte-rerank |
| 检索 | ChromaDB、rank_bm25、jieba |
| 存储 | MySQL 8、Redis 7 |
| 前端 | Vue 3、Element Plus、Vite |
| 部署 | Docker / Docker Compose |

---

## 目录结构

```text
AiMenu/
├── agent/              # Agent 状态机、工具定义
├── api/                # FastAPI 路由与请求/响应模型
├── service/            # 业务服务层（点餐、订单、支付）
├── tools/              # MySQL / Redis / ChromaDB / 高德 / JWT 工具
├── tests/              # 单元测试与接口测试
├── scripts/            # 检索评测脚本
├── ui/                 # Vue 3 前端工程
├── docker-compose.yml  # MySQL(3307) + Redis(6379)
├── Dockerfile          # 后端镜像
├── setup_db.py         # 建表 + 菜品数据初始化 + 向量同步
├── run.py              # 后端启动入口
└── requirements.txt
```

---

## 快速开始

### 1. 配置环境变量

```bash
# Windows
copy .env.example .env
# macOS / Linux
cp .env.example .env
```

至少需要填写 `DASHSCOPE_API_KEY`（大模型与向量化）和 `AMAP_API_KEY`（配送校验）。

### 2. 启动中间件

```bash
docker compose up -d
```

默认启动 MySQL（宿主机 3307）与 Redis（宿主机 6379）。

### 3. 初始化并启动后端

```bash
# 推荐使用 uv
uv venv --python 3.10
.venv\Scripts\activate        # macOS/Linux: source .venv/bin/activate
uv pip install -r requirements.txt

python setup_db.py            # 建表 + 12 道菜品 + 向量入库（仅首次）
python run.py
```

API 地址：http://127.0.0.1:8001 ，接口文档：http://127.0.0.1:8001/docs

### 4. 启动前端

```bash
cd ui
npm install
npm run dev
```

访问 http://localhost:3000 （Vite 已配置 `/api` 代理到后端）。

### 可选：构建镜像

```bash
docker build -t aimenu-backend .
docker build -t aimenu-web ./ui
```

`ui/nginx.conf` 已包含 `/api` 反向代理与 SSE 关闭缓冲的配置。

---

## 测试与评测

```bash
# 单元测试与接口测试（不依赖数据库、外部 API）
pytest -q

# 真实链路检索评测（需要 DashScope 可用 + 已执行 setup_db.py）
python scripts/eval_rag.py --top-k 3 --json eval_result.json

# 将检索评测接入 pytest（可选）
RUN_INTEGRATION=1 pytest tests/test_retrieval.py -s
```

`scripts/eval_rag.py` 内置 36 条「query → 期望菜品」标注，分别统计纯向量检索与混合检索的
Recall@K 和 MRR；若 Embedding 接口不可用，脚本会直接中止而不是输出失真指标。

### 实测结果（Top-3，Rerank 已启用）

| 检索策略 | Recall@3 | MRR |
| --- | --- | --- |
| 纯向量检索 | 0.9167 | 0.8472 |
| 向量 + BM25 + Rerank | **1.0000** | **0.9583** |

语料为 `setup_db.py` 初始化的 12 道菜，标注集见 `scripts/eval_rag.py` 中的 `LABELED_QUERIES`。
语料规模较小，指标主要用于验证链路与回归对比，不构成大规模检索效果的结论。

---

## 安全设计

- 密码使用 bcrypt 加盐哈希，认证使用 JWT Bearer Token。
- 支付接口校验订单归属，越权操作返回 403。
- 下单金额、配送费由服务端依据数据库价格与实时距离重算，不信任客户端传值。
- SQL 全部参数化；Agent 的订单查询工具强制按当前 `user_id` 过滤。

---

## 已知限制

- Agent 记忆使用进程内 `MemorySaver`，重启即丢失，暂不支持多实例部署。
- 数据库访问与地图请求为同步实现，在 async 路由中直接调用会阻塞事件循环，高并发场景需改为异步驱动或线程池。
- 菜单仅 12 条，检索指标不代表大规模语料下的效果。
- 支付为模拟实现，未接入真实支付渠道；通知推送为 `BackgroundTasks` 占位逻辑。
- 菜品 `category` 字段与前端筛选（川菜/粤菜/浙菜）尚未完全对齐。

---

## 测试账号

- 用户名：`admin`
- 密码：`password123`

首次部署后请立即修改默认密码与 `JWT_SECRET_KEY`。

---

## License

本项目基于 [MIT License](LICENSE) 开源。
