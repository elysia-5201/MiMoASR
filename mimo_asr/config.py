"""配置读写：API Key、语言、模型等，保存在用户 AppData 下（打包后也可写）。"""
from __future__ import annotations

import json
import os
from pathlib import Path

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


def _env_api_key() -> str:
    return os.environ.get("MIMO_API_KEY", "").strip()


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        if CONFIG_FILE.exists():
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass  # 配置损坏时回退默认值
    if not cfg.get("api_key"):
        cfg["api_key"] = _env_api_key()
    return cfg


def save(cfg: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )
