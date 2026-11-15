"""AiMenu HTTP 压测脚本（基于 httpx，无需额外依赖）

先登录拿到 Bearer Token，再以固定并发持续请求指定接口，输出 QPS 与延迟分位数。

示例：
    python scripts/loadtest.py --base-url http://127.0.0.1:8001 \
        --concurrency 20 --duration 15 --paths /healthy,/menu/list

说明：
- 默认使用 admin / password123 登录（可用 --username/--password 覆盖）；
- 每个请求都会带 Token，因此 /menu/list 的耗时包含鉴权依赖中的数据库查询；
- 结果只反映本机环境，请在同机、同并发下对比改造前后。
"""
import argparse
import asyncio
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
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(int(len(ordered) * ratio), len(ordered) - 1)
    return ordered[index]


async def worker(client, token, path, duration, latencies, errors, stop_at):
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


async def run(args):
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


def main():
    parser = argparse.ArgumentParser(description="AiMenu 接口压测")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="password123")
    parser.add_argument("--paths", default="/healthy,/menu/list", help="逗号分隔的 GET 接口")
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--duration", type=float, default=10.0, help="压测时长（秒）")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
