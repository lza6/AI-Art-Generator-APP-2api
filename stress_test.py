"""AI Art Generator 2API 压测脚本（对本地 demo_server 施压，不直连上游）。

用法：
    python stress_test.py                      # 默认 5 并发 x 3 轮
    python stress_test.py -c 8 -n 16           # 8 并发，共 16 次
    python stress_test.py --host 127.0.0.1:8940 --prompt "cyberpunk city"

输出：每请求状态/耗时、p50/p95/max、成功率，退出码非 0 表示存在失败。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor


def one(host: str, prompt: str, idx: int) -> dict:
    body = json.dumps({"model": "aiart-generator-v1", "prompt": f"{prompt} #{idx}",
                       "n": 1, "size": "1024x1024", "response_format": "url"}).encode()
    req = urllib.request.Request(f"http://{host}/v1/images/generations", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
        dt = time.time() - t0
        ok = bool(payload.get("data"))
        return {"ok": ok, "seconds": dt, "url": (payload.get("data") or [{}])[0].get("url"),
                "err": "" if ok else json.dumps(payload)[:200]}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "seconds": time.time() - t0, "url": None,
                "err": f"HTTP {exc.code}: {exc.read()[:200].decode('utf-8', 'replace')}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "seconds": time.time() - t0, "url": None, "err": repr(exc)[:200]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-c", "--concurrency", type=int, default=5)
    ap.add_argument("-n", "--total", type=int, default=15)
    ap.add_argument("--host", default="127.0.0.1:8940")
    ap.add_argument("--prompt", default="a serene lake with mountains, 4k, highly detailed")
    args = ap.parse_args()

    print(f"[stress] host={args.host} concurrency={args.concurrency} total={args.total}", flush=True)
    t_start = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(one, args.host, args.prompt, i) for i in range(args.total)]
        for i, fut in enumerate(futures, 1):
            r = fut.result()
            results.append(r)
            print(f"  #{i:02d} {'OK ' if r['ok'] else 'ERR'} {r['seconds']:6.2f}s "
                  f"{(r['url'] or r['err'])[:96]}", flush=True)

    wall = time.time() - t_start
    lat = sorted(r["seconds"] for r in results)
    ok = [r for r in results if r["ok"]]
    p50 = statistics.median(lat) if lat else 0
    p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else 0
    print("\n===== summary =====")
    print(f"requests     : {len(results)}")
    print(f"success      : {len(ok)} ({len(ok) / len(results) * 100:.1f}%)")
    print(f"wall         : {wall:.2f}s")
    print(f"throughput   : {len(ok) / wall * 60:.2f} img/min")
    print(f"latency p50  : {p50:.2f}s")
    print(f"latency p95  : {p95:.2f}s")
    print(f"latency max  : {max(lat):.2f}s" if lat else "")
    errs = [r["err"] for r in results if not r["ok"]]
    if errs:
        print(f"errors       : {len(errs)}, first: {errs[0][:200]}")
    return 0 if len(ok) == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
