"""VAD 持续监听：音量超过阈值开始缓冲，静音超时自动切段。

- 回调线程内做轻量状态机（无锁、无阻塞）
- 段时长 < min_speech_sec 直接丢弃 —— 过滤咳嗽、清痰等短促非语音
- 切段后进入短暂冷却，避免尾音余响立刻再触发
"""
from __future__ import annotations

from typing import Callable, List, Optional

import numpy as np
import sounddevice as sd

from .recorder import CHANNELS, DTYPE, _numpy_to_wav

# 段时长过滤：短于此秒数的音频视为非语音噪声，不回调
DEFAULT_MIN_SPEECH_SEC = 0.5
# 切段后冷却：此秒数内不重新触发
COOLDOWN_SEC = 0.3


class VadListener:
    def __init__(self, sample_rate: int = 16000, device: Optional[int] = None,
                 threshold: float = 0.02,
                 silence_timeout: float = 0.8,
                 min_speech_sec: float = DEFAULT_MIN_SPEECH_SEC,
                 on_segment: Optional[Callable[[bytes, float], None]] = None,
                 on_level: Optional[Callable[[float], None]] = None):
        """
        on_segment: 一段语音完成时回调 (wav_bytes, 时长秒) —— 音频线程内
        on_level:   每帧实时音量回调 (0~1) —— 音频线程内
        """
        self.sample_rate = sample_rate
        self.device = device
        self.threshold = threshold
        self.silence_timeout = silence_timeout
        self.min_speech_sec = min_speech_sec
        self.on_segment = on_segment
        self.on_level = on_level
        self.level = 0.0  # UI 可轮询读取

        self._stream: Optional[sd.InputStream] = None
        self._chunks: List[np.ndarray] = []
        self._state = "idle"          # idle | speech | cooldown
        self._frame_count = 0
        self._last_voice_frame = 0
        self._cooldown_until = 0
        self._voice_frames = 0        # 有效语音帧数（音量≥阈值），用于短音过滤

    @property
    def listening(self) -> bool:
        return self._stream is not None

    def _callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        rms = float(np.sqrt(np.mean(np.square(indata.astype(np.float32)))))
        self.level = min(1.0, rms / 32768.0)
        if self.on_level:
            self.on_level(self.level)

        n = self._frame_count
        self._frame_count += frames
        sr = self.sample_rate

        if self._state == "cooldown":
            if n >= self._cooldown_until:
                self._state = "idle"
            return

        if self.level >= self.threshold:
            if self._state == "idle":
                self._state = "speech"
                self._chunks = []
                self._voice_frames = 0
            self._last_voice_frame = n
            self._voice_frames += frames
            self._chunks.append(indata.copy())
        elif self._state == "speech":
            if (n - self._last_voice_frame) >= self.silence_timeout * sr:
                self._finish_segment()
            else:
                # 低于阈值但未超时：仍缓冲该帧 —— 保留句尾弱音/音量下降部分的语音，
                # 避免"最后低于阈值的语音被截断"的问题
                self._chunks.append(indata.copy())

    def _finish_segment(self) -> None:
        self._state = "cooldown"
        self._cooldown_until = self._frame_count + int(COOLDOWN_SEC * self.sample_rate)
        chunks, self._chunks = self._chunks, []
        voice_duration = self._voice_frames / self.sample_rate
        self._voice_frames = 0
        if not chunks:
            return
        data = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
        duration = len(data) / self.sample_rate
        # 按"有效语音时长"过滤：咳嗽/清痰等有效语音过短，直接丢弃（尾部静音不算）
        if voice_duration < self.min_speech_sec:
            return
        wav = _numpy_to_wav(data, self.sample_rate)
        if self.on_segment:
            self.on_segment(wav, duration)

    def start(self) -> None:
        if self.listening:
            return
        self._chunks = []
        self._state = "idle"
        self._frame_count = 0
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

    def stop(self) -> None:
        """停止监听；若有未完成的语音段，立即切段回调（flush）。"""
        if not self.listening:
            return
        try:
            self._stream.stop()
            self._stream.close()
        finally:
            self._stream = None
        if self._state == "speech":
            self._finish_segment()
        else:
            self._chunks = []
        self._state = "idle"
