
from datetime import datetime

from pathlib import Path

from playwright.sync_api import sync_playwright



debug_dir = Path("/home/ubuntu/ai-router/debug")

debug_dir.mkdir(parents=True, exist_ok=True)



filename = debug_dir / (

    "chatgpt_error_" +

    datetime.now().strftime("%Y%m%d_%H%M%S") +

    ".png"

)



with sync_playwright() as p:

    browser = p.chromium.connect_over_cdp(

        "http://127.0.0.1:9223"

    )



    page = None



    for context in browser.contexts:

        for candidate in context.pages:

            if "chatgpt.com" in candidate.url:

                page = candidate

                break



    if page is None:

        raise RuntimeError("没有找到ChatGPT页面")



    page.screenshot(

        path=str(filename),

        full_page=True

    )



    print(f"错误截图已保存：{filename}")

