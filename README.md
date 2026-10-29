# AI Art Generator 1.4.7 → 2API

把 `ai.art.generator.paint.draw.photo`（AI Art Generator，v1.4.7）的文生图能力还原成
可直接调用的 HTTP 接口。**纯协议直连，不需要 APK / 真机 / Frida / 代理池。**

## 快速开始

```bash
# 依赖：Python 3.9+，无第三方包
cd 2api
python demo_server.py                     # 默认 :8940；AAG_PORT=8945 可改
# 浏览器打开 http://127.0.0.1:8945/
```

```bash
# OpenAI 兼容调用
curl -X POST http://127.0.0.1:8945/v1/images/generations \
  -H 'Content-Type: application/json' \
  -d '{"model":"aiart-style-5","prompt":"a red fox in snowy forest, 4k","n":1,"size":"1024x1024"}'
```

```json
{"created":1793286477,
 "data":[{"url":"https://storage.googleapis.com/ai-resource-bucket/art_generator_andr/target_file/<uuid>.jpeg",
          "revised_prompt":"a red fox in snowy forest, 4k","request_id":"..."}],
 "usage":{"images":1,"style":"5","proportion":"1:1"}}
```

## 文件

| 文件 | 说明 |
|---|---|
| `client.py` | 协议客户端：头鉴权 + body 鉴权 + `creating`/`getResult` 轮询。内置官方签名证书 |
| `demo_server.py` | Demo HTTP 服务：OpenAI 兼容 `/v1/images/generations`、`/api/styles`、`/health`、`/stats` + 内置 demo 页面 |
| `stress_test.py` | 压测脚本（对本地 demo_server 施压） |
| `docs/PROTOCOL.md` | 完整协议还原：鉴权算法、端点表、md5 拼接顺序、限额实测 |
| `cert.txt` | 官方 APK 签名证书 DER hex（1.4.7） |

## 环境变量

| 变量 | 默认 | 用途 |
|---|---|---|
| `AAG_PORT` | `8940` | demo 端口 |
| `AAG_POOL_SIZE` | `8` | uid 池大小（额度按 uid 计） |
| `AAG_WORKERS` | `16` | 生成并发 |
| `AAG_TASK_TIMEOUT` | `180` | 单任务轮询上限（秒） |
| `AAG_CERT_HEX` | 内置 | 覆盖签名证书 hex（换版本时用） |
| `AAG_BASE` | `https://aga.coolsummerdev.com/` | 上游地址 |

## 实测数据（2026-10-29）

- 单 uid 连续提交 25 次：25/25 成功，未见限额阻断
- 5 并发 × 10 次：**10/10 成功**，wall 93.97s，p50 46.94s，p95 67.61s，**6.38 img/min**
- 输出：真实 JPEG ~500KB，`storage.googleapis.com` 直链可下载

## new-api 对接

把 `client.py` 封装成渠道：`model` → `aiart-style-<s_id>`，`size` → `proportion`
（`1024x1024`→`1:1`，`768x1344`→`9:16`，`1344x768`→`16:9`），
`n` 上限 4，返回 `url` 直链即可。上游单张 25–70s，渠道超时需 ≥ 180s。

细节见 `docs/PROTOCOL.md`。
