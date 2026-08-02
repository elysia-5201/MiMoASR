"""VRChat OSC 发送（手写 OSC 协议，零第三方依赖）。

VRChat OSC API:
- /chatbox/input  (string message, bool notify, bool interrupt)  → 端口 9000
- /chatbox/typing (bool typing)
UDP 无连接，VRChat 未运行 / 未开 OSC 时静默丢弃，不影响程序。
"""
from __future__ import annotations

import socket
import struct
from typing import Union

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9000


def _align(b: bytes) -> bytes:
    """OSC 元素按 4 字节对齐（补 \x00）。"""
    pad = (4 - len(b) % 4) % 4
    return b + b"\x00" * pad


def _osc_string(s: str) -> bytes:
    return _align(s.encode("utf-8") + b"\x00")


def build_osc(address: str, type_tags: str, *args) -> bytes:
    """构造一条 OSC 消息字节流。type_tags 中 s=字符串, i=int32, f=float32, T/F=bool(无数据)。"""
    msg = _osc_string(address) + _osc_string("," + type_tags)
    for tag, arg in zip(type_tags, args):
        if tag == "s":
            msg += _osc_string(arg)
        elif tag == "i":
            msg += struct.pack(">i", int(arg))
        elif tag == "f":
            msg += struct.pack(">f", float(arg))
        # T / F 无参数数据
    return msg


def _send(msg: bytes, host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(msg, (host, port))
        return True
    except OSError:
        return False


def send_chatbox(message: str, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 notify: bool = True, interrupt: bool = False) -> bool:
    """发送文本到 VRChat chatbox。返回是否发送成功（UDP 是否投递，不代表 VRChat 收到）。"""
    tags = "s" + ("T" if notify else "F") + ("T" if interrupt else "F")
    return _send(build_osc("/chatbox/input", tags, message), host, port)


def send_typing(typing: bool, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> bool:
    """设置 VRChat chatbox 打字中状态。"""
    return _send(build_osc("/chatbox/typing", "T" if typing else "F"), host, port)


# 便捷函数：发送前可先设 typing 提示
def send_with_typing(message: str, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                     notify: bool = True, interrupt: bool = False) -> bool:
    send_typing(False, host, port)
    return send_chatbox(message, host, port, notify, interrupt)
