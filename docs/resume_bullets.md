# AiMenu 简历文案

## 一句话定位

全栈 AI 智能点餐系统：LangGraph 工具调用型 Agent + 混合检索（向量 / BM25 / RRF / Cross-Encoder 精排）
+ 无答案拒答，FastAPI + Vue 3 + MySQL / Redis / ChromaDB。

## 简历条目（三条版，推荐）

- 基于 LangGraph 搭建工具调用型 Agent（菜单检索 / 配送校验 / 订单查询），支持 SSE 流式输出与多轮上下文；会话记忆与配送地址槽位落 Redis，按 `user_id + session_id` 隔离并做 TTL / 轮数裁剪；14 条端到端用例工具选择准确率 14/14、`answerable` 判定 100%、拒答话术 LLM 评审 3/3。
- 自建 153 道菜品语料、150 条标注与 18 条域外问题的评测集，实现向量 + BM25 混合召回、RRF 融合与 Cross-Encoder 精排：Top-3 Recall 0.9933、MRR 0.9656；以重排最高分做无答案拒答，按固定种子切分 dev/test 标定阈值，held-out 误拒 3/75、域外问题 18/18 全部拒答。
- 定位并修复本地 bge-reranker 对口语 query 打分失真：重排前做 query 名词化与候选结构化，正例分数中位数 0.80 → 0.94、低分正例 59 → 15 条，使零额度本地模型达到与云端 API 持平的召回。

## 简历条目（技术深度版，可选替换第三条）

- 工程化与稳定性：JWT + 服务端金额重算 + 订单归属校验；Redis 分钟级限流、LLM 超时重试与 token 成本日志；MySQL 连接池 + 阻塞调用移出事件循环，压测 QPS 57.8 → 332.2（并发 20，复跑 355.3 / 363.2，零错误）；前端先转义再渲染 Markdown，消除 `v-html` 的 DOM XSS。
- 交付：Docker 多阶段构建、非 root 运行、gunicorn 多 uvicorn worker；依赖精确锁定；GitHub Actions 覆盖 pytest、前端构建与可选的真实链路评测；仓库内置检索 / 阈值 / Agent 三套可复现评测脚本。

## 技术栈（简历用一行）

Python、FastAPI、LangGraph、LangChain、ChromaDB、BM25 / RRF、Cross-Encoder（bge-reranker-v2-m3）、
Redis、MySQL、Vue 3、Element Plus、Docker、GitHub Actions

## 素材清单

已有：

- README 里的 Mermaid 架构图与运行截图/录屏：`screenshots/chat.gif`、`screenshots/chat.png`、`screenshots/home.png`
- 三套评测脚本与结果：`scripts/eval_rag.py`、`scripts/eval_threshold.py`、`scripts/eval_agent.py`，
  明细见 `data/eval_rag_local.json`、`data/eval_threshold_local.json`、`data/eval_agent_local.json`

待补（简历加分项）：

- 公网 Demo 链接：需部署到云主机或内网穿透，当前未做

## 面试口径（避免被追问翻车）

- 所有数字都能用 README「测试与评测」里的命令复现，先说清是**本地 153 道菜的模拟语料**，不是线上规模。
- 拒答命中率 2/3 是关键词启发式，主动说明「帮我写代码」那条回复用了「主要负责…」而非关键词表里的措辞；
  LLM 评审 3/3 才是更可信的口径。
- 多轮地址继承不是靠提示词「求模型记住」，而是 Redis 会话槽位 + 工具参数补齐（`_patch_delivery_calls`），
  这条可以答「你怎么保证多轮稳定性」。
- 压测口径：单机、并发 20、持续 10 秒、每个请求带 Token，`/menu/list` 含鉴权依赖里的 DB 查询；
  改造前的 57.8 QPS 是当时记录，复现需回滚连接池/线程池改造，面试时如实说明。
- 本地 Rerank 平均 18.5s/条含 CPU 推理与高德超时重试，不要把它说成线上延迟。
- 更多问答见 [interview_qa.md](./interview_qa.md)。
