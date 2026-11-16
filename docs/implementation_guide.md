# AiMenu 智能点餐系统：一步步实现手册 (新手向)

本手册旨在指导一名初学者从零开始构建一个具备工业级深度的 AI 智能点餐系统。

---

## 阶段一：环境搭建与架构设计

### 1. 技术栈选择
*   **后端**: FastAPI (高性能、异步)
*   **AI 核心**: LangGraph (状态机管理) + DashScope (通义千问模型)
*   **数据库**: MySQL (关系数据) + Redis (缓存) + ChromaDB (向量数据库)
*   **前端**: Vue 3 + Element Plus

### 2. 基础设施部署 (Docker)
使用 Docker 运行中间件，避免在本地安装繁琐的软件。
*   编写 `docker-compose.yml`，配置 MySQL 8.0 和 Redis 7.0。
*   **注意**: MySQL 建议映射到非标准端口（如 3307），防止与本地冲突。

---

## 阶段二：数据建模与初始化

### 1. 数据库设计
*   **users 表**: 存储账户信息，密码需通过 `bcrypt` 加密。
*   **menu_items 表**: 核心业务表，包含名称、价格、辣度、主要食材等。
*   **orders 表**: 存储订单流水，关键字段是 `items_detail` (JSON 类型) 和 `user_id`。

### 2. 数据灌入
*   编写 `setup_db.py` 脚本，预置 1060 道菜品数据（含东坡肉、佛跳墙等）。
*   同步逻辑：在插入 MySQL 的同时，将菜品描述向量化并存入 ChromaDB。

---

## 阶段三：AI 核心 - RAG 与 Agent 实现

### 1. RAG 混合检索
*   **向量检索**: 使用 `text-embedding-v4` 将用户问题转化为向量。
*   **关键词检索**: 引入 `rank_bm25`，提取问题中的菜名关键词。
*   **重排序 (Rerank)**: 默认调用 DashScope `qwen3-vl-rerank` 对候选菜品精排；也可切到本地 `bge-reranker-v2-m3`（`RERANK_PROVIDER=local`，免额度）。

### 2. LangGraph 智能体
*   定义 `AgentState`：包含消息列表、推荐菜品 ID 和用户信息。
*   构建状态机图：
    - `agent` 节点：调用 LLM 决策。
    - `tools` 节点：执行具体工具（菜单搜索、地址校验、订单查询）。
    - 循环逻辑：直到 LLM 给出最终答复。

---

## 阶段四：业务接口开发

### 1. 鉴权系统 (JWT)
*   实现 `/auth/register` 和 `/auth/login`。
*   登录成功返回 `access_token`，后续请求在 Header 中带上 `Authorization: Bearer <token>`。

### 2. 对话与配送接口
*   `/chat/stream`: 实现 SSE 流式输出，提升用户体验。
*   `/delivery`: 调用高德地图 API，计算距离并判断是否在配送范围内。

---

## 阶段五：前端开发与联调

### 1. 登录墙机制
*   使用 Vue 3 全局状态管理用户信息。
*   未登录时，通过 CSS 滤镜 `blur(8px)` 模糊主界面，强制弹出登录窗口。

### 2. AI 交互体验
*   支持 Markdown 渲染 AI 回复。
*   实现“菜品自动高亮”：当 AI 返回 `menu_ids` 时，前端自动滚动到首个推荐菜品，并在左侧列表标注「推荐」徽标。

---

## 阶段六：评估与上线

### 1. 链路监控
*   可选：在 `.env` 中打开 `LANGCHAIN_TRACING_V2` 并填写 `LANGCHAIN_API_KEY`，即可在 LangSmith 后台查看 Agent 的推理步骤与耗时（默认关闭）。

### 2. 功能验证
*   运行测试脚本 `pytest tests/`。
*   手动测试：问 AI “我想点川菜”，验证是否准确召回“回锅肉”等菜品。
