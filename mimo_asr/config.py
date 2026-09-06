"""配置读写：API Key、语言、模型等，保存在用户 AppData 下（打包后也可写）。

安全说明：
- Windows 下 API Key 使用 DPAPI 加密后写入 config.json；
- 其他平台优先使用 keyring，若不可用则降级为普通文件并尽量收紧权限（0600）；
- 旧版明文 config.json 仍可自动读取，并在下次保存时升级为加密存储。
"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

try:
    import keyring
except Exception:  # pragma: no cover - 可选依赖
    keyring = None

APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "MiMoASR"
CONFIG_FILE = APP_DIR / "config.json"

DEFAULTS: dict = {
    "api_key": "",              # 小米 MiMo 开放平台 API Key
    "language": "auto",         # asr_options.language
    "model": "mimo-v2.5-asr",   # 官方当前 ASR 模型
    "base_url": "https://api.xiaomimimo.com/v1/chat/completions",
    "sample_rate": 16000,       # 录音采样率（16k 单声道，文件小、识别快）
    # --- 麦克风 ---
    "input_device": "",         # 设备标识 "hostapi:设备名"，空 = 系统默认
    # --- 自动识别（VAD）---
    "vad_threshold": 0.02,      # 触发阈值：int16 RMS 归一化到 0~1
    "silence_timeout": 0.8,     # 静音多少秒后结束一段语音（秒）
    "min_speech_sec": 0.5,      # 最短语音段，短于此丢弃（过滤咳嗽/清痰等）
    # --- VRChat 发送 ---
    "vrc_enabled": False,       # 识别结果自动发送到 VRChat chatbox
    "vrc_host": "127.0.0.1",
    "vrc_port": 9000,
    # --- 过滤 ---
    "filter_noise": True,       # 过滤无效信息（语气词/短音/非语音）
}

_ENC_PREFIX = "enc:v1:"


def _env_api_key() -> str:
    return os.environ.get("MIMO_API_KEY", "").strip()


# ------------------------- Windows DPAPI -------------------------
def _dpapi_protect(secret: str) -> str:
    """Windows DPAPI 加密，返回 base64 密文。"""
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_byte))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    LocalFree = kernel32.LocalFree
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(DATA_BLOB),
        wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL

    data = secret.encode("utf-8")
    buf = ctypes.create_string_buffer(data)
    blob_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    blob_out = DATA_BLOB()
    if not crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise ctypes.WinError()
    try:
        raw = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        return base64.b64encode(raw).decode("ascii")
    finally:
        LocalFree(blob_out.pbData)


def _dpapi_unprotect(payload: str) -> str:
    """Windows DPAPI 解密 base64 密文。"""
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_byte))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    LocalFree = kernel32.LocalFree
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(DATA_BLOB), wintypes.LPVOID, wintypes.LPVOID,
        wintypes.DWORD, ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL

    raw = base64.b64decode(payload)
    buf = ctypes.create_string_buffer(raw)
    blob_in = DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    blob_out = DATA_BLOB()
    if not crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData).decode("utf-8")
    finally:
        LocalFree(blob_out.pbData)


# ------------------------- 加解密封装 -------------------------
def _protect(secret: str) -> str:
    """加密 API Key。Windows 用 DPAPI；其他平台用 keyring；都不行则返回明文（文件权限 0600）。"""
    if not secret:
        return ""
    if sys.platform == "win32":
        try:
            return _ENC_PREFIX + _dpapi_protect(secret)
        except Exception:
            pass  # 降级到 keyring / 明文
    if keyring is not None:
        try:
            keyring.set_password("MiMoASR", "api_key", secret)
            return _ENC_PREFIX + "keyring"
        except Exception:
            pass
    return secret


def _unprotect(value: str) -> str:
    """解密 API Key；兼容旧版明文。"""
    if not value:
        return ""
    if value.startswith(_ENC_PREFIX):
        payload = value[len(_ENC_PREFIX):]
        if payload == "keyring":
            if keyring is not None:
                try:
                    return keyring.get_password("MiMoASR", "api_key") or ""
                except Exception:
                    return ""
            return ""
        if sys.platform == "win32":
            try:
                return _dpapi_unprotect(payload)
            except Exception:
                return ""
        return ""
    return value


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        if CONFIG_FILE.exists():
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass  # 配置损坏时回退默认值
    try:
        cfg["api_key"] = _unprotect(str(cfg.get("api_key") or ""))
    except Exception:
        cfg["api_key"] = ""
    if not cfg.get("api_key"):
        cfg["api_key"] = _env_api_key()
    return cfg


def save(cfg: dict) -> None:
    data = dict(cfg)
    key = str(data.get("api_key") or "").strip()
    data["api_key"] = _protect(key) if key else ""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if os.name != "nt":
        try:
            os.chmod(CONFIG_FILE, 0o600)
        except OSError:
            pass
