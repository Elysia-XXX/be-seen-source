"""Explicit real-provider connectivity check; uses one search and one model call."""
import asyncio
import json
import argparse
from pathlib import Path
from .providers import Providers, ProviderError
from .domain import parse_pages, validate_recommendations
from .schemas import Recommendations
from .prompts import messages, recommend_input
from .config import settings


async def main(search_only=False, save_result=None):
    cfg = settings()
    if search_only and not (cfg.tencentcloud_secret_id and cfg.tencentcloud_secret_key):
        raise SystemExit("请先配置元宝搜索凭据。未发起外部调用。")
    if not search_only and not cfg.configured:
        raise SystemExit("请先填写 .env 中的元宝搜索与模型配置。未发起外部调用。")
    provider = Providers()
    try:
        raw = await provider.search("深圳市拌饭餐厅")
        pages = parse_pages(raw, "preflight")
        if search_only:
            result = {"search_ok": True, "query": "深圳市拌饭餐厅", "returned_pages": len(pages),
                      "search_version": raw.get("Version"), "request_id": raw.get("RequestId")}
            if save_result:
                from .db import now
                path = Path(save_result)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({**result, "queried_at": now(), "pages": pages}, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        output = await provider.llm(messages("recommend", recommend_input("深圳市有哪些拌饭餐厅？", pages), Recommendations))
        checked = validate_recommendations(Recommendations.model_validate(output["data"]), pages)
        print(json.dumps({"search_ok": True, "returned_pages": len(pages), "search_version": raw.get("Version"),
                          "request_id": raw.get("RequestId"), "model_ok": True, "model": output["model"],
                          "valid_recommendations": len(checked["recommendations"])}, ensure_ascii=False, indent=2))
    except ProviderError as e:
        raise SystemExit(str(e)) from None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="验证真实搜索和模型连接，不打印密钥")
    parser.add_argument("--search-only", action="store_true", help="仅验证元宝搜索，消耗一次搜索调用")
    parser.add_argument("--save-result", help="搜索单独验证时，将带来源的结果保存为 JSON")
    args = parser.parse_args()
    asyncio.run(main(args.search_only, args.save_result))
