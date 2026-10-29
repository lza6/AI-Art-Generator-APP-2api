# AI Art Generator 1.4.7 协议还原（PROTOCOL）

- 包名：`ai.art.generator.paint.draw.photo`
- 版本：`1.4.7`（versionCode `147`），Kotlin + Compose，R8 混淆
- 上游：`https://aga.coolsummerdev.com/`（Laravel，Debug 未关，`/home/admin/enServer/htdocs/art_generator_andr/`）
- 同域反馈：`https://feedback.cocomobi.com/`（`api/v2/feedbackinsert`）
- 测试域（内部遗留）：`http://aitest.cocomobi.com:8081/api/v3/home/mapsv2`
- 图存储：`https://storage.googleapis.com/ai-resource-bucket/art_generator_andr/target_file/<uuid>.jpeg`

结论：**纯协议可直连，不需要 APK / 真机 / Frida / 代理**。所有 v3 端点为 `POST`，
请求 JSON，响应 `{"ret":<int>,"msg":str,"data":...}`。

---

## 1. 请求头鉴权（第一层）

由 OkHttp 拦截器 `j/p02z.java` 注入：

| Header | 值 |
|---|---|
| `Content-Type` | `application/json` |
| `TIMESTAMP` | 秒级时间戳字符串 |
| `MD5` | `md5(PACKAGE + 官方签名证书DER的hex + TIMESTAMP)` |
| `PACKAGE` | `ai.art.generator.paint.draw.photo` |
| `uid` | 客户端生成的设备 uid（见下） |
| `version` | `147` |
| `simCountryCode` | SIM/网络国家码，如 `US` |
| `localeCountryCode` | 区域国家码，如 `US` |

- 证书 hex 是 **APK 签名证书 DER 的 `Signature.toCharsString()` 小写 hex**（无分隔符）。
- 服务端**白名单存官方 Google Play 签名证书**，因此必须用官方证书 hex 计算；
  本仓库 `client.py` 已内置 1.4.7 官方证书（1792 hex 字符）。
- 用重签名包（Mod）的证书计算会得到 `{"ret":40101,"msg":"Authentication Failed 1"}`。
- 时间戳与服务器偏差过大时返回 `Authentication Failed 2`，客户端会用响应 `Date` 头校正后重试一次。

### uid 生成（客户端可任意伪造）

`io/bidmachine/v0.java -> a()`：

```
SDK>=26: base = UUID(android_id.hashCode(), MediaDrm("deviceUniqueId").hashCode())
SDK<26 : base = UUID(hash(机型指纹), MediaDrm hash)
uid = md5(base.toString())     # 存入 MMKV PREF_DEFAULT_UID
```

服务端只把它当空间名使用，不做设备校验 → 任意 `md5(uuid4())` 都是合法新用户。

---

## 2. 请求体鉴权（第二层）

除头鉴权外，业务请求体必须带 `uid` + `md5`，其中：

```
md5 = md5( <按接口固定顺序拼接的业务字段值> + uid + NATIVE_KEY )
```

`NATIVE_KEY` 来自 `libcipher-jni.so`：

```
Java_com_cipher_CipherUtils_getCipherKeyFromJNI() => "Emoji_JingNCK567"
```

（`CipherUtils` 的 DES 实现取前 8 字节 `Emoji_Ji`；md5 用的是完整 16 字符串。）

各接口拼接顺序（逆向自 smali，`invokeSuspend` 中的 `StringBuilder.append` 链）：

| 端点 | 拼接顺序 |
|---|---|
| `CheckUserWhite` / `SuccessCount` / 列表类 | `uid + NATIVE_KEY` |
| `creating` | `is_sub + localeCountryCode + orderId + productId + prompt + proportion + purchaseToken + purchaseType + simCountryCode + strength + style + uid + NATIVE_KEY` |
| `getResult` | `request_id + uid + NATIVE_KEY` |
| `createJobLogo` | `brand_name + func + is_sub + localeCountryCode + orderId + productId + prompt + proportion + purchaseToken + purchaseType + scene + simCountryCode + strength + style + uid + NATIVE_KEY` |
| `noticeSub` | `orderId + productId + purchaseToken + purchaseType + uid + NATIVE_KEY` |

实测：**缺失字段按空串参与拼接**，因此空 body 时 `md5(uid+key)` 恰好命中，
一旦带上 `prompt` 等字段就必须按上表顺序重算，否则 `{"ret":40104,"msg":"Invalid MD5"}`。

IAP 相关字段（`orderId/productId/purchaseToken/purchaseType/is_sub`）全部填空串 / `"0"` 即可，
服务端不校验真实订阅（VIP 判定仅返回 `is_sub:false`，不影响生成）。

---

## 3. 端点清单

| 路径 | 关键入参 | 返回 |
|---|---|---|
| `api/v3/CheckUserWhite` | `uid,md5` | `data.is_sub` |
| `api/v3/SuccessCount` | `uid,md5` | `data.{uid,other_count,swap_count}` |
| `api/v3/tips` | `uid,md5` | `data.prompt[]`（提示词灵感） |
| `api/v3/getStyleList` | `uid,md5` | `data.styleList[].{s_id,style,preViewUrl}` |
| `api/v3/getStyleListLogo` | `uid,md5` | Logo/Signature/Tattoo 风格 |
| `api/v3/getInspirationList` | `uid,md5` | 灵感列表 |
| `api/v3/getFilterList` / `filter/styles` | `uid,md5` | 滤镜列表（服务端偶发 500） |
| `api/v3/home/mapsv2` | `uid,md5` | 首页配置 |
| `api/v3/creating` | 见 §2，必需 `style`+`proportion`（string） | `data.{uid,request_id}` |
| `api/v3/getResult` | `request_id` | `data.{status,message,url,...}` |
| `api/v3/cancelJob` | `request_id` | 取消 |
| `api/v3/reface/create` | multipart：`styleId,simCountryCode,localeCountryCode,is_sub,orderId,productId,purchaseToken,purchaseType,md5` + 图片文件 | 换脸任务 |
| `api/v3/filter/create` | 必需 `style_id`（int）等 | 滤镜任务 |
| `api/v3/filter/result` / `filter/cancel` | `request_id` 类 | 滤镜结果 |
| `api/v3/createJobLogo` | 见 §2 | Logo 任务 |
| `api/v3/getResultLogo` | `request_id` | Logo 结果 |
| `api/v3/loginFake` / `loginOutFake` | 未在客户端调用（遗留） | — |
| `api/v3/noticeSub` | IAP 订阅回调 | — |
| `api/v3/uploadDeviceId` | 设备上报 | — |

### `creating` 请求体示例

```json
{
  "prompt": "a cute cat, 8k",
  "proportion": "1:1",
  "style": "5",
  "uid": "<md5 uuid>",
  "strength": "",
  "simCountryCode": "US",
  "localeCountryCode": "US",
  "is_sub": "0",
  "orderId": "", "productId": "", "purchaseToken": "", "purchaseType": "",
  "md5": "<见 §2>"
}
```

- `style` 必须是 **风格 id 的字符串**（`getStyleList` 里的 `s_id`）；传风格名会得到
  `{"ret":0,"msg":"Undefined offset: 0"}`。
- `proportion` 为比例字符串（`1:1` / `9:16` / `16:9`…）。
- 成功返回 `{"ret":200,"data":{"uid":...,"request_id":"<uuid>"}}`。

### `getResult` 返回

```json
{"ret":200,"msg":"success","data":{
  "uid":"...","request_id":"...","status":1,"message":"success",
  "url":"https://storage.googleapis.com/ai-resource-bucket/art_generator_andr/target_file/<uuid>.jpeg"}}
```

`status`：`1`=成功，`2`=失败，`3`=处理中，`4`=排队，`5`=取消，`6`=NSFW。
轮询间隔建议 4s，单张总耗时实测 25–70s。**图片 URL 在 status=1 后可能延迟 1–3 秒才可下载**，
过早请求会拿到 Google Storage 的 404 XML。

---

## 4. 限额与风控实测（2026-10-29）

| 观测 | 结果 |
|---|---|
| 单 uid 连续 `creating` 25 次 | 25/25 返回 200（无限流） |
| `SuccessCount.other_count` | 随使用增长（25 次后 18），未见阻断阈值 |
| 是否需要代理 | 否，直连 `https://aga.coolsummerdev.com/` 正常 |
| 并发 5 × 10 次（经 demo_server） | 10/10 成功，p50 46.9s，p95 67.6s，6.38 img/min |
| 图片可下载 | 是，JPEG ~500KB / 1024 级 |

注意：`getFilterList` 等服务端偶发 `HTTP 500`（Laravel 内部错误），非鉴权问题。

---

## 5. 复现步骤

1. 取官方 APK（Uptodown / APKPure 的 XAPK），提取 base APK 的签名证书 DER：
   ```python
   from androguard.core.apk import APK
   cert = APK("base.apk").get_certificates_der_v3()[0]   # 896 bytes，sha256 e7cb1996...
   open("cert.txt","w").write(cert.hex())
   ```
2. `AAG_CERT_HEX=$(cat cert.txt) python client.py`（或直接改写 `client.py` 里的 `DEFAULT_CERT_HEX`）。
3. 跑 `python demo_server.py`，浏览器打开 `http://127.0.0.1:8945/`。

---

## 6. 附加分支实测状态（2026-10-29）

| 分支 | 状态 | 证据 |
|---|---|---|
| 文生图 `creating` + `getResult` | ✅ 已打通 | 生成真实 JPEG，压测 10/10 |
| `createJobLogo` + `getResultLogo` | ⚠️ 请求通过、任务不消费 | 创建返回 `request_id`，轮询 80s 恒为 `status=4 queuing`，后台队列不产出 |
| `reface/create`（换脸，multipart） | ❌ 签名未对齐 | JSON 形式 `md5 = md5(style_id + uid + key)` 可过签名（返回 `Missing files`），一旦走 multipart 无论怎样组合文件 md5 / 文件名 / 字段顺序都返回 `Invalid MD5`，需动态抓包确认 |
| `filter/create`（滤镜，multipart） | ❌ 同上 | 服务端校验字段 `style_id`(int)，同样受 multipart 签名未解阻塞 |

### 已确认的服务端细节

- **md5 拼接按「请求里实际出现的字段」生效**：未传的字段按空串参与，所以"只传 uid+md5"时 `md5(uid+key)` 恰好命中。
  一旦多传一个非空字段（例如 `simCountryCode`），签名必须同步加上它，否则 `Invalid MD5`。
  → 调用时应**只传必要性字段**，不要照抄客户端的全字段 body。
- 服务端字段校验用 snake_case（`style_id`），而客户端 multipart 用 camelCase（`styleId`），二者不一致：
  JSON 传 `style_id` 能通过校验链，multipart 传 `styleId` 才进得去业务分支。服务端版本与客户端有漂移。
- `createJobLogo` 的 `md5 = md5(brand_name + func + prompt + proportion + style + uid + KEY)`，
  `func ∈ {logo, signature, tattoo}`，`style` 必须是 `getStyleListLogo` 的 `s_id` 字符串。
- `getResultLogo` 的 `md5 = md5(request_id + uid + KEY)`，与 `getResult` 一致。

### 若后续要打通 multipart 分支

1. 真机 / arm64 模拟器 + frida hook `Lu/a;->invokeSuspend`（`reface/create`）与 `Leo/p06f;->invokeSuspend`（`filter/create`），
   直接打印 `md5` 与 `MultipartBody` 的 part 列表；
2. 或对比服务端与客户端版本，确认服务端改造后的拼接口径。
