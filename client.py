"""AI Art Generator 1.4.7 —— 协议客户端（纯协议，无需 APK / 真机 / Frida）。

上游：https://aga.coolsummerdev.com/  (Laravel)
包名：ai.art.generator.paint.draw.photo

鉴权两层：
1) HTTP 头 MD5 = md5(PACKAGE + 官方APK签名证书DER的hex + TIMESTAMP)
   —— 服务端白名单里存的是官方签名证书，必须用官方 hex（默认已内置 1.4.7 证书，
      可用环境变量 AAG_CERT_HEX 覆盖成其它版本证书）。
2) 请求体 md5 = md5(<按接口固定顺序拼接的业务字段> + uid + NATIVE_KEY)
   —— NATIVE_KEY 来自 libcipher-jni.so 的 getCipherKeyFromJNI()。

uid 由客户端任意生成：uid = md5(UUID 字符串)，服务端不校验来源。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

BASE = os.environ.get("AAG_BASE", "https://aga.coolsummerdev.com/")
PACKAGE = "ai.art.generator.paint.draw.photo"
VERSION = os.environ.get("AAG_VERSION", "147")  # versionCode
NATIVE_KEY = "Emoji_JingNCK567"  # libcipher-jni.so -> Java_com_cipher_CipherUtils_getCipherKeyFromJNI

# 官方 1.4.7 签名证书（DER hex，来自 Google Play 分发签名）
DEFAULT_CERT_HEX = (
    "3082037c30820264020101300d06092a864886f70d01010b05003081823119301706035504030c104149204172742047656e"
    "657261746f72311e301c060355040b0c154149204172742047656e657261746f72205465616d311e301c060355040a0c1541"
    "49204172742047656e657261746f72205465616d310b300906035504070c024c41310b300906035504080c024341310b3009"
    "0603550406130230313020170d3233303431393132323730375a180f32313232303332363132323730375a30818231193017"
    "06035504030c104149204172742047656e657261746f72311e301c060355040b0c154149204172742047656e657261746f72"
    "205465616d311e301c060355040a0c154149204172742047656e657261746f72205465616d310b300906035504070c024c41"
    "310b300906035504080c024341310b300906035504061302303130820122300d06092a864886f70d01010105000382010f00"
    "3082010a0282010100aabd8b9d066bb12a8f4827d1e8f5deb45b9eeca935776d0cb15a2d347d1300aff6d10aed80b55827c7"
    "31c916c7146901e0d0c355f2fde9862b4c398322edb4a0804a81a46482e7d2b5156da9d65fdba7b589b3797e5e38a3faa38f"
    "c03b6c0b0aca9ceff88db042492a28016237e5c9c98f038c66f368bbdcf287a8d62ee09e22ed728814329d5f5256d86db78f"
    "3f498a72450ffaf2adfc37dcc821d6c4361a978ab413811366607de619e34122c21dd3e12ad23e8b7892a49bbfaa9c1b71d2"
    "9eb518383fe2f875f53a84d4fcedf2c54c70c9e744fb42e39dd38a6b5c2b55a65241470d225be3ebb22dd1efce2f20fba0fe"
    "d70c7fe9fccef37354f5b620e0b3570203010001300d06092a864886f70d01010b050003820101006c9f05ba843525c2db6c"
    "de1547f12dcfd7722e6f6d53e186eac072e222a88175df71b7279d054d4e33f0fb2a81c7de2fd3f5286c0f0ab8735dcd4760"
    "02dc444f0565e01c973b12288aff9b4c4fe1f6d0b8af4648d7b0da2dc43bb193708f2d4c44e064a1bcd2a6ea51257635ce62"
    "a21e9b43841fedd4ada5e6c1e27696f3723abba343db6017a150929cf9516e4f881c9f7e7827fecd5aaaf6423bf198d825d7"
    "be7540495b80c0f00d77064db36b0ac007588e51a262229cfc028029f8383b25d487b095c37ee7fbf5223c8dfa16eb0e0722"
    "a11586c2c614afebd5b29b970167c293dfd5d077bc9af5c9c1f6834c6a707bbf326a8a147a6622c08130e56276d1"
)
CERT_HEX = os.environ.get("AAG_CERT_HEX", "").strip() or DEFAULT_CERT_HEX

# 状态码
STATUS_SUCCESS = 1
STATUS_FAILED = 2
STATUS_PROCESSING = 3
STATUS_QUEUING = 4
STATUS_CANCEL = 5
STATUS_NSFW = 6


def md5_hex(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def new_uid() -> str:
    """客户端 uid = md5(随机 UUID)，服务端不校验设备来源。"""
    return md5_hex(str(uuid.uuid4()))


class AagError(RuntimeError):
    def __init__(self, ret: int, msg: str):
        super().__init__(f"ret={ret} msg={msg}")
        self.ret = ret
        self.msg = msg


class AagClient:
    """一个 uid 一个配额桶。多 uid = 多桶（免费额度按 uid 计）。"""

    def __init__(self, uid: str | None = None, cert_hex: str | None = None,
                 sim_country: str = "US", locale_country: str = "US", timeout: int = 90):
        self.uid = uid or new_uid()
        self.cert_hex = (cert_hex or CERT_HEX)
        if not self.cert_hex:
            raise RuntimeError("缺少官方签名证书 hex：设置 AAG_CERT_HEX 或传 cert_hex=")
        self.sim_country = sim_country
        self.locale_country = locale_country
        self.timeout = timeout

    # ---------------- 传输层 ----------------
    def _headers(self) -> dict[str, str]:
        ts = str(int(time.time()))
        return {
            "Content-Type": "application/json",
            "TIMESTAMP": ts,
            "MD5": md5_hex(PACKAGE + self.cert_hex + ts),
            "PACKAGE": PACKAGE,
            "uid": self.uid,
            "version": VERSION,
            "simCountryCode": self.sim_country,
            "localeCountryCode": self.locale_country,
        }

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(BASE + path.lstrip("/"), data=data,
                                     headers=self._headers(), method="POST")
        last = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8", "replace"))
            except urllib.error.HTTPError as exc:  # 上游 5xx 偶发
                last = exc
                if 500 <= exc.code < 600 and attempt < 2:
                    time.sleep(1 + attempt)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError) as exc:
                last = exc
                if attempt < 2:
                    time.sleep(1 + attempt)
                    continue
                raise
        raise AagError(-1, f"request failed: {last}")

    def _auth(self, *parts: str) -> str:
        return md5_hex("".join(parts) + self.uid + NATIVE_KEY)

    def _basic(self) -> dict[str, str]:
        """CheckUserWhite / SuccessCount / 列表类端点只需 uid + md5(uid+key)。"""
        return {"uid": self.uid, "md5": self._auth()}

    # ---------------- 业务接口 ----------------
    def check_user(self) -> dict[str, Any]:
        return self.post("/api/v3/CheckUserWhite", self._basic())

    def success_count(self) -> dict[str, Any]:
        return self.post("/api/v3/SuccessCount", self._basic())

    def style_list(self) -> list[dict[str, Any]]:
        return self.post("/api/v3/getStyleList", self._basic()).get("data", {}).get("styleList", [])

    def tips(self) -> list[dict[str, Any]]:
        return self.post("/api/v3/tips", self._basic()).get("data", {}).get("prompt", [])

    def inspiration(self) -> list[dict[str, Any]]:
        return self.post("/api/v3/getInspirationList", self._basic()).get("data", {}).get("inspirationList", [])

    def create(self, prompt: str, style_id: str | int, proportion: str = "1:1",
               strength: str = "") -> str:
        """提交文生图任务，返回 request_id。

        md5 拼接顺序（逆向自 Lle/e0;->invokeSuspend）：
            is_sub + localeCountryCode + orderId + productId + prompt
            + proportion + purchaseToken + purchaseType + simCountryCode
            + strength + style + uid + NATIVE_KEY
        所有 IAP 字段留空、is_sub 固定 "0" 即可（服务端不校验真实订阅）。
        """
        style = str(style_id)
        body = {
            "prompt": prompt,
            "proportion": proportion,
            "style": style,
            "uid": self.uid,
            "strength": strength,
            "simCountryCode": self.sim_country,
            "localeCountryCode": self.locale_country,
            "is_sub": "0",
            "orderId": "",
            "productId": "",
            "purchaseToken": "",
            "purchaseType": "",
        }
        body["md5"] = md5_hex("0" + self.locale_country + "" + "" + prompt + proportion + ""
                             + "" + self.sim_country + strength + style + self.uid + NATIVE_KEY)
        res = self.post("/api/v3/creating", body)
        if res.get("ret") != 200:
            raise AagError(res.get("ret", -1), str(res.get("msg", "")))
        return res["data"]["request_id"]

    def result(self, request_id: str) -> dict[str, Any]:
        """查询任务；md5 = md5(request_id + uid + key)。"""
        res = self.post("/api/v3/getResult", {
            "request_id": request_id,
            "uid": self.uid,
            "md5": self._auth(request_id),
        })
        if res.get("ret") != 200:
            raise AagError(res.get("ret", -1), str(res.get("msg", "")))
        return res.get("data", {})

    def generate(self, prompt: str, style_id: str | int = 1, proportion: str = "1:1",
                 strength: str = "", timeout: float = 180.0, interval: float = 4.0) -> dict[str, Any]:
        """create + 轮询，返回最终 data（含 url）。status: 1=success 2=failed 3=processing。"""
        rid = self.create(prompt, style_id, proportion, strength)
        return self.wait(rid, timeout, interval)

    def wait(self, request_id: str, timeout: float = 180.0, interval: float = 4.0) -> dict[str, Any]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            data = self.result(request_id)
            status = data.get("status")
            if status == STATUS_SUCCESS:
                return data
            if status in (STATUS_FAILED, STATUS_NSFW, STATUS_CANCEL):
                raise AagError(-2, f"task failed: status={status} msg={data.get('message')}")
            time.sleep(interval)
        raise AagError(-3, f"task timeout after {timeout}s (request_id={request_id})")

    # ---------------- multipart 分支 ----------------
    def _multipart(self, fields: dict[str, str], filename: str, blob: bytes) -> tuple[bytes, str]:
        boundary = "----AagBoundary" + uuid.uuid4().hex
        buf = bytearray()
        for key, val in fields.items():
            buf += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{val}\r\n".encode()
        buf += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{filename}\"; "
                f"filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode()
        buf += blob + b"\r\n"
        buf += f"--{boundary}--\r\n".encode()
        return bytes(buf), f"multipart/form-data; boundary={boundary}"

    def _post_raw(self, path: str, payload: bytes, ctype: str) -> dict[str, Any]:
        headers = self._headers()
        headers["Content-Type"] = ctype
        req = urllib.request.Request(BASE + path.lstrip("/"), data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))

    def reface_categories(self) -> list[dict[str, Any]]:
        data = self.post("/api/v3/reface/categories", self._basic()).get("data", {})
        return data.get("refaceCategoryList", [])

    def reface_create(self, style_id: str | int, image: bytes, filename: str = "face.jpg") -> str:
        """换脸。md5 = md5(is_sub + localeCountryCode + orderId + productId + purchaseToken
        + purchaseType + simCountryCode + styleId + uid + KEY)；文件 part 名任意。"""
        sid = str(style_id)
        md5v = md5_hex("0" + self.locale_country + "" + "" + "" + "" + self.sim_country
                       + sid + self.uid + NATIVE_KEY)
        fields = {"styleId": sid, "uid": self.uid,
                  "simCountryCode": self.sim_country, "localeCountryCode": self.locale_country,
                  "is_sub": "0", "orderId": "", "productId": "",
                  "purchaseToken": "", "purchaseType": "", "md5": md5v}
        payload, ctype = self._multipart(fields, filename, image)
        res = self._post_raw("/api/v3/reface/create", payload, ctype)
        if res.get("ret") != 200:
            raise AagError(res.get("ret", -1), str(res.get("msg", "")))
        return res["data"]["request_id"]

    def filter_create(self, style_id: str | int, proportion: str, image: bytes,
                      sex: int = 0, filename: str = "photo.jpg") -> str:
        """AI 滤镜。md5 = md5(is_sub + localeCountryCode + orderId + productId + proportion
        + purchaseToken + purchaseType + sex + simCountryCode + style_id + uid + KEY)。"""
        sid = str(style_id)
        md5v = md5_hex("0" + self.locale_country + "" + "" + proportion + "" + ""
                       + str(sex) + self.sim_country + sid + self.uid + NATIVE_KEY)
        fields = {"proportion": proportion, "style_id": sid, "uid": self.uid,
                  "simCountryCode": self.sim_country, "localeCountryCode": self.locale_country,
                  "is_sub": "0", "orderId": "", "productId": "", "purchaseToken": "",
                  "purchase_type": "", "sex": str(sex), "md5": md5v}
        payload, ctype = self._multipart(fields, filename, image)
        res = self._post_raw("/api/v3/filter/create", payload, ctype)
        if res.get("ret") != 200:
            raise AagError(res.get("ret", -1), str(res.get("msg", "")))
        return res["data"]["request_id"]

    def create_logo(self, prompt: str, style: str | int, proportion: str = "1:1", strength: str = "",
                    brand_name: str = "", func: str = "logo", scene: str = "0") -> str:
        """Logo / Signature / Tattoo。func ∈ {logo, signature, tattoo}；style 用 getStyleListLogo 的 s_id。

        实测（2026-10-29）：服务端拼接顺序 = func + prompt + proportion + style + uid
        （is_sub/locale/orderId/productId/purchaseToken/purchaseType/scene/sim/strength
        为空时按空串，可与 uid 一起省略）。style 传风格名会 40109 Invalid style。
        """
        style = str(style)
        md5v = md5_hex(brand_name + func + prompt + proportion + style + self.uid + NATIVE_KEY)
        body = {"prompt": prompt, "brand_name": brand_name, "proportion": proportion,
                "style": style, "uid": self.uid, "func": func, "md5": md5v}
        res = self.post("/api/v3/createJobLogo", body)
        if res.get("ret") != 200:
            raise AagError(res.get("ret", -1), str(res.get("msg", "")))
        return res["data"]["request_id"]

    def result_logo(self, request_id: str) -> dict[str, Any]:
        res = self.post("/api/v3/getResultLogo", {
            "request_id": request_id, "uid": self.uid, "md5": self._auth(request_id)})
        if res.get("ret") != 200:
            raise AagError(res.get("ret", -1), str(res.get("msg", "")))
        return res.get("data", {})
