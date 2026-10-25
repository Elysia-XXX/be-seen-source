import json
import pytest
import httpx
from tencentcloud.wsa.v20250508 import wsa_client
from app.providers import Providers, ProviderError


@pytest.mark.asyncio
async def test_tencent_sdk_request_shape_and_response(monkeypatch):
    captured = {}
    class Response:
        def to_json_string(self):
            return json.dumps({"Pages":[],"RequestId":"sdk-check","Version":"standard"})
    class Client:
        def __init__(self, cred, region, profile):
            captured["endpoint"] = profile.httpProfile.endpoint
            captured["timeout"] = profile.httpProfile.reqTimeout
        def SearchPro(self, request):
            captured["params"] = json.loads(request.to_json_string())
            return Response()
    monkeypatch.setattr(wsa_client,"WsaClient",Client)
    output = await Providers().search("深圳市拌饭")
    assert output["RequestId"] == "sdk-check"
    assert captured["endpoint"] == "wsa.tencentcloudapi.com"
    assert captured["timeout"] == 20
    assert captured["params"]["Mode"] == 0
    assert captured["params"]["Cnt"] == 10
    assert captured["params"]["Freshness"] is None


@pytest.mark.asyncio
async def test_compatible_model_wire_contract(monkeypatch):
    captured = {}
    class Client:
        def __init__(self, **kw): captured["timeout"]=kw["timeout"]
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            captured["url"]=url
            captured["payload"]=kwargs["json"]
            return httpx.Response(200,json={"model":"resolved-model","choices":[{"message":{"content":"```json\n{\"recommendations\":[]}\n```"}}],"usage":{"total_tokens":3}})
    monkeypatch.setattr(httpx,"AsyncClient",Client)
    response = await Providers().llm([{"role":"user","content":"test"}])
    assert captured["url"] == "https://model.example.test/v1/chat/completions"
    assert captured["timeout"] == 60
    assert captured["payload"]["temperature"] == 0
    assert "response_format" not in captured["payload"]
    assert response["data"] == {"recommendations":[]}


@pytest.mark.asyncio
async def test_provider_error_never_echoes_sensitive_body(monkeypatch):
    class Client:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs):
            return httpx.Response(401, text="your API key is test-model-key")
    monkeypatch.setattr(httpx,"AsyncClient",Client)
    with pytest.raises(ProviderError) as e:
        await Providers().llm([])
    assert e.value.fatal
    assert "test-model-key" not in str(e.value)


@pytest.mark.asyncio
async def test_network_permission_error_is_not_credentials_failure(monkeypatch):
    from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
    class Client:
        def __init__(self, *args): pass
        def SearchPro(self, request):
            raise TencentCloudSDKException("ClientNetworkError", "[WinError 10013] Access denied test-search-key")
    monkeypatch.setattr(wsa_client,"WsaClient",Client)
    with pytest.raises(ProviderError) as e:
        await Providers().search("深圳市拌饭")
    assert e.value.fatal
    assert "10013" in str(e.value)
    assert "test-search-key" not in str(e.value)
