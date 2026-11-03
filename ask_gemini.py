
import sys

import time

from playwright.sync_api import sync_playwright



CDP_URL = "http://127.0.0.1:9222"





def get_gemini_page(browser):

    for context in browser.contexts:

        for page in context.pages:

            if "gemini.google.com" in page.url:

                return page

    raise RuntimeError("没有找到 Gemini 页面")





def get_input_box(page):

    selectors = [

        "div.ql-editor",

        'rich-textarea [contenteditable="true"]',

        '[contenteditable="true"][role="textbox"]',

    ]



    for selector in selectors:

        locator = page.locator(selector)



        if locator.count() > 0:

            for i in range(locator.count() - 1, -1, -1):

                try:

                    if locator.nth(i).is_visible():

                        return locator.nth(i)

                except:

                    pass



    raise RuntimeError("没有找到 Gemini 输入框")





def clean_answer(text):

    text = text.strip()



    if text.startswith("Gemini 说"):

        text = text[len("Gemini 说"):].strip()



    return text





def ask_gemini(prompt):

    with sync_playwright() as p:

        browser = p.chromium.connect_over_cdp(CDP_URL)



        page = get_gemini_page(browser)

        page.bring_to_front()



        responses = page.locator("model-response")

        old_count = responses.count()



        box = get_input_box(page)

        box.click()



        try:

            box.fill(prompt)

        except:

            box.press("Control+A")

            box.insert_text(prompt)



        box.press("Enter")



        print("已发送，正在等待 Gemini 回复...")



        timeout = 180

        start = time.time()

        last_text = ""

        stable_count = 0



        while time.time() - start < timeout:

            current_count = responses.count()



            if current_count > old_count:

                try:

                    current_text = responses.last.inner_text().strip()

                except:

                    current_text = ""



                if current_text:

                    if current_text == last_text:

                        stable_count += 1

                    else:

                        last_text = current_text

                        stable_count = 0



                    if stable_count >= 5:

                        return clean_answer(current_text)



            time.sleep(1)



        raise TimeoutError("等待 Gemini 回复超过 180 秒")





if __name__ == "__main__":

    if len(sys.argv) < 2:

        print('用法：python ask_gemini.py "你的问题"')

        sys.exit(1)



    prompt = " ".join(sys.argv[1:])



    try:

        answer = ask_gemini(prompt)



        print()

        print("=" * 60)

        print("Gemini 回复：")

        print("=" * 60)

        print(answer)



    except Exception as e:

        print()

        print("调用失败：", e)

        print("请确认 Gemini Chrome 正在运行，并且 9222 端口正常。")

