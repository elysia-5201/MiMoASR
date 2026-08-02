"""VAD / OSC / 文本过滤 / UI 冒烟 测试（无需真实麦克风与网络）。"""
from __future__ import annotations

import socket
import threading
import time

import numpy as np

from mimo_asr import vrc
from mimo_asr.ui import filter_noise_text
from mimo_asr.vad import VadListener

SR = 16000
FRAME = 1600  # 100ms


def frames(samples) -> np.ndarray:
    """把样本列表转成 (FRAME,1) int16 数组。"""
    return np.asarray(samples, dtype=np.int16).reshape(-1, 1)


def voice_frame(amp: float = 0.1) -> np.ndarray:
    """100ms 正弦波，RMS ≈ amp*0.707。"""
    t = np.arange(FRAME) / SR
    return frames((amp * 32767 * np.sin(2 * np.pi * 200 * t)))


def silence_frame() -> np.ndarray:
    return frames(np.zeros(FRAME))


def feed(listener: VadListener, n_frames, f):
    for _ in range(n_frames):
        listener._callback(f(), FRAME, None, None)


def test_vad_segment_trigger():
    """1.0s 语音 + 静音超时 → 触发一个段，且尾部静音被缓冲（尾音保留）。"""
    got = []
    v = VadListener(sample_rate=SR, threshold=0.02, silence_timeout=0.8,
                    min_speech_sec=0.5, on_segment=lambda wav, dur: got.append((wav, dur)))
    v.start = lambda: None  # 不真正开麦克风
    feed(v, 3, silence_frame)      # 0.3s 静音
    feed(v, 10, voice_frame)       # 1.0s 语音
    feed(v, 10, silence_frame)     # 1.0s 静音 → 0.8s 超时切段
    assert len(got) == 1, f"应触发 1 段, got {len(got)}"
    wav, dur = got[0]
    # 段 = 1.0s 语音 + ~0.7s 尾部静音（尾音保留），而非 1.0s 硬切
    assert 1.4 <= dur <= 2.0, dur
    assert len(wav) > 44  # 有效 WAV
    # 冷却期不重复触发
    feed(v, 5, voice_frame)
    feed(v, 10, silence_frame)
    assert len(got) == 1, "冷却期内不应触发新段"


def test_vad_tail_preserved():
    """音量低于阈值但未超时的尾音/弱音必须保留在段内（修复尾音截断问题）。"""
    got = []
    v = VadListener(sample_rate=SR, threshold=0.02, silence_timeout=0.8,
                    min_speech_sec=0.5, on_segment=lambda wav, dur: got.append((wav, dur)))
    feed(v, 3, silence_frame)            # 0.3s 静音
    feed(v, 10, voice_frame)             # 1.0s 语音（≥阈值）
    feed(v, 5, lambda: voice_frame(amp=0.005))  # 0.5s 弱音（<阈值，说话音量下降部分）
    feed(v, 10, silence_frame)           # 静音超时切段
    assert len(got) == 1, f"应触发 1 段, got {len(got)}"
    wav, dur = got[0]
    # 段必须包含 0.5s 弱音尾 → 总时长 >= 1.0 + 0.5 = 1.5s
    assert dur >= 1.5, f"尾音未保留: {dur}s"


def test_vad_short_noise_dropped():
    """0.3s 短音（模拟咳嗽/清痰）有效语音时长 < min_speech_sec → 丢弃不触发。"""
    got = []
    v = VadListener(sample_rate=SR, threshold=0.02, silence_timeout=0.8,
                    min_speech_sec=0.5, on_segment=lambda wav, dur: got.append(dur))
    feed(v, 3, silence_frame)
    feed(v, 3, voice_frame)        # 0.3s 短音（有效语音 0.3s < 0.5s）
    feed(v, 10, silence_frame)     # 静音超时
    assert got == [], f"短音应被丢弃, got {got}"


def test_vad_low_volume_ignored():
    """音量低于阈值 → 始终不触发。"""
    got = []
    v = VadListener(sample_rate=SR, threshold=0.02, silence_timeout=0.8,
                    min_speech_sec=0.5, on_segment=lambda wav, dur: got.append(dur))
    feed(v, 30, lambda: voice_frame(amp=0.005))  # RMS ~0.0035 < 0.02
    feed(v, 10, silence_frame)
    assert got == []


def test_osc_format():
    msg = vrc.build_osc("/chatbox/input", "sTT", "你好")
    # "/chatbox/input"(13) + null = 14 → 4 字节对齐补 2 个 \x00 = 16 字节
    assert msg.startswith(b"/chatbox/input\x00\x00"), msg[:20]
    assert b",sTT" in msg
    # 类型标签块: ",sTT"(5) + null 对齐到 8 字节，其后才是字符串参数
    body = msg[msg.index(b",sTT") + 8:]
    # 字符串参数: UTF-8 + null + 4 字节对齐
    assert body == b"\xe4\xbd\xa0\xe5\xa5\xbd\x00\x00", body


def test_send_chatbox_udp():
    """起一个 UDP mock 服务器，验证 send_chatbox 真的把 OSC 包发出去。"""
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    received = []

    def recv():
        data, _ = srv.recvfrom(4096)
        received.append(data)

    threading.Thread(target=recv, daemon=True).start()
    ok = vrc.send_chatbox("大家好", host="127.0.0.1", port=port)
    assert ok
    time.sleep(0.2)
    assert received, "未收到 UDP 包"
    assert received[0].startswith(b"/chatbox/input\x00\x00"), received[0][:20]
    assert b"\xe5\xa4\xa7\xe5\xae\xb6\xe5\xa5\xbd" in received[0]  # UTF-8 "大家好"
    assert b",sTF" in received[0]  # notify=True, interrupt=False
    srv.close()


def test_filter_noise():
    assert filter_noise_text("你好世界") == "你好世界"
    assert filter_noise_text("hello world") == "hello world"
    assert filter_noise_text(" 好的 ") == "好的"
    for bad in ["", "   ", "。。。", "。。。,!", "嗯", "嗯嗯", "咳咳", "啊", "哦哦","1."
                "emmm", "uh", "hmm", "emm", "哼"]:
        assert filter_noise_text(bad) is None, f"应过滤: {bad!r}"
    assert filter_noise_text("嗯 好的") is not None  # 有实义内容保留


def test_ui_smoke():
    import tkinter as tk
    from mimo_asr.ui import App
    root = tk.Tk()
    app = App(root)
    root.update()  # 完成布局，使 Canvas 有真实宽度
    # 直接注入结果，验证过滤 + 展示流程（不真实请求）
    app._on_result("测试", "你好世界", {"seconds": 0.1, "prompt_tokens": 3}, time.time(), False)
    content = app.result_text.get("1.0", "end")
    assert "你好世界" in content
    app._on_result("测试", "嗯", {"seconds": 0.1}, time.time(), False)  # 语气词 → 不显示
    assert content == app.result_text.get("1.0", "end")
    # 音量条点击设置阈值
    class Ev:
        x = 100
    app._on_meter_click(Ev())
    assert 0 < app.cfg["vad_threshold"] < 1, app.cfg["vad_threshold"]
    app.on_close()
    print("UI smoke OK")


if __name__ == "__main__":
    for fn in [test_vad_segment_trigger, test_vad_tail_preserved, test_vad_short_noise_dropped,
               test_vad_low_volume_ignored,
               test_osc_format, test_send_chatbox_udp, test_filter_noise, test_ui_smoke]:
        fn()
        print(f"PASS: {fn.__name__}")
    print("ALL FEATURE TESTS PASSED")
