"""AI Art Generator 2API demo server (OpenAI images 兼容 + 原生端点)。

启动：
    python demo_server.py            # 默认 :8940
    AAG_PORT=8940 python demo_server.py

端点：
    GET  /                     demo 页面（内嵌，无需静态资源）
    GET  /health               健康检查
    GET  /stats                运行统计（请求数 / 成功 / 失败 / 平均耗时 / uid 池）
    GET  /v1/models            OpenAI 风格模型列表
    POST /v1/images/generations OpenAI 兼容文生图（prompt / n / size / model）
    POST /api/styles           风格列表（上游 getStyleList 透传）
    POST /api/tips             提示词灵感（上游 tips 透传）
"""

from __future__ import annotations

import json
import os
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import client as aag

PORT = int(os.environ.get("AAG_PORT", "8940"))
POOL_SIZE = int(os.environ.get("AAG_POOL_SIZE", "8"))       # 预创建 uid 数
WORKERS = int(os.environ.get("AAG_WORKERS", "16"))          # 生成并发
TASK_TIMEOUT = float(os.environ.get("AAG_TASK_TIMEOUT", "180"))

MODEL_ID = "aiart-generator-v1"


class UidPool:
    """uid 池：服务端免费额度按 uid 计，轮换即可横向扩容。"""

    def __init__(self, size: int):
        self._lock = threading.Lock()
        self._clients: list[aag.AagClient] = [aag.AagClient() for _ in range(max(1, size))]
        self._cursor = 0
        self.used: dict[str, int] = {c.uid: 0 for c in self._clients}

    def next(self) -> aag.AagClient:
        with self._lock:
            cli = self._clients[self._cursor % len(self._clients)]
            self._cursor += 1
        return cli

    def mark(self, cli: aag.AagClient) -> None:
        with self._lock:
            self.used[cli.uid] = self.used.get(cli.uid, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"size": len(self._clients), "used": dict(self.used),
                    "uids": [c.uid for c in self._clients]}


POOL = UidPool(POOL_SIZE)
EXECUTOR = ThreadPoolExecutor(max_workers=WORKERS)
STATS_LOCK = threading.Lock()
STATS = {"requests": 0, "ok": 0, "failed": 0, "total_seconds": 0.0, "last_error": ""}


def stats_add(ok: bool, seconds: float, error: str = "") -> None:
    with STATS_LOCK:
        STATS["requests"] += 1
        STATS["total_seconds"] += seconds
        if ok:
            STATS["ok"] += 1
        else:
            STATS["failed"] += 1
            STATS["last_error"] = error[:500]


def stats_snapshot() -> dict[str, Any]:
    with STATS_LOCK:
        snap = dict(STATS)
    avg = snap["total_seconds"] / snap["requests"] if snap["requests"] else 0.0
    snap["avg_seconds"] = round(avg, 3)
    snap["success_rate"] = round(snap["ok"] / snap["requests"], 4) if snap["requests"] else 0.0
    return snap


def size_to_proportion(size: str) -> tuple[str, str]:
    """OpenAI size -> (上游 proportion, 展示尺寸)。上游只认档位比例。"""
    table = {
        "1024x1024": ("1:1", "1024x1024"),
        "512x512": ("1:1", "1024x1024"),
        "1024x1792": ("9:16", "768x1344"),
        "1792x1024": ("16:9", "1344x768"),
        "768x1344": ("9:16", "768x1344"),
        "1344x768": ("16:9", "1344x768"),
    }
    return table.get(size, ("1:1", "1024x1024"))


def style_from_model(model: str, fallback: int = 1) -> str:
    """model 支持 "aiart-generator-v1" 或 "aiart-style-<id>"。"""
    if model.startswith("aiart-style-"):
        return model.split("aiart-style-", 1)[1]
    return str(fallback)


def generate_one(prompt: str, style: str, proportion: str) -> dict[str, Any]:
    cli = POOL.next()
    data = cli.generate(prompt, style, proportion, timeout=TASK_TIMEOUT)
    POOL.mark(cli)
    return {"url": data.get("url"), "style": data.get("style"), "proportion": data.get("proportion"),
            "request_id": data.get("request_id"), "uid": cli.uid}


DEMO_HTML = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Art Generator 2API — Demo</title>
<style>
:root{color-scheme:dark}
body{margin:0;font:15px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;background:#0b0d12;color:#e6e8ee}
.wrap{max-width:1080px;margin:0 auto;padding:28px 20px 60px}
h1{font-size:22px;margin:0 0 4px}
.sub{color:#8b93a7;font-size:13px;margin-bottom:22px}
.card{background:#141822;border:1px solid #232a38;border-radius:12px;padding:18px;margin-bottom:18px}
label{display:block;font-size:12px;color:#8b93a7;margin:10px 0 5px;text-transform:uppercase;letter-spacing:.04em}
input,select,textarea{width:100%;box-sizing:border-box;background:#0b0d12;border:1px solid #2a3242;color:#e6e8ee;border-radius:8px;padding:10px;font:inherit}
textarea{min-height:78px;resize:vertical}
.row{display:flex;gap:12px;flex-wrap:wrap}.row>*{flex:1;min-width:180px}
button{background:#4f7cff;color:#fff;border:0;border-radius:8px;padding:11px 18px;font:inherit;font-weight:600;cursor:pointer;margin-top:14px}
button:disabled{opacity:.5;cursor:progress}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px;margin-top:16px}
.grid img{width:100%;border-radius:10px;display:block;background:#1b2130}
.meta{font-size:12px;color:#8b93a7;word-break:break-all;margin-top:6px}
pre{background:#0b0d12;border:1px solid #232a38;border-radius:8px;padding:12px;overflow:auto;font-size:12px;max-height:260px}
.tag{display:inline-block;background:#1c2434;border:1px solid #2a3242;border-radius:99px;padding:2px 10px;font-size:12px;color:#9aa3b8;margin:2px 4px 2px 0;cursor:pointer}
.tag:hover{border-color:#4f7cff;color:#cfe0ff}
#msg{font-size:13px;color:#ffb4b4;min-height:18px;margin-top:10px}
</style></head><body><div class="wrap">
<h1>AI Art Generator 1.4.7 → 2API</h1>
<div class="sub">纯协议直连 <code>aga.coolsummerdev.com</code>，无需 APK / 真机。OpenAI images 兼容端点：<code>POST /v1/images/generations</code></div>

<div class="card">
  <label>模型 / 风格</label>
  <select id="model"></select>
  <div id="styles" style="margin-top:8px"></div>
  <label>提示词</label>
  <textarea id="prompt">a majestic snow mountain at golden hour, ultra detailed, 4k</textarea>
  <div class="row">
    <div><label>尺寸</label><select id="size">
      <option>1024x1024</option><option>768x1344</option><option>1344x768</option>
    </select></div>
    <div><label>数量 n</label><input id="n" type="number" value="1" min="1" max="4"></div>
  </div>
  <button id="go">生成</button>
  <div id="msg"></div>
  <div class="grid" id="out"></div>
</div>

<div class="card">
  <label>请求 / 响应</label>
  <pre id="log">等待请求…</pre>
</div>

<script>
const $ = s => document.querySelector(s);
async function j(u, o) { const r = await fetch(u, o); return r.json(); }
async function init() {
  try {
    const m = await j('/v1/models');
    $('#model').innerHTML = m.data.map(x => `<option value="${x.id}">${x.id}</option>`).join('');
    const s = await j('/api/styles', {method:'POST'});
    const list = (s.data && s.data.styleList) || [];
    $('#styles').innerHTML = list.slice(0, 24).map(x =>
      `<span class="tag" data-id="${x.s_id}">${x.style} #${x.s_id}</span>`).join('');
    document.querySelectorAll('.tag').forEach(t => t.onclick = () => {
      $('#model').innerHTML = `<option value="aiart-style-${t.dataset.id}">aiart-style-${t.dataset.id}</option>`;
    });
  } catch (e) { $('#msg').textContent = '初始化失败: ' + e; }
}
$('#go').onclick = async () => {
  const btn = $('#go'); btn.disabled = true; $('#msg').textContent = '';
  const body = {
    model: $('#model').value || 'aiart-generator-v1',
    prompt: $('#prompt').value,
    n: parseInt($('#n').value || '1', 10),
    size: $('#size').value,
    response_format: 'url'
  };
  $('#log').textContent = '> POST /v1/images/generations\n' + JSON.stringify(body, null, 2);
  const t0 = performance.now();
  try {
    const r = await j('/v1/images/generations', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
    });
    const dt = ((performance.now() - t0) / 1000).toFixed(2);
    $('#log').textContent += `\n\n< ${dt}s\n` + JSON.stringify(r, null, 2);
    if (r.data) {
      $('#out').innerHTML = r.data.map(d =>
        `<div><img src="${d.url}" alt=""><div class="meta">${d.revised_prompt || ''}<br>${d.url}</div></div>`).join('');
    } else { $('#msg').textContent = JSON.stringify(r); }
  } catch (e) { $('#msg').textContent = '失败: ' + e; }
  btn.disabled = false;
};
init();
</script></div></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "aag2api/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:  # 静默默认日志
        pass

    def _send(self, code: int, payload: Any, ctype: str = "application/json") -> None:
        body = payload.encode("utf-8") if isinstance(payload, str) else json.dumps(
            payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if "json" in ctype or "html" in ctype else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._send(200, DEMO_HTML, "text/html")
        if path == "/health":
            return self._send(200, {"status": "ok", "pool": POOL.snapshot()["size"],
                                    "workers": WORKERS, "ts": int(time.time())})
        if path == "/stats":
            snap = stats_snapshot()
            snap["pool"] = POOL.snapshot()
            return self._send(200, snap)
        if path == "/v1/models":
            return self._send(200, {"object": "list", "data": [
                {"id": MODEL_ID, "object": "model", "owned_by": "aag-2api"}]})
        self._send(404, {"error": {"message": f"no route {path}"}})

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        body = self._read_json()
        try:
            if path == "/v1/images/generations":
                return self._images(body)
            if path == "/api/styles":
                return self._send(200, {"data": {"styleList": POOL.next().style_list()}})
            if path == "/api/tips":
                cli = POOL.next()
                return self._send(200, {"data": {"prompt": cli.tips()}})
            self._send(404, {"error": {"message": f"no route {path}"}})
        except aag.AagError as exc:
            stats_add(False, 0.0, str(exc))
            self._send(502, {"error": {"message": str(exc), "ret": exc.ret}})
        except Exception as exc:  # noqa: BLE001 - 兜底，避免线程静默崩
            stats_add(False, 0.0, repr(exc))
            traceback.print_exc()
            self._send(500, {"error": {"message": repr(exc)}})

    def _images(self, body: dict[str, Any]) -> None:
        t0 = time.time()
        prompt = (body.get("prompt") or "").strip()
        if not prompt:
            return self._send(400, {"error": {"message": "prompt is required"}})
        n = max(1, min(int(body.get("n") or 1), 4))
        proportion, _ = size_to_proportion(str(body.get("size") or "1024x1024"))
        style = style_from_model(str(body.get("model") or MODEL_ID))
        futures = [EXECUTOR.submit(generate_one, prompt, style, proportion) for _ in range(n)]
        outs, errors = [], []
        for fut in futures:
            try:
                outs.append(fut.result())
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
        if not outs:
            stats_add(False, time.time() - t0, errors[0] if errors else "unknown")
            return self._send(502, {"error": {"message": errors[0] if errors else "generation failed"}})
        stats_add(True, time.time() - t0)
        self._send(200, {
            "created": int(time.time()),
            "data": [{"url": o["url"], "revised_prompt": prompt, "request_id": o["request_id"]}
                     for o in outs],
            "usage": {"images": len(outs), "style": style, "proportion": proportion},
            "partial_errors": errors,
        })


def main() -> None:
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[aag2api] demo on http://127.0.0.1:{PORT}  pool={POOL_SIZE} workers={WORKERS}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
