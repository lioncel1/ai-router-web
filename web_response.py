"""Read only the model body and wait for UI completion plus stable content."""
import hashlib
import time

from quality import check_quality


BODY = {
    "gemini": ("model-response", "message-content .markdown"),
    "chatgpt": ('[data-message-author-role="assistant"]', ".markdown"),
}

STOP = (
    'button[data-testid="stop-button"], '
    'button[aria-label*="Stop"], '
    'button[aria-label*="停止"], '
    'button.stop-button, '
    '[aria-busy="true"]'
)

COPY = (
    'button[data-testid="copy-turn-action-button"], '
    'button[aria-label="复制"], '
    'button[aria-label="Copy"], '
    'button[aria-label="Copy response"]'
)


def _processing(page):
    return bool(page.locator(f":is({STOP}):visible").count())


def _chatgpt_snapshot(page):
    """Read assistant answer only, including the 2026 DIL-rendered ChatGPT UI.

    Do not use generic <section> or <main> text: those can contain the user's
    question, navigation, and hidden quick-chat elements.
    """
    # 2026 ChatGPT layout: the assistant answer is rendered in a DilResponseRoot.
    # Match the stable class-name prefix rather than its changing CSS hash.
    # :visible excludes hidden quick-chat and other invisible copies.
    responses = page.locator('main [class*="DilResponseRoot"]:visible')
    count = responses.count()
    if count:
        # The root is specific to an assistant response. For plain and rich
        # replies, the entire root can contain multiple MarkdownRoot blocks.
        # Reading the root avoids dropping paragraphs or duplicating nested text.
        text = responses.last.inner_text().strip()
        return count, text, bool(text) and not _processing(page)

    # Compatibility with the old ChatGPT DOM (before DIL rendering).
    outer, body = BODY['chatgpt']
    responses = page.locator(outer)
    count = responses.count()
    if count:
        latest = responses.last
        text = '\n\n'.join(latest.locator(body).all_inner_texts()).strip()
        return count, text, bool(text) and not _processing(page)

    return 0, '', False



def snapshot(page, provider):
    if provider == "chatgpt":
        return _chatgpt_snapshot(page)

    outer, body = BODY[provider]
    responses = page.locator(outer)
    count = responses.count()
    if not count:
        return 0, "", False

    latest = responses.last
    content = latest.locator(body)
    text = "\n\n".join(content.all_inner_texts()).strip()
    # Rich Gemini cards (weather, search widgets) often have no Copy button.
    # Completion still requires a stable, changed model-only body.
    complete = bool(text) and not _processing(page)
    return count, text, complete


def get_last_response(page, provider):
    count, text, _ = snapshot(page, provider)
    return count, text


def wait_for_answer(page, provider, old_count=None, old_text=None, timeout=240, old_hash=None):
    started = time.monotonic()
    stable_since = None
    last_text = None

    while time.monotonic() - started < timeout:
        count, text, complete = snapshot(page, provider)

        changed = (
            bool(text)
            if old_count is None
            else count > old_count
            or bool(
                text
                and (
                    hashlib.sha256(text.encode()).hexdigest() != old_hash
                    if old_hash is not None
                    else text != old_text
                )
            )
        )

        valid, _ = check_quality(
            text,
            evidence={
                "body_present": bool(text),
                "completed": complete,
                "processing": not complete,
            },
        )

        if not changed or not valid:
            stable_since = None
            last_text = None
        elif text != last_text:
            last_text = text
            stable_since = time.monotonic()
        elif stable_since is not None:
            # Missing Gemini copy action is common for rich cards; require
            # longer stability to reduce false positives during streaming.
            stable_seconds = 5
            if provider == "gemini" and page is not None:
                try:
                    if page.locator("model-response").last.locator(COPY).count() == 0:
                        stable_seconds = 15
                except Exception:
                    stable_seconds = 15
            if time.monotonic() - stable_since >= stable_seconds:
                return text

        time.sleep(1)

    raise TimeoutError(f"{provider}: no completed stable model body within {timeout}s")
