# AI Art Generator 1.4.7 → 2API

把 `ai.art.generator.paint.draw.photo`（AI Art Generator，v1.4.7）的文生图能力还原成可直接调用的
HTTP 接口。**纯协议直连，不需要 APK / 真机 / Frida / 代理池。**

![sample](screenshots_sample.jpeg)

## 快速开始

```bash
# 仅需 Python 3.9+，无第三方依赖
cd 2api
python demo_server.py            # 默认 :8940，AAG_PORT=8945 可改
# 浏览器打开 http://127.0.0.1:8945/
```

```bash
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

## 能力状态

| 能力 | 状态 | 说明 |
|---|---|---|
| 文生图 `creating` + `getResult` | ✅ 已验证 | 真实出图，压测 5 并发 × 10 = 10/10，p50 46.9s |
| `getStyleList` / `tips` / `CheckUserWhite` / `SuccessCount` | ✅ 已验证 | 20 个风格、提示词灵感、额度查询 |
| Logo / Signature / Tattoo | ⚠️ 请求通、无产物 | 上游队列恒 `status=4` 不消费 |
| 换脸 `reface/create`、滤镜 `filter/create`（multipart） | ❌ 未打通 | 服务端 multipart 签名口径未对齐，见 `docs/PROTOCOL.md §6` |

## 文件

| 文件 | 说明 |
|---|---|
| `client.py` | 协议客户端：双层 md5 鉴权 + `generate()/create_logo()/reface_create()/filter_create()`，内置官方签名证书 |
| `demo_server.py` | Demo HTTP 服务：OpenAI 兼容 `/v1/images/generations`、`/api/styles`、`/api/tips`、`/health`、`/stats` + 内嵌 demo 页 |
| `stress_test.py` | 压测脚本（对本地 demo 施压） |
| `docs/PROTOCOL.md` | 完整协议：鉴权算法、22 个端点表、各接口 md5 拼接顺序、限额实测、未打通分支台账 |
| `docs/NEW-API-HANDOFF.md` | **new-api 对接交接文档**：两条落地路径（零代码中转 / 原生渠道 type 74）、落点清单、代码骨架、验收清单、已知坑 |
| `cert.txt` | 官方 APK 签名证书 DER hex（1.4.7） |

## 鉴权（为什么不需要 APK）

1. **请求头**：`MD5 = md5(PACKAGE + 官方签名证书DER的hex + TIMESTAMP)`
   服务端白名单存的是 **Google Play 官方证书** → 用 Mod 重签名包的证书算永远 `40101`。
   官方证书从 Uptodown/APKPure 的 XAPK 里的 base.apk 提取即可（脚本见 `docs/PROTOCOL.md §5`）。
2. **请求体**：`md5 = md5(<该接口固定字段顺序拼接> + uid + NATIVE_KEY)`，
   `NATIVE_KEY = Emoji_JingNCK567`（`libcipher-jni.so` 的 `getCipherKeyFromJNI()`，静态可读）。
3. **uid**：`md5(uuid4())`，服务端不校验设备 → 额度按 uid 计，池化即扩容。

## 环境变量

| 变量 | 默认 | 用途 |
|---|---|---|
| `AAG_PORT` | `8940` | demo 端口 |
| `AAG_POOL_SIZE` | `8` | uid 池大小 |
| `AAG_WORKERS` | `16` | 生成并发 |
| `AAG_TASK_TIMEOUT` | `180` | 单任务轮询上限（秒） |
| `AAG_CERT_HEX` | 内置 | 覆盖签名证书 hex（换版本时用） |
| `AAG_BASE` | `https://aga.coolsummerdev.com/` | 上游地址 |

## 实测数据（2026-10-29）

- 单 uid 连续提交 25 次：25/25 成功，未见限额阻断；直连无需代理
- 5 并发 × 10 次：**10/10 成功**，wall 93.97s，p50 46.94s，p95 67.61s，**6.38 img/min**
- 输出：真实 JPEG ~500KB，`storage.googleapis.com` 直链可下载

## 免责声明

本项目仅用于协议研究与自有网关的技术验证。上游服务条款由原厂定义，请自行评估使用风险；
请勿用于绕过付费墙后的商业分发。
