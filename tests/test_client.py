"""本地 mock 服务器验证 asr_client：不依赖真实 API Key。"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from mimo_asr import asr_client


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        # 校验请求结构：严格官方格式，仅 user + input_audio（不能加 system，模型会转写它）
        assert body["model"] == "mimo-v2.5-asr", body["model"]
        assert body["messages"][0]["role"] == "user", body["messages"]
        assert len(body["messages"]) == 1, body["messages"]
        content = body["messages"][0]["content"][0]
        assert content["type"] == "input_audio", content
        data_url = content["input_audio"]["data"]
        assert data_url.startswith("data:audio/wav;base64,"), data_url[:40]
        assert self.headers.get("api-key") == "TEST-KEY"
        assert body["asr_options"]["language"] == "zh"
        payload = {
            "id": "mock", "object": "chat.completion", "model": "mimo-v2.5-asr",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "你好，世界",
                                     "audio": None, "tool_calls": None, "audio_tokens": []}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13,
                      "prompt_tokens_details": {"audio_tokens": 4, "cached_tokens": 6}},
            "created": 0,
        }
        out = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def main():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    wav = b"\x00" * 32000  # 假音频字节（客户端不解析内容）
    text, usage = asr_client.recognize(
        wav, "audio/wav", api_key="TEST-KEY",
        base_url=f"http://127.0.0.1:{port}/v1/chat/completions",
        language="zh",
    )
    assert text == "你好，世界", text
    assert usage["prompt_tokens"] == 10
    print(f"PASS: text={text!r} usage={usage}")

    # 错误路径：无 Key
    try:
        asr_client.recognize(wav, "audio/wav", api_key="")
        raise SystemExit("FAIL: 应抛出 AsrError")
    except asr_client.AsrError as e:
        print(f"PASS: 无 Key 报错 -> {e}")

    server.shutdown()
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
