"""Conservative response validity checks; never a factual-accuracy score."""
import re

STATUS = re.compile(r'^(?:thinking|working|loading|processing|generating|思考中|正在思考|正在生成|Gemini\s*说|ChatGPT)[.。…\s]*$', re.I)
ERROR = re.compile(r'^(?:Traceback \(most recent call last\):|(?:\w*Error|\w*Exception):|\[Router\].*(?:failed|timeout|unavailable)|Something went wrong\b|An error occurred\b|You.ve reached .*limit|Unable to (?:generate|respond)|出错了|发生错误|调用失败)', re.I)
# Fallback for legacy text with no DOM evidence, not an ever-growing title list.
TITLE = re.compile(r'^[A-Z][a-z]+ing(?:\s+(?:[A-Z][A-Za-z-]*|the|a|an|of|and|for|to)){1,9}$')


def check_quality(answer, *, task='', role='answer', transport_ok=True,
                  wrapper_ok=True, evidence=None):
    if not transport_ok:
        return False, 'transport_failed'
    if not wrapper_ok:
        return False, 'wrapper_failed'
    text = answer.strip()
    if not text:
        return False, 'empty_answer'
    evidence = evidence or {}
    if evidence.get('processing'):
        return False, 'page_processing'
    if evidence.get('body_present') is False:
        return False, 'missing_model_body'
    if STATUS.fullmatch(text):
        return False, 'ui_status_only'
    if ERROR.match(text):
        return False, 'provider_error_text'
    if not evidence.get('completed') and TITLE.fullmatch(text) and text not in task:
        return False, 'temporary_title_only'
    if role == 'synthesis' and re.fullmatch(r'(?:review|analysis|审查意见|分析结果)[:：]?', text, re.I):
        return False, 'missing_final_answer'
    return True, 'ok'
