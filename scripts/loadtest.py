"""AiMenu HTTP 压测脚本（基于 httpx，无需额外依赖）

支持两类目标：

1. 普通 GET 接口（默认）：登录拿 Bearer Token 后，以固定并发持续请求，输出 QPS 与延迟分位数；
2. SSE 流式接口 `/chat/stream`：POST 一个 query，记录首字延迟（TTFT）、总耗时、
   输出 token 数与 tokens/s、错误率，并单独统计 429（触发对话限流）的次数。

示例：
    # 普通接口
    python scripts/loadtest.py --concurrency 20 --duration 15 --paths /healthy,/menu/list

    # 流式对话：并发 5/10/20 依次压，每档每个客户端发 1 个请求
    python scripts/loadtest.py --stream --path /chat/stream \
        --queries "推荐几个川菜,四十块以内的下饭菜,有什么清淡不辣的汤" \
        --sweep 5,10,20 --requests-per-worker 1 --cooldown 61

说明：
- 默认使用 admin / password123 登录（可用 --username/--password 覆盖）；
- 每个请求都会带 Token，因此 /menu/list 的耗时包含鉴权依赖中的数据库查询；
- `/chat/stream` 每次请求都会真实消耗 LLM 与 Rerank 额度，务必用 --requests-per-worker 限制总量；
- 对话接口默认有每分钟 20 次的限流（CHAT_RATE_LIMIT_PER_MINUTE），并发 20 时会看到 429，
  这是预期结果而非脚本缺陷；用 --cooldown 让各档位落在不同的限流窗口内。
"""
import argparse
import asyncio
import json
import statistics
import time

import httpx


async def login(client: httpx.AsyncClient, username: str, password: str) -> str:
    resp = await client.post(
        "/auth/login",
        data={"username": username, "password": password},
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def percentile(values, ratio):
    """线性插值分位数，与 scripts/eval_rag.py 保持一致"""
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = (len(ordered) - 1) * ratio
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


async def worker(client, token, path, duration, latencies, errors, stop_at):
    """普通接口：在 duration 内尽可能多地发 GET 请求"""
    headers = {"Authorization": f"Bearer {token}"}
    while time.perf_counter() < stop_at:
        start = time.perf_counter()
        try:
            resp = await client.get(path, headers=headers)
            elapsed = time.perf_counter() - start
            if resp.status_code >= 400:
                errors.append(f"{path} -> HTTP {resp.status_code}")
            else:
                latencies[path].append(elapsed)
        except Exception as exc:  # 网络异常也要计入错误，避免压测静默失败
            errors.append(f"{path} -> {exc}")


def _extract_sse(line):
    """从 `data: {...}` 行里取出 JSON，非数据行返回 None"""
    if not line.startswith("data:"):
        return None
    raw = line[5:].strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


async def stream_worker(client, token, path, queries, rounds, metrics, errors, worker_id):
    """流式接口：按 rounds 发有限个请求，记录 TTFT / 总耗时 / tokens"""
    headers = {"Authorization": f"Bearer {token}"}
    for index in range(rounds):
        query = queries[(worker_id + index) % len(queries)]
        body = {"query": query, "session_id": f"load-{int(time.time())}-{worker_id}-{index}"}
        started = time.perf_counter()
        ttft = None
        chars = 0
        chunks = 0
        usage = {}
        status = None
        try:
            async with client.stream("POST", path, json=body, headers=headers) as resp:
                status = resp.status_code
                if resp.status_code >= 400:
                    await resp.aread()
                    errors.append({"status": resp.status_code, "query": query})
                    continue
                async for line in resp.aiter_lines():
                    payload = _extract_sse(line)
                    if not payload:
                        continue
                    kind = payload.get("type")
                    if kind == "content":
                        if ttft is None:
                            ttft = time.perf_counter() - started
                        chunks += 1
                        chars += len(payload.get("content") or "")
                    elif kind == "usage":
                        usage = payload.get("usage") or {}
                    elif kind == "error":
                        errors.append({"status": "sse-error", "query": query,
                                       "detail": str(payload.get("content"))[:80]})
                        break
        except Exception as exc:
            errors.append({"status": "network", "query": query, "detail": str(exc)[:80]})
            continue

        total = time.perf_counter() - started
        if status is not None and status >= 400:
            continue
        if ttft is None:
            # 没有内容块（例如纯工具调用后直接结束）也要记录，避免样本静默丢失
            errors.append({"status": "empty-stream", "query": query})
            continue
        metrics.append({
            "query": query,
            "ttft": ttft,
            "total": total,
            "chars": chars,
            "chunks": chunks,
            "usage": usage,
        })


def _stream_summary(metrics, elapsed, error_list):
    if not metrics:
        return {"requests": 0, "errors": len(error_list)}
    ttfts = [item["ttft"] for item in metrics]
    totals = [item["total"] for item in metrics]
    with_usage = [item for item in metrics if item["usage"].get("output_tokens")]
    output_tokens = [item["usage"]["output_tokens"] for item in with_usage]
    total_tokens = [item["usage"]["total_tokens"] for item in with_usage]
    gen_span = [item["total"] - item["ttft"] for item in metrics]
    throughput = [
        token / span for token, span in zip(output_tokens, gen_span) if span > 0
    ]
    return {
        "requests": len(metrics),
        "errors": len(error_list),
        "rate_limited": sum(1 for item in error_list if item["status"] == 429),
        "qps": round(len(metrics) / elapsed, 2) if elapsed else 0.0,
        "ttft_p50_ms": round(percentile(ttfts, 0.50) * 1000, 1),
        "ttft_p95_ms": round(percentile(ttfts, 0.95) * 1000, 1),
        "total_p50_ms": round(percentile(totals, 0.50) * 1000, 1),
        "total_p95_ms": round(percentile(totals, 0.95) * 1000, 1),
        "chars_avg": round(statistics.mean(item["chars"] for item in metrics), 1),
        "output_tokens_avg": round(statistics.mean(output_tokens), 1) if output_tokens else None,
        "total_tokens_avg": round(statistics.mean(total_tokens), 1) if total_tokens else None,
        "tokens_per_sec_avg": round(statistics.mean(throughput), 1) if throughput else None,
        "usage_samples": len(output_tokens),
    }


def _plain_summary(args, total_requests, total_elapsed, latencies, errors):
    """把普通接口压测结果整理成可直接入库的 JSON 结构"""
    paths = {}
    for path, values in latencies.items():
        if not values:
            paths[path] = {"requests": 0}
            continue
        paths[path] = {
            "requests": len(values),
            "qps": round(len(values) / total_elapsed, 1) if total_elapsed else 0.0,
            "mean_ms": round(statistics.mean(values) * 1000, 1),
            "p50_ms": round(percentile(values, 0.50) * 1000, 1),
            "p95_ms": round(percentile(values, 0.95) * 1000, 1),
            "p99_ms": round(percentile(values, 0.99) * 1000, 1),
            "max_ms": round(max(values) * 1000, 1),
        }
    return {
        "mode": "plain",
        "base_url": args.base_url,
        "concurrency": args.concurrency,
        "duration_s": round(total_elapsed, 2),
        "total_requests": total_requests,
        "qps": round(total_requests / total_elapsed, 1) if total_elapsed else 0.0,
        "errors": len(errors),
        "paths": paths,
        "error_samples": errors[:5],
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


async def run_plain(args):
    async with httpx.AsyncClient(base_url=args.base_url, timeout=args.timeout) as client:
        token = await login(client, args.username, args.password)
        paths = [item.strip() for item in args.paths.split(",") if item.strip()]

        latencies = {path: [] for path in paths}
        errors = []
        start = time.perf_counter()
        stop_at = start + args.duration

        await asyncio.gather(*[
            worker(client, token, paths[i % len(paths)], args.duration, latencies, errors, stop_at)
            for i in range(args.concurrency)
        ])

        total_elapsed = time.perf_counter() - start

    total_requests = sum(len(values) for values in latencies.values())
    print(f"并发={args.concurrency} 时长={total_elapsed:.1f}s 总请求={total_requests} "
          f"错误={len(errors)} 整体QPS={total_requests / total_elapsed:.1f}")
    for path, values in latencies.items():
        if not values:
            print(f"  {path:<16} 无成功请求")
            continue
        print(
            f"  {path:<16} 请求={len(values):<6} QPS={len(values) / total_elapsed:7.1f} "
            f"均值={statistics.mean(values) * 1000:7.1f}ms "
            f"P50={percentile(values, 0.50) * 1000:7.1f}ms "
            f"P95={percentile(values, 0.95) * 1000:7.1f}ms "
            f"P99={percentile(values, 0.99) * 1000:7.1f}ms "
            f"最大={max(values) * 1000:7.1f}ms"
        )
    if errors:
        print(f"  错误样例: {errors[:5]}")

    if args.json_path:
        summary = _plain_summary(args, total_requests, total_elapsed, latencies, errors)
        with open(args.json_path, "w", encoding="utf-8") as fp:
            json.dump(summary, fp, ensure_ascii=False, indent=2)
        print(f"结果已写入: {args.json_path}")


async def run_stream_level(args, concurrency):
    queries = [item.strip() for item in args.queries.split(",") if item.strip()]
    timeout = httpx.Timeout(args.timeout, read=args.stream_timeout)
    async with httpx.AsyncClient(base_url=args.base_url, timeout=timeout) as client:
        token = await login(client, args.username, args.password)
        metrics = []
        errors = []
        start = time.perf_counter()
        await asyncio.gather(*[
            stream_worker(client, token, args.path, queries,
                          args.requests_per_worker, metrics, errors, i)
            for i in range(concurrency)
        ])
        elapsed = time.perf_counter() - start

    summary = _stream_summary(metrics, elapsed, errors)
    print(
        f"并发={concurrency:<3} 请求={summary['requests']:<3} 错误={summary['errors']} "
        f"(429={summary.get('rate_limited', 0)}) QPS={summary.get('qps', 0)}"
    )
    if summary["requests"]:
        print(
            f"  TTFT P50={summary['ttft_p50_ms']}ms P95={summary['ttft_p95_ms']}ms | "
            f"总耗时 P50={summary['total_p50_ms']}ms P95={summary['total_p95_ms']}ms"
        )
        print(
            f"  输出 {summary['chars_avg']} 字符/次 | "
            f"tokens 输入+输出均值={summary['total_tokens_avg']} "
            f"(输出 {summary['output_tokens_avg']}, 样本 {summary['usage_samples']}/{summary['requests']}) | "
            f"tokens/s={summary['tokens_per_sec_avg']}"
        )
    if errors:
        print(f"  错误样例: {errors[:5]}")
    return summary


async def run_stream(args):
    levels = [int(item) for item in args.sweep.split(",") if item.strip()] \
        if args.sweep else [args.concurrency]

    if args.warmup:
        print("预热：跑 1 次对话，避免模型冷启动拉高 P95 …")
        await run_stream_level(
            argparse.Namespace(**{**vars(args), "sweep": "", "concurrency": 1,
                                  "requests_per_worker": 1}), 1)
        print("-" * 68)

    results = {}
    for index, level in enumerate(levels):
        if index and args.cooldown:
            await asyncio.sleep(args.cooldown)
        results[level] = await run_stream_level(args, level)
    if len(results) > 1:
        print("=" * 68)
        print("并发 | 请求 | 错误 | 429 | TTFT P50 | TTFT P95 | 总耗时 P95 | tokens/s")
        for level, summary in results.items():
            print(
                f"{level:<4} | {summary['requests']:<4} | {summary['errors']:<4} | "
                f"{summary.get('rate_limited', 0):<3} | "
                f"{summary.get('ttft_p50_ms', 0):>8} | {summary.get('ttft_p95_ms', 0):>8} | "
                f"{summary.get('total_p95_ms', 0):>10} | {summary.get('tokens_per_sec_avg')}"
            )
    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as fp:
            json.dump({str(k): v for k, v in results.items()}, fp, ensure_ascii=False, indent=2)
        print(f"结果已写入: {args.json_path}")


def main():
    parser = argparse.ArgumentParser(description="AiMenu 接口压测")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="password123")
    parser.add_argument("--paths", default="/healthy,/menu/list", help="逗号分隔的 GET 接口")
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--duration", type=float, default=10.0, help="压测时长（秒）")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--stream", action="store_true", help="压 SSE 流式接口")
    parser.add_argument("--path", default="/chat/stream", help="流式接口路径")
    parser.add_argument(
        "--queries",
        default="推荐几个川菜,四十块以内的下饭菜,有什么清淡不辣的汤,你们有帝王蟹吗,今天武汉天气怎么样",
        help="轮换使用的 query，逗号分隔",
    )
    parser.add_argument("--sweep", default="", help="并发梯度，如 5,10,20；为空则只跑 --concurrency")
    parser.add_argument("--requests-per-worker", type=int, default=1,
                        help="流式模式下每个客户端发几个请求，控制 LLM 消耗")
    parser.add_argument("--cooldown", type=float, default=0.0,
                        help="档位之间的间隔秒数，用于避开每分钟限流窗口")
    parser.add_argument("--warmup", type=int, default=1, help="是否先跑 1 次预热（1/0）")
    parser.add_argument("--stream-timeout", type=float, default=180.0, help="流式读取超时（秒）")
    parser.add_argument("--json", dest="json_path",
                        help="结果写入 JSON：普通接口为汇总，流式模式为各并发档位")
    args = parser.parse_args()

    if args.stream:
        asyncio.run(run_stream(args))
    else:
        asyncio.run(run_plain(args))


if __name__ == "__main__":
    main()
