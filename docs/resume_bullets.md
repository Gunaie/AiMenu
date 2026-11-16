# AiMenu 简历文案

## 一句话定位

全栈 AI 智能点餐系统：LangGraph 工具调用型 Agent + 混合检索（向量 / BM25 / RRF / Cross-Encoder 精排）
+ 无答案拒答，FastAPI + Vue 3 + MySQL / Redis / ChromaDB。

## 简历条目（三条版，推荐）

- 基于 LangGraph 搭建工具调用型 Agent（菜单检索 / 配送校验 / 订单查询），支持 SSE 流式输出与多轮上下文；会话记忆与配送地址槽位落 Redis，按 `user_id + session_id` 隔离并做 TTL / 轮数裁剪；61 条端到端用例分三类统计（评审模型固定 `qwen-plus`）：工具选择 0.9672、最终答复正确率 0.8689、如实拒答 0.6923（关键词与评审两个口径一致；两者互补、无一条同时失败，人工核对 13 条均明确拒答），延迟 P50 9.96s / P95 25.09s，单次约 2.7k tokens；图入口另加确定性护栏，空输入与越权请求 0 token、0 工具调用直接拒答。
- 模型 A/B：发现评测脚本的 `--model` 被各工具模块的 `load_dotenv(override=True)` 覆盖失效，改为显式注入模型实例后，用同一评审模型重跑 61 条——固定快照 `qwen3.7-plus-2026-05-26` 相比 `qwen-plus` 工具选择 0.9672 vs 0.8689、答复正确率 0.8689 vs 0.8361，延迟基本持平（11.32s vs 11.74s）而 token 多 12%；`qwen-plus` 的退化在「该不该调工具」（6 条推荐类直接不检索、凭上下文报菜名），只看检索指标与人工抽检都会漏掉。
- 自建 1060 道菜品语料、585 条四类分层标注（菜名直查 125 / 口语化 270 / 属性约束 95 / 易混菜区分 95）与 84 条域外问题（含 15 条 prompt 注入、10 条越权请求），实现向量 + BM25 混合召回、RRF 融合与 Cross-Encoder 精排：Top-1 0.8171 / Top-3 0.9060 / MRR 0.8564（本地 bge-reranker-v2-m3），随机基线 Top-1 仅 0.0021；以重排最高分做无答案拒答，按固定种子切分 dev/test 标定阈值，held-out 误拒 10/293、误收 2/42，prompt 注入与越权请求 25/25 全部拒答。属性约束类 Top-1 0.4947，已如实标注为当前最弱环节（标注标准答案非全语料穷举，指标偏保守）。
- 定位并修复本地 bge-reranker 对口语 query 打分失真：重排前做 query 名词化与候选结构化，正例分数中位数 0.80 → 0.94、低分正例 59 → 15 条，使零额度本地模型可以离线复现完整检索与拒答评测。
- 冷启动优化：日志拆出首次对话 78 秒的两段一次性开销（Rerank 权重加载 66s、LangChain/LangGraph 导入与 Agent 构建 19.4s），改为启动后两个后台线程预热并跑一次最小推理，启动不被阻塞；首个对话请求 TTFT 19.3s → 1.95s、总耗时 28.6s → 6.53s（稳态 1.46s / 6.05s），预热失败只降级不阻断启动。

## 简历条目（技术深度版，可选替换第三条）

- 工程化与稳定性：JWT + 服务端金额重算 + 订单归属校验；Redis 分钟级限流（对话按用户、登录按「用户名 + IP」）、JWT 不使用仓库默认密钥（未配置时进程级随机生成并告警）、基础安全响应头 + 1MB 请求体上限、对外参数长度收敛（query ≤500 字、订单条目 ≤50），登录失败统一文案且用户不存在也执行一次 bcrypt 以抹平账号枚举时序；LLM 超时重试与 token 成本日志；MySQL 连接池 + 阻塞调用移出事件循环，压测 QPS 57.8 → 332.2（并发 20，复跑 355.3 / 363.2，零错误）；对 `/chat/stream` 做 5/10/20 并发 SSE 压测，TTFT P50 1.3s → 3.1s、P95 1.4s → 17.8s，tokens/s 30 → 18.8，零 5xx，25 并发突发时 5/25 被 429 限流；检索评测默认启用 Embedding 磁盘缓存（sha1 → 向量），指标可复现且 CI 不依赖实时额度；Agent 图入口加确定性护栏（空输入/纯标点、越权索要他人订单与隐私），命中即拒答且 0 token、0 工具调用，提示词另设权限边界兜底；前端先转义再渲染 Markdown，消除 `v-html` 的 DOM XSS。
- 可观测性：HTTP 中间件生成或透传 `X-Trace-ID`，日志统一带 trace_id 且可切 JSON 结构化输出；LangGraph 侧累计每次模型调用的 token，`/metrics/cost` 暴露调用次数、输入/输出 token 与按单价估算的成本，流式响应末尾同步下发 `usage` 事件。
- 订单一致性：下单在单事务内完成「幂等校验 → 按菜品 ID 升序 `SELECT ... FOR UPDATE` 锁库存 → 校验并扣减 → 写订单」，任一步失败显式回滚释放行锁；`Idempotency-Key` + 唯一索引保证重试/并发重复提交只落一单、库存只扣一次；前端每次下单生成一次性 UUID 作幂等键，并在确认弹窗到支付提示之间用 `submitting` 标记拦截重复点击，Playwright 用例同帧连点两次「提交订单」实测只发 1 次请求、只落 1 单；订单状态机 `pending → paid → completed`，`pending` 可取消、终态冻结，非法跃迁返回 409。实测：库存置 5、20 线程并发各下 1 份 → 成功 5、库存不足拒绝 15、最终库存 0；10 并发同幂等键 → 落库 1 笔、返回同一订单号。
- 交付：Docker 多阶段构建、非 root 运行、gunicorn 多 uvicorn worker；依赖精确锁定；GitHub Actions 覆盖 pytest、前端构建与可选的真实链路评测；仓库内置检索 / 阈值 / Agent 三套可复现评测脚本。

## 技术栈（简历用一行）

Python、FastAPI、LangGraph、LangChain、ChromaDB、BM25 / RRF、Cross-Encoder（bge-reranker-v2-m3）、
Redis、MySQL、Vue 3、Element Plus、Docker、GitHub Actions

## 素材清单

已有：

- README 里的 Mermaid 架构图与运行截图/录屏：`screenshots/chat.gif`、`screenshots/chat.png`、`screenshots/home.png`
- 三套评测脚本与结果：`scripts/eval_rag.py`、`scripts/eval_threshold.py`、`scripts/eval_agent.py`，
  明细见 `data/eval_rag_local.json`、`data/eval_threshold_local.json`、`data/eval_agent_local.json`
- 并发压测：`scripts/loadtest.py`（支持普通接口与 `/chat/stream` SSE），流式结果已写入 `data/loadtest_stream.json`
- 订单并发验证：`scripts/verify_oversell.py`（超卖防护 + 幂等键去重，自带清理与退出码）
- 前端 e2e：`ui/e2e/ordering.spec.js`（Playwright，3 条主路径：登录墙、真实流式渲染 + 推荐加购、下单连点幂等；
  本机 3 passed / 46.1s，全部打真实后端）
- 安全回归：`tests/test_security.py`（10 条：登录限流与统一失败文案、JWT 非默认密钥、安全响应头、
  413 体积上限、输入长度约束、CORS 预检），全量 `pytest -q` 88 passed / 1 skipped
- 冷启动预热：`api/main.py` 的 `_warmup_rerank_in_background` / `_warmup_agent_in_background`，
  单测 `tests/test_rerank_warmup.py`（7 条：幂等加载、首推理、失败不抛出、按 provider 开关）；
  实测对比表见 README「冷启动与预热」
- Agent 模型 A/B：`data/eval_agent_snapshot.json`（`qwen3.7-plus-2026-05-26`）/ `data/eval_agent_model_b.json`
  （`qwen-plus`），同一评审模型；由 `scripts/eval_agent.py --model / --judge-model` 生成
- 本地 Rerank 权重支持绝对路径（当前在 `models/bge-reranker-v2-m3`，已加入 `.gitignore`，不随仓库分发）

待补（简历加分项）：

- 公网 Demo 链接：需部署到云主机或内网穿透，当前未做

## 面试口径（避免被追问翻车）

- 所有数字都能用 README「测试与评测」里的命令复现，先说清是**本地 1060 道菜的模拟语料**，不是线上规模。
- 若被追问「为什么 Top-3 只有 0.9060 而不是 0.99」：旧集是 153 道菜、标注以菜名直查为主，存在指标饱和；
  扩到 1060 道并补进口语化、属性约束与易混菜干扰项后，0.9060 才是可信的水平，两组不可直接比较。
- 拒绝「刷指标」的说法：`attribute` 类按共享属性放宽判定可到 Top-3 0.8421，但该口径只作为诊断，不写进对外指标。
- 主动交代失败项：有 3 条口语 query（「那种红亮亮的甜口红烧肉」等）在扩语料后从 Top-1 掉到 Top-3 未命中，
  原因是 Rerank 给近义菜的分数偏低，同时被拒答阈值拦下——这是当前真实短板。
- 拒答命中率 2/3 是关键词启发式，主动说明「帮我写代码」那条回复用了「主要负责…」而非关键词表里的措辞；
  LLM 评审 3/3 才是更可信的口径。
- 多轮地址继承不是靠提示词「求模型记住」，而是 Redis 会话槽位 + 工具参数补齐（`_patch_delivery_calls`），
  这条可以答「你怎么保证多轮稳定性」。
- 压测口径：单机、并发 20、持续 10 秒、每个请求带 Token，`/menu/list` 含鉴权依赖里的 DB 查询；
  改造前的 57.8 QPS 是当时记录，复现需回滚连接池/线程池改造，面试时如实说明。
- 本地 Rerank 是 CPU 推理，单次检索 P50 2.67s / P95 4.42s（含 Embedding 调用），不要把它说成线上延迟；
  换 GPU 或云端 Rerank API 会低一个数量级。
- 流式压测的口径：单 worker、CPU 重排、`qwen3.7-plus`，每档 1 请求/客户端、档位间冷却 65 秒；
  并发 20 时 TTFT P95 17.8s，瓶颈是本地 Rerank 串行排队而不是生成速度（总耗时 P95 仅 56s）。
  不要说成「线上 QPS」，这套数字只说明链路在并发下的退化形状。
- 遇到「你那 429 是不是 bug」时明确回答：是 Redis 每分钟 20 次的对话限流在按预期工作，
  25 并发突发时 5 条被拒，属于设计行为。
- 被问「JWT 安全性」时主动交代：当前是单枚 24h 访问令牌，没有 refresh token 与主动吊销；
  已做的是密钥不复用仓库默认值、登录撞库限流、账号枚举时序抹平，分布式限流应上移到网关。
- 更多问答见 [interview_qa.md](./interview_qa.md)。
