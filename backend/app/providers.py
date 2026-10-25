import asyncio
import json
import httpx
from .config import settings


class ProviderError(Exception):
    def __init__(self, message, retryable=False, fatal=False):
        super().__init__(message)
        self.retryable = retryable
        self.fatal = fatal


class Providers:
    def __init__(self):
        self.cfg = settings()

    async def read_article(self, url):
        from .articles import read_article
        return await read_article(url)

    async def search(self, query):
        if not self.cfg.tencentcloud_secret_id or not self.cfg.tencentcloud_secret_key:
            raise ProviderError("元宝搜索未配置：请在服务端填写腾讯云 SecretId 和 SecretKey。", fatal=True)
        def request():
            from tencentcloud.common import credential
            from tencentcloud.common.profile.client_profile import ClientProfile
            from tencentcloud.common.profile.http_profile import HttpProfile
            from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
            from tencentcloud.wsa.v20250508 import wsa_client, models
            hp = HttpProfile(endpoint="wsa.tencentcloudapi.com", reqTimeout=20)
            cp = ClientProfile(httpProfile=hp)
            client = wsa_client.WsaClient(credential.Credential(self.cfg.tencentcloud_secret_id, self.cfg.tencentcloud_secret_key), "", cp)
            req = models.SearchProRequest()
            req.from_json_string(json.dumps({"Query": query, "Mode": 0, "Cnt": 10}))
            try:
                raw = json.loads(client.SearchPro(req).to_json_string())
                if raw.get("Msg"):
                    raise ProviderError("元宝搜索未正常返回结果：" + str(raw["Msg"])[:200])
                return raw
            except TencentCloudSDKException as e:
                code = e.get_code()
                # Inspect locally, but never echo SDK bodies or request metadata.
                detail = e.get_message() or ""
                if "10013" in detail:
                    raise ProviderError("本机网络访问被权限策略阻止（WinError 10013）；请求尚未到达腾讯云，无法验证凭据。", fatal=True) from None
                if "NetworkError" in code:
                    raise ProviderError("无法连接元宝搜索官方接口；请检查当前执行环境的网络权限、代理和网络连接。", retryable=True) from None
                transient = any(x in code for x in ("RequestLimitExceeded", "InternalError", "NetworkError"))
                fatal = any(x in code for x in ("AuthFailure", "Unauthorized", "ResourceNotFound", "ResourceUnavailable"))
                raise ProviderError(f"元宝搜索调用失败（{code}）；请检查服务开通、配额和密钥权限。", transient, fatal) from None
        try:
            return await asyncio.to_thread(request)
        except ProviderError:
            raise
        except Exception:
            raise ProviderError("元宝搜索连接失败，请检查网络或 SDK 配置。", retryable=True) from None

    async def llm(self, messages):
        if not self.cfg.base_url or not self.cfg.api_key or not self.cfg.model:
            raise ProviderError("模型服务未配置：请在服务端填写 BASE_URL、API_KEY 和 MODEL。", fatal=True)
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(self.cfg.base_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": f"Bearer {self.cfg.api_key}"},
                    json={"model": self.cfg.model, "messages": messages, "temperature": 0, "max_tokens": 6500})
            if response.status_code >= 400:
                raise ProviderError(f"模型接口返回 HTTP {response.status_code}；请检查模型、额度及接口配置。",
                                    response.status_code == 429 or response.status_code >= 500,
                                    response.status_code in (401, 403, 404))
            raw = response.json()
            text = raw["choices"][0]["message"]["content"]
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            return {"data": json.loads(text), "usage": raw.get("usage", {}), "model": raw.get("model", self.cfg.model)}
        except ProviderError:
            raise
        except (httpx.TimeoutException, httpx.TransportError):
            raise ProviderError("模型请求超时或网络连接失败。", retryable=True) from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise ProviderError("模型未返回可校验的 JSON 对象。", retryable=True) from None
