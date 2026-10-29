# new-api 对接交接文档（AI Art Generator 1.4.7 → 2API）

> 面向执行会话：本文档自包含，读完即可动手，不需要回看逆向过程。
> 送审对象：`new-api-Max`（Go 网关）+ 我方协议产物仓库 `lza6/AI-Art-Generator-APP-2api`。

---

## 0. 上游事实（已实测，可直接依赖）

| 项 | 值 |
|---|---|
| 上游 | `POST https://aga.coolsummerdev.com/api/v3/*`（Laravel，全部 POST，JSON） |
| 鉴权 | 两层，均为 md5：① 请求头 `MD5 = md5(PACKAGE + 官方APK签名证书DER hex + TIMESTAMP)`；② 请求体 `md5 = md5(<该接口固定字段顺序拼接> + uid + NATIVE_KEY)` |
| 常量 | `PACKAGE = ai.art.generator.paint.draw.photo`、`version = 147`、`NATIVE_KEY = Emoji_JingNCK567`、证书 hex 见 `cert.txt`（1792 字符） |
| uid | 客户端随意生成，`uid = md5(uuid4())`，服务端不校验设备；**额度按 uid 计，池化即横向扩容** |
| 文生图 | `POST /api/v3/creating` → `{uid, request_id}`；`POST /api/v3/getResult` → `{status, url,...}` |
| status | `1`=成功 `2`=失败 `3`=处理中 `4`=排队 `5`=取消 `6`=NSFW |
| 耗时 | 单张 **25–70s**（p50 ≈ 47s），5 并发实测 6.38 img/min，未触发限流 |
| 额度 | 单 uid 连打 25 次全 200，无硬限额；`other_count` 随用量增长 |
| 产出 | `https://storage.googleapis.com/ai-resource-bucket/art_generator_andr/target_file/<uuid>.jpeg`（~500KB JPEG）；**status=1 后需 1–3s 再取图**，过早会 404 |
| style | 必须是 `getStyleList` 里的 `s_id` **字符串**（`"1"`,`"5"`,`"29"`…），传风格名 → `{"ret":0,"msg":"Undefined offset: 0"}` |
| proportion | `1:1` / `9:16` / `16:9` |
| 参考实现 | 仓库 `client.py`（已验证出图）、`demo_server.py`（OpenAI 兼容层）、`docs/PROTOCOL.md`（22 端点全表） |

---

## 1. 两条对接路径（任选其一）

### 路径 A：零 Go 代码（把 demo_server 当中转上游）—— 30 分钟可上线

`demo_server.py` 已暴露 OpenAI images 兼容端点，new-api 侧只需建一个渠道指向它。

1. 起中转（与 new-api 同机或内网可达）：
   ```bash
   cd 2api && AAG_PORT=8945 AAG_POOL_SIZE=8 AAG_WORKERS=16 python demo_server.py
   ```
2. new-api 建渠道：`类型 = OpenAI`，`BaseURL = http://<内网IP>:8945`，`模型 = aiart-generator-v1`（或 `aiart-style-<s_id>`）。
3. 客户端按 OpenAI 图像接口调用：
   ```bash
   curl -X POST http://<new-api>/v1/images/generations \
     -H "Authorization: Bearer <token>" -H 'Content-Type: application/json' \
     -d '{"model":"aiart-style-5","prompt":"a red fox in snow, 4k","n":1,"size":"1024x1024"}'
   ```
   返回体已按 OpenAI 格式：`data[].url` + `usage{images,style,proportion}`。

**优点**：不动 Go、不动前端、不碰计费表；**缺点**：多一跳、计费按 OpenAI 图像默认口径走，不是 2API 2币 口径。

**必做防护**：
- 渠道超时必须 ≥ **180s**（默认值会截断 25–70s 的生成）
- `n ≤ 4`（中转层已 clamp）
- 中转进程要常驻（PowerShell `Start-Process -WindowStyle Hidden`，bash `nohup` 会随会话被杀）
- 上游 500 偶发（`getFilterList` 已知），中转已做 3 次重试

### 路径 B：原生 Go 渠道 `type 74`（推荐，进 2API 计费体系）

按现有 2API 渠道（`imagerator`/`imagenai`/`duck`）同构实现，直接调上游，无中转。

#### B.1 落点清单（仓库内已确认）

| 文件 | 改动 |
|---|---|
| `constant/channel.go` | 新增 `ChannelTypeAAG = 74`（当前最大 `ChannelTypeDuck = 73`）＋ `ChannelTypeNames` 加 `74: "AIArtGenerator"` |
| `constant/api_type.go` | 新增 `APITypeAAG`（与 channel type 同序） |
| `relay/relay_adaptor.go` | import `relay/channel/aag`；`GetAdaptor` switch 加 `case constant.APITypeAAG: return &aag.Adaptor{}` |
| `relay/channel/aag/adaptor.go` | 新建：`GetRequestURL` / `SetupRequestHeader` / `ConvertImageRequest` / `DoResponse` / `GetModelList` / `GetChannelName` |
| `relay/channel/aag/client.go` | 新建：签名 + creating + 轮询（可直译 `client.py`） |
| `service/billing.go` | `isTwoAPIBillingChannel` 的 62–73 列表加入 `ChannelTypeAAG`（**漏这步 → 走常规余额，2币不扣**） |
| `web/src/features/channels/constants.ts` | 渠道类型标签加 74 |
| `web/src/features/pricing/components/twoapi-badge.tsx` | 66/73 白名单加 74，2API 徽章才显示 |

> 参考模板：`relay/channel/imagerator/`（同为"图生图/文生图 2API，同步 relay 出 media url"）。
> `relay/common/raw_prompt.go` 提供 2API 通用自然语言 prompt 提取，可直接复用。

#### B.2 关键实现（可直接抄）

```go
package aag

const (
    baseURL  = "https://aga.coolsummerdev.com/"
    pkgName  = "ai.art.generator.paint.draw.photo"
    version  = "147"
    nativeKey = "Emoji_JingNCK567"   // libcipher-jni.so -> getCipherKeyFromJNI()
    certHex  = "<cert.txt 内容，1792 hex 字符>" // 官方 Google Play 签名证书 DER hex
)

func md5hex(s string) string { sum := md5.Sum([]byte(s)); return hex.EncodeToString(sum[:]) }

// 请求头（第一层）
func authHeaders(uid string, ts int64) map[string]string {
    t := strconv.FormatInt(ts, 10)
    return map[string]string{
        "Content-Type": "application/json",
        "TIMESTAMP": t,
        "MD5": md5hex(pkgName + certHex + t),
        "PACKAGE": pkgName,
        "uid": uid,
        "version": version,
        "simCountryCode": "US",
        "localeCountryCode": "US",
    }
}

// 创建任务（第二层：字段顺序固定，未传字段按空串）
// md5 = md5(is_sub + localeCountryCode + orderId + productId + prompt + proportion
//           + purchaseToken + purchaseType + simCountryCode + strength + style + uid + KEY)
func (c *Client) Create(uid, prompt, style, proportion string) (string, error) {
    sig := "0" + "US" + "" + "" + prompt + proportion + "" + "" + "US" + "" + style + uid + nativeKey
    body := map[string]any{
        "prompt": prompt, "proportion": proportion, "style": style, "uid": uid,
        "strength": "", "simCountryCode": "US", "localeCountryCode": "US", "is_sub": "0",
        "orderId": "", "productId": "", "purchaseToken": "", "purchaseType": "",
        "md5": md5hex(sig),
    }
    // POST api/v3/creating -> data.request_id
}

// 结果：md5 = md5(request_id + uid + KEY)
func (c *Client) Result(uid, rid string) (*Result, error) { /* POST api/v3/getResult */ }
```

#### B.3 请求映射

| new-api 入参 | 上游 |
|---|---|
| `dto.ImageRequest.Model` | `aiart-style-<s_id>` → `style = s_id`；不带 style 后缀则默认 `"1"` |
| `dto.ImageRequest.Prompt` | `prompt`（原样，长度上限见 §4） |
| `dto.ImageRequest.Size` | `1024x1024 → 1:1`、`768x1344/1024x1792 → 9:16`、`1344x768/1792x1024 → 16:9`；未知值回落 `1:1` |
| `dto.ImageRequest.N` | `1–4`，多张 = 多次 `creating`（上游一次一张），并发起跑再聚合 |

#### B.4 响应与错误

```go
// 成功：status==1 且有 url → 回 OpenAI image 格式
{"created":<ts>,"data":[{"url":"<storage url>","revised_prompt":prompt}]}

// 失败映射（上游 ret）
40101 → 签名/证书问题（渠道配置错，应停渠道而非重试）
40103 → 缺少 body 鉴权字段（实现 bug）
40104 → Invalid MD5（拼接顺序写错，最常见）
40109 → Invalid style（style 传了风格名而非 s_id）
-2/-3 → status∈{2,6,5} 或轮询超时 → 502，可重试
```

#### B.5 计费

按 2API 口径：命中 `isTwoAPIBillingChannel` 后走 2币 结算，单次一张 = 一次调用。
参考 `aivg` 渠道的落账路径（`relay.QuotaClamp` 不涉及倍率，因为是固定 2币/次）。

---

## 2. 验收标准（必须逐条跑过再算完成）

```bash
# 1) 健康：渠道测试按钮通过
# 2) 单图（默认 style）
curl -X POST $NEWAPI/v1/images/generations -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"model":"aiart-generator-v1","prompt":"a cat","n":1,"size":"1024x1024"}'
# 期望：200 + data[0].url 可 curl -I 到 image/jpeg
# 3) 指定风格
-d '{"model":"aiart-style-5", ...}'         # → usage.style = "5"
# 4) n=4 并发聚合：期望 4 个 url，无 500
# 5) 失败路径：prompt 空 → 400；style 传 "Anime" → 40109 映射成 502 且有日志
# 6) 计费：一次调用 2币 欠账 +¥0.00005（对比调用前后 twoapi 账本）
# 7) 超时：渠道 timeout ≥180s，跑一张 60s 的图不被截断
```

---

## 3. 已知坑（照抄会踩）

1. **别照抄客户端的全字段 body**：上游 md5 按「请求里实际出现的字段」拼接，多传一个非空字段（如 `simCountryCode`）就必须同步进 md5，否则 `Invalid MD5`。只传必要字段最稳。
2. **style 必须是 s_id 字符串**，传风格名 → `Undefined offset: 0`（ret=0，容易被误判成功）。
3. **图片 URL 有 1–3s 落盘延迟**，`status=1` 立刻下载会拿到 Google Storage 的 404 XML，必须重试 3 次。
4. **证书是官方 Play 签名证书**，用重签名（Mod）包的证书算 header MD5 永远 `40101`。
5. `getFilterList` 等端点上游偶发 `HTTP 500`，不要据此判定渠道挂掉。
6. 单 uid 高频无限制，但**不要用单 uid 承载全站**（上游一旦按 uid 拉黑会整片失败），池 ≥8。
7. 不要在并发压测里跑生产主机（AGENTS.md 部署安全条款第 5 条）。

---

## 4. 未覆盖能力（别对外宣称支持）

| 能力 | 状态 |
|---|---|
| 文生图（creating/getResult） | ✅ 可用 |
| Logo/Signature/Tattoo（createJobLogo/getResultLogo） | ⚠️ 签名与调用已通，上游队列 `status=4` 不消费，**无产物** |
| 换脸 reface/create（multipart） | ❌ 签名未对齐，`Invalid MD5`，需 frida 抓包 |
| 滤镜 filter/create（multipart） | ❌ 同上 |

接入时**只暴露文生图模型**，其它路径先不通电，避免用户侧 502。

---

## 5. 需要另外做的事

- `relaykit/` 不涉及（纯图片 relay，无需协议转换）
- 若走路径 A，注意不要把 `demo_server` 暴露公网（无鉴权）
- 任何 schema/迁移无关，本次不涉及数据库改动
