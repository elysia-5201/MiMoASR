"""MiMo-V2.5-ASR 语音识别客户端（OpenAI 兼容接口）。

POST https://api.xiaomimimo.com/v1/chat/completions
请求体: {"model":"mimo-v2.5-asr","messages":[{"role":"user","content":[
            {"type":"input_audio","input_audio":{"data":"data:<mime>;base64,<audio>"}}]}],
         "asr_options":{"language":"auto"}}
响应: choices[0].message.content 即识别文本。
"""
from __future__ import annotations

import base64
import json
import time
from typing import Optional, Tuple

import requests

TIMEOUT = (10, 180)  # (连接超时, 读取超时)


class AsrError(Exception):
    """携带可读中文信息的 ASR 调用错误。"""


def _to_data_url(audio_bytes: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(audio_bytes).decode('ascii')}"


def recognize(
    audio_bytes: bytes,
    mime: str,
    *,
    api_key: str,
    base_url: str = "https://api.xiaomimimo.com/v1/chat/completions",
    model: str = "mimo-v2.5-asr",
    language: str = "auto",
) -> Tuple[str, dict]:
    """上传音频并返回 (识别文本, usage 统计)；文本可能为空串。

    注意：严格使用官方请求结构（仅 user + input_audio），
    不要添加 system 提示 —— MiMo-V2.5-ASR 会把 system 文本当内容转写，
    导致输出变成 prompt 本身（如"请只输出一个空字符串"）。
    无效信息过滤由 VAD 短音丢弃 + 文本清洗完成。
    """
    api_key = (api_key or "").strip()
    if not api_key:
        raise AsrError("未设置 API Key，请在界面上填写或设置环境变量 MIMO_API_KEY")

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "input_audio",
                    "input_audio": {"data": _to_data_url(audio_bytes, mime)},
                }
            ],
        }
    ]

    body = {
        "model": model,
        "messages": messages,
        "asr_options": {"language": language or "auto"},
    }

    headers = {
        "Content-Type": "application/json",
        "api-key": api_key,  # 官方支持 api-key 与 Authorization: Bearer 两种，任选其一
    }

    t0 = time.time()
    try:
        resp = requests.post(base_url, headers=headers, json=body, timeout=TIMEOUT)
    except requests.exceptions.Timeout:
        raise AsrError("请求超时：音频过大或网络不佳，请重试")
    except requests.exceptions.ConnectionError:
        raise AsrError("网络连接失败，请检查网络或代理设置")
    except requests.exceptions.RequestException as e:
        raise AsrError(f"请求异常：{e}")

    try:
        data = resp.json()
    except ValueError:
        raise AsrError(f"服务返回非 JSON 数据（HTTP {resp.status_code}）")

    if resp.status_code != 200:
        msg = data.get("error", {}).get("message") if isinstance(data, dict) else None
        raise AsrError(f"HTTP {resp.status_code}：{msg or json.dumps(data, ensure_ascii=False)[:300]}")

    try:
        text = (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        raise AsrError(f"响应格式异常：{json.dumps(data, ensure_ascii=False)[:300]}")

    # 空文本不抛错：由调用方决定（自动识别时静默过滤，手动识别时提示）
    usage = data.get("usage", {})
    usage = {**usage, "seconds": round(time.time() - t0, 1)}
    return text, usage
