"""麦克风录音：sounddevice 采集 16kHz 单声道 int16 PCM。

- 支持指定输入设备（列表枚举 / 按 "hostapi:设备名" 持久化标识恢复）
- 每次回调计算实时音量 level（int16 RMS 归一化 0~1），供音量条/阈值用
- 停止时把缓冲区合并成标准 WAV 字节
"""
from __future__ import annotations

import io
import threading
import wave
from typing import Callable, List, Optional, Tuple

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"


def list_input_devices() -> List[Tuple[str, str, int]]:
    """返回 [(标识 "hostapi:设备名", 显示名, 输入声道数), ...]，按默认设备在前排序。"""
    hostapis = sd.query_hostapis()
    default_in = sd.default.device[0] if sd.default.device and sd.default.device[0] is not None else 0
    items = []
    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            ha = hostapis[dev["hostapi"]]["name"]
            key = f"{ha}:{dev['name']}"
            items.append((key, f"{dev['name']}  [{ha}]", int(dev["max_input_channels"])))
    items.sort(key=lambda x: (x[0] != items[default_in][0] if items else False, x[1]))
    # 默认设备放最前（若列表非空）
    if items:
        default_key = items[default_in][0] if default_in < len(items) else items[0][0]
        items.sort(key=lambda x: (x[0] != default_key, x[1]))
    return items


def resolve_device(device_key: str) -> Optional[int]:
    """把 "hostapi:设备名" 解析回 sounddevice 设备索引；找不到返回 None（用默认）。"""
    if not device_key:
        return None
    try:
        hostapis = sd.query_hostapis()
        for i, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] <= 0:
                continue
            key = f"{hostapis[dev['hostapi']]['name']}:{dev['name']}"
            if key == device_key:
                return i
    except Exception:
        pass
    return None


class Recorder:
    def __init__(self, sample_rate: int = SAMPLE_RATE, device: Optional[int] = None,
                 on_audio: Optional[Callable[[bytes], None]] = None,
                 on_level: Optional[Callable[[float], None]] = None):
        self.sample_rate = sample_rate
        self.device = device
        self.on_audio = on_audio          # 录音停止后回调 (wav_bytes)
        self.on_level = on_level          # 每帧回调实时音量（0~1），音频线程内
        self.level = 0.0                  # 最新音量，UI 可轮询读取
        self._stream: Optional[sd.InputStream] = None
        self._chunks: List[np.ndarray] = []
        self._lock = threading.Lock()   # 保护 _chunks，避免音频线程/主线程竞争

    @property
    def recording(self) -> bool:
        return self._stream is not None

    def _callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        # PortAudio 线程调用；只做轻量计算与拷贝
        rms = float(np.sqrt(np.mean(np.square(indata.astype(np.float32)))))
        self.level = min(1.0, rms / 32768.0)
        if self.on_level:
            self.on_level(self.level)
        with self._lock:
            self._chunks.append(indata.copy())

    def start(self) -> None:
        if self.recording:
            return
        self._chunks = []
        self.level = 0.0
        try:
            self._open_stream(self.device)
        except sd.PortAudioError as e:
            # 部分设备（如 WDM-KS）不支持 16kHz / 已被拔出：回退默认设备重试
            if self.device is not None:
                try:
                    self._open_stream(None)
                    return
                except Exception:
                    pass
            self._stream = None
            raise RuntimeError(f"无法打开麦克风：{e}")
        except Exception as e:
            self._stream = None
            raise RuntimeError(f"无法打开麦克风：{e}")

    def _open_stream(self, device: Optional[int]) -> None:
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=CHANNELS,
            dtype=DTYPE,
            device=device,
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> Optional[bytes]:
        """停止录音并返回 WAV 字节；未采集到声音返回 None。"""
        if not self.recording:
            return None
        try:
            self._stream.stop()
            self._stream.close()
        finally:
            self._stream = None

        with self._lock:
            if not self._chunks:
                return None
            data = np.concatenate(self._chunks) if len(self._chunks) > 1 else self._chunks[0]
            self._chunks = []
        wav = _numpy_to_wav(data, self.sample_rate)
        if self.on_audio:
            self.on_audio(wav)
        return wav


def _numpy_to_wav(data: np.ndarray, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(2)  # int16
        w.setframerate(sample_rate)
        w.writeframes(data.tobytes())
    return buf.getvalue()
