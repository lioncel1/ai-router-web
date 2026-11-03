
import sys
import hashlib
import conversation_store

import json

import time

from pathlib import Path

from playwright.sync_api import sync_playwright
from web_response import get_last_response as read_last_response, wait_for_answer
from router_log import is_valid_conversation_url
from quality import check_quality


CDP_URL = "http://127.0.0.1:9223"

ROOT_URL = "https://chatgpt.com/"


BASE_DIR = Path(__file__).resolve().parent

DB_FILE = BASE_DIR / "data" / "chatgpt_conversations.json"


def load_db():
    return conversation_store.load(DB_FILE)


def save_db(data):
    conversation_store.save(DB_FILE, data)


def connect_chatgpt():

    p = sync_playwright().start()


    try:

        browser = p.chromium.connect_over_cdp(CDP_URL)

    except Exception:

        p.stop()

        raise RuntimeError(

            "无法连接9223端口，请确认ChatGPT Chrome已经启动"

        )


    if not browser.contexts:

        p.stop()

        raise RuntimeError("没有找到Chrome浏览器上下文")


    context = browser.contexts[0]


    for page in context.pages:

        if "chatgpt.com" in page.url:

            return p, browser, page


    return p, browser, context.new_page()


def _is_usable_input(item):
    """Return whether a locator is a visible, enabled, editable prompt box."""
    try:
        if not item.is_visible() or not item.is_enabled():
            return False
        if item.get_attribute("aria-hidden") == "true":
            return False

        tag_name = item.evaluate(
            "(element) => element.tagName.toLowerCase()"
        )
        if tag_name == "textarea":
            class_name = item.get_attribute("class") or ""
            if "fallbacktextarea" in class_name.lower():
                return False
            if item.get_attribute("readonly") is not None:
                return False

        return item.is_editable()
    except Exception:
        return False


def find_input_box(page, timeout=60):
    selectors = [
        "#prompt-textarea",
        '[contenteditable="true"]',
        'textarea[name="prompt-textarea"]',
        "textarea",
    ]
    start = time.time()

    while time.time() - start < timeout:
        for selector in selectors:
            locator = page.locator(selector)
            try:
                count = locator.count()
            except Exception:
                continue

            for index in range(count):
                item = locator.nth(index)
                if _is_usable_input(item):
                    return item

        time.sleep(0.5)

    raise RuntimeError(
        f"等待可见且可编辑的ChatGPT输入框超过{timeout}秒；"
        f"当前页面：{page.url}"
    )


def fill_prompt(page, prompt, attempts=3):
    """Fill the current prompt box, reacquiring it after DOM rebuilds."""
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            box = find_input_box(page)
            box.click()

            if not _is_usable_input(box):
                raise RuntimeError("ChatGPT输入框在点击后已不可用")

            try:
                box.fill(prompt)
                return box
            except Exception as fill_error:
                last_error = fill_error

            # The frontend may have replaced the locator during fill().
            box = find_input_box(page)
            box.click()

            if not _is_usable_input(box):
                raise RuntimeError("重新获取的ChatGPT输入框不可用")

            page.keyboard.press("Control+A")
            page.keyboard.insert_text(prompt)
            return box
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                page.wait_for_timeout(750)

    detail = (
        f"{type(last_error).__name__}: {last_error}"
        if last_error is not None
        else "未知错误"
    )
    raise RuntimeError(
        f"填写ChatGPT输入框失败，已尝试{attempts}次；"
        f"最后错误：{detail}"
    ) from last_error


def get_last_response(page):
    return read_last_response(page, "chatgpt")


def wait_until_stable(page, old_count=None, old_text=None, timeout=240, old_hash=None):
    return wait_for_answer(page, "chatgpt", old_count, old_text, timeout, old_hash)


def wait_for_conversation_url(page, timeout=20):

    start = time.time()


    while time.time() - start < timeout:

        url = page.url


        if "/c/" in url:

            return url


        time.sleep(0.5)


    return page.url


def run_chat(name, prompt, mode):

    conversations = load_db()

    info = conversations.get(name)


    # 完全相同的问题已经成功执行过

    if (

        info

        and info.get("last_prompt") == prompt

        and info.get("status") == "done"

        and info.get("last_answer")
        and is_valid_conversation_url(info.get("url", ""), "chatgpt")
        and check_quality(info["last_answer"], task=prompt, evidence=info.get("response_evidence"))[0]

    ):

        print("检测到相同任务已经执行过，不重复调用ChatGPT。")

        return info["last_answer"], info.get("url", "")


    if info and info.get("status") == "pending":
        if info.get("last_prompt") != prompt or not info.get("url"):
            raise RuntimeError("pending conversation cannot be overwritten or resent")
        if "baseline_count" not in info:
            raise RuntimeError("legacy pending response cannot be verified; inspect the existing conversation manually")

    p, browser, page = connect_chatgpt()


    try:

        # 上次已经发送，但读取阶段中断：

        # 只重新读取，不再发送Prompt

        if (

            info

            and info.get("last_prompt") == prompt

            and info.get("status") in {"pending", "done"}

            and info.get("url")

        ):

            print("检测到该问题之前已经发送，正在恢复读取，不重复提问。")


            page.goto(

                info["url"],

                wait_until="domcontentloaded",

                timeout=60000

            )


            page.wait_for_timeout(2000)


            answer = wait_until_stable(

                page,

                old_count=info.get("baseline_count"),

                old_text=None,
                old_hash=info.get("baseline_hash"),

                timeout=240

            )


            if not is_valid_conversation_url(page.url, "chatgpt"):
                raise RuntimeError("invalid conversation URL")
            info["url"] = page.url
            info["response_evidence"] = {"body_present": True, "completed": True, "processing": False}
            info["status"] = "done"

            info["last_answer"] = answer

            info["updated_at"] = time.strftime(

                "%Y-%m-%d %H:%M:%S"

            )


            conversations[name] = info

            save_db(conversations)


            return answer, info["url"]


        # new：已有同名会话时不重复新建

        if mode == "new" and info:

            print(

                f"会话 {name} 已存在，为避免重复创建，继续原会话。"

            )


            target_url = info.get("url", ROOT_URL)


        elif mode == "ask":

            if not info:

                raise RuntimeError(

                    f"会话 {name} 不存在，请先使用 new 创建"

                )


            target_url = info["url"]


        elif mode == "reset":

            print(f"明确重新开始ChatGPT会话：{name}")

            target_url = ROOT_URL


        else:

            print(f"新建ChatGPT会话：{name}")

            target_url = ROOT_URL


        page.goto(

            target_url,

            wait_until="domcontentloaded",

            timeout=60000

        )


        page.wait_for_timeout(2000)


        old_count, old_text = get_last_response(page)


        fill_prompt(page, prompt)
        page.keyboard.press("Enter")


        print("问题已发送，正在等待ChatGPT回复……")


        conversation_url = wait_for_conversation_url(page)


        conversations[name] = {

            "url": conversation_url,

            "last_prompt": prompt,

            "last_answer": "",

            "status": "pending",
            "baseline_count": old_count,
            "baseline_hash": hashlib.sha256(old_text.encode()).hexdigest(),

            "updated_at": time.strftime(

                "%Y-%m-%d %H:%M:%S"

            )

        }


        save_db(conversations)


        answer = wait_until_stable(

            page,

            old_count=old_count,

            old_text=old_text,

            timeout=240

        )


        if not is_valid_conversation_url(page.url, "chatgpt"):
            raise RuntimeError("invalid conversation URL")
        conversations[name]["response_evidence"] = {"body_present": True, "completed": True, "processing": False}
        conversations[name]["status"] = "done"

        conversations[name]["last_answer"] = answer

        conversations[name]["url"] = page.url

        conversations[name]["updated_at"] = time.strftime(

            "%Y-%m-%d %H:%M:%S"

        )


        save_db(conversations)


        return answer, page.url


    finally:

        p.stop()


def list_chats():

    conversations = load_db()


    if not conversations:

        print("目前没有已保存的ChatGPT会话")

        return


    for name, info in conversations.items():

        print()

        print(name)

        print("  状态：", info.get("status", ""))

        print("  地址：", info.get("url", ""))

        print("  更新时间：", info.get("updated_at", ""))


def main():

    if len(sys.argv) < 2:

        print("用法：")

        print('python chatgpt_chat.py new 会话名 "问题"')

        print('python chatgpt_chat.py ask 会话名 "继续追问"')

        print('python chatgpt_chat.py reset 会话名 "重新开始的问题"')

        print('python chatgpt_chat.py list')

        return


    command = sys.argv[1].lower()


    if command == "list":

        list_chats()

        return


    if command not in ["new", "ask", "reset"]:

        print("只支持：new / ask / reset / list")

        return


    if len(sys.argv) < 4:

        print("请提供会话名称和问题")

        return


    name = sys.argv[2]

    prompt = " ".join(sys.argv[3:])


    answer, url = run_chat(

        name=name,

        prompt=prompt,

        mode=command

    )


    print()

    print("=" * 60)

    print("ChatGPT回复：")

    print("=" * 60)

    print(answer)

    print()

    print("会话地址：", url)


if __name__ == "__main__":

    main()

