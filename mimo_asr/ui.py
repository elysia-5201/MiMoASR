"""Tkinter 主界面：录音 / 文件识别 / 自动识别(VAD) / VRChat 发送。

- 麦克风下拉选择，选择即保存，下次启动自动恢复
- 实时音量条，条上可单击/拖动设置触发阈值
- 自动识别：VAD 音量触发、静音切段，识别串行队列，不并发堆积
- 识别结果可选自动发送到 VRChat chatbox（OSC）
- 无效信息过滤：非语音(咳嗽/清痰)由 ASR 空结果 + 最短时长丢弃 + 语气词文本清洗
"""
from __future__ import annotations

import os
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Optional, Tuple

from . import asr_client, vrc
from .config import load as load_config, save as save_config
from .recorder import Recorder, list_input_devices, resolve_device
from .vad import VadListener

MIME_MAP = {
    ".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
    ".aac": "audio/aac", ".flac": "audio/flac", ".ogg": "audio/ogg",
    ".opus": "audio/ogg", ".webm": "audio/webm", ".wma": "audio/x-ms-wma",
}
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25MB，防止超大文件占满内存/请求超时
LANGUAGES = ["auto", "zh", "en", "ja", "ko", "ru", "fr", "de", "es"]

IDLE_TEXT = "🎙 按住录音（点击开始/停止）" if os.name == "nt" else "🎙 录音"
REC_TEXT = "⏹ 停止并识别"
AUTO_OFF_TEXT = "🎧 自动识别"
AUTO_ON_TEXT = "⏹ 停止自动识别"

# 无效信息过滤：纯语气词/无意义短音不显示、不发送
NOISE_WORDS = {
    "嗯", "啊", "哦", "呃", "唉", "咳", "哼", "哈", "呀", "唔", "恩", "诶",
    "嗯嗯", "哦哦", "啊啊", "哈哈", "咳咳", "呃呃", "嘿嘿", "呜呜",
    "emm", "em", "emmm", "ah", "uh", "um", "hmm", "hm", "a", "o", "en","1.","0.","mm."
}
PURE_SINGLE = "嗯啊哦呃唉咳哼哈呀唔恩诶嘿"

METER_H = 30  # 音量条高度


def filter_noise_text(text: str) -> Optional[str]:
    """无效信息过滤：空串、纯标点、语气词/无意义短音 → 返回 None（不显示、不发送）。"""
    t = (text or "").strip()
    if not t:
        return None
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "", t)  # 去掉标点/符号/空白，保留中英文数字
    if not cleaned:
        return None
    low = cleaned.lower()
    if low in NOISE_WORDS:
        return None
    if len(cleaned) <= 2 and all(ch in PURE_SINGLE for ch in cleaned):
        return None
    return t


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = load_config()
        self.recorder: Optional[Recorder] = None
        self.vad: Optional[VadListener] = None
        self.busy = False          # 手动模式是否正在识别
        self.auto_mode = False     # 自动识别模式
        self._q: "queue.Queue[Tuple]" = queue.Queue()
        self._last_text = ""       # 最近一次有效识别文本（供手动发送 VRC）
        self._device_map: dict = {}
        self._meter_level_val = 0.0

        root.title("MiMo 语音识别")
        root.geometry("700x620")
        root.minsize(620, 540)

        self._build_ui()
        self._set_busy(False)
        threading.Thread(target=self._queue_worker, daemon=True).start()
        self._refresh_meter()

    # ================= UI 构建 =================
    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 4}

        # ---- 设置区 ----
        top = ttk.LabelFrame(self.root, text=" 设置 ")
        top.pack(fill="x", **pad)

        row1 = ttk.Frame(top)
        row1.pack(fill="x", padx=8, pady=(6, 2))
        ttk.Label(row1, text="API Key:").pack(side="left")
        self.key_var = tk.StringVar(value=self.cfg["api_key"])
        self.key_entry = ttk.Entry(row1, textvariable=self.key_var, show="•", width=44)
        self.key_entry.pack(side="left", fill="x", expand=True, padx=6)
        self.show_key_btn = ttk.Button(row1, text="显示", width=5, command=self._toggle_key)
        self.show_key_btn.pack(side="left")

        row2 = ttk.Frame(top)
        row2.pack(fill="x", padx=8, pady=(2, 6))
        ttk.Label(row2, text="麦克风:").pack(side="left")
        self.mic_var = tk.StringVar()
        self.mic_box = ttk.Combobox(row2, textvariable=self.mic_var, state="readonly", width=32)
        self.mic_box.pack(side="left", padx=6)
        self.mic_box.bind("<<ComboboxSelected>>", self._on_mic_selected)
        self._refresh_devices()

        self.lang_var = tk.StringVar(value=self.cfg["language"])
        ttk.Label(row2, text="语言:").pack(side="left", padx=(10, 0))
        ttk.Combobox(row2, textvariable=self.lang_var, values=LANGUAGES,
                     state="readonly", width=7).pack(side="left", padx=4)
        self.model_var = tk.StringVar(value=self.cfg["model"])
        ttk.Label(row2, text="模型:").pack(side="left", padx=(10, 0))
        ttk.Entry(row2, textvariable=self.model_var, width=16).pack(side="left", padx=4)

        row3 = ttk.Frame(top)
        row3.pack(fill="x", padx=8, pady=(0, 6))
        self.vrc_var = tk.BooleanVar(value=bool(self.cfg.get("vrc_enabled", False)))
        ttk.Checkbutton(row3, text="识别结果发送到 VRChat chatbox",
                        variable=self.vrc_var, command=self._on_vrc_toggle).pack(side="left")
        ttk.Label(row3, text="OSC 端口:").pack(side="left", padx=(12, 2))
        self.vrc_port_var = tk.StringVar(value=str(self.cfg.get("vrc_port", 9000)))
        ttk.Entry(row3, textvariable=self.vrc_port_var, width=6).pack(side="left")
        ttk.Label(row3, text="(127.0.0.1) 需在 VRChat 中开启 OSC").pack(side="left", padx=10)
        ttk.Label(row3, text="API Key 存于本地，可用环境变量 MIMO_API_KEY").pack(side="right")

        # ---- 音量区（阈值线可拖动）----
        meter_frame = ttk.LabelFrame(self.root, text=" 输入音量 — 在条上单击/拖动设置触发阈值 ")
        meter_frame.pack(fill="x", **pad)
        self.meter_canvas = tk.Canvas(meter_frame, height=METER_H, bg="#1e1e1e",
                                      highlightthickness=1, highlightbackground="#333333")
        self.meter_canvas.pack(fill="x", padx=8, pady=(4, 0))
        self.meter_canvas.bind("<Button-1>", self._on_meter_click)
        self.meter_canvas.bind("<B1-Motion>", self._on_meter_click)
        self.meter_canvas.bind("<Configure>", lambda e: self._refresh_meter())
        meter_row = ttk.Frame(meter_frame)
        meter_row.pack(fill="x", padx=10, pady=(2, 4))
        self.thresh_label = ttk.Label(meter_row, text="")
        self.thresh_label.pack(side="left")
        ttk.Label(meter_row, text="   静音切段(秒):").pack(side="left")
        self.silence_var = tk.StringVar(value=str(self.cfg.get("silence_timeout", 0.8)))
        self.silence_entry = ttk.Entry(meter_row, textvariable=self.silence_var, width=5)
        self.silence_entry.pack(side="left")
        self.silence_entry.bind("<Return>", self._on_silence_change)
        self.silence_entry.bind("<FocusOut>", self._on_silence_change)
        ttk.Label(meter_row, text="  说话音量低于阈值后, 静音这么久才切段").pack(side="left", padx=6)
        self._update_thresh_label()

        # ---- 操作按钮 ----
        btns = ttk.Frame(self.root)
        btns.pack(fill="x", **pad)
        self.rec_btn = ttk.Button(btns, text=IDLE_TEXT, command=self._on_record_click, width=24)
        self.rec_btn.pack(side="left")
        self.auto_btn = ttk.Button(btns, text=AUTO_OFF_TEXT, command=self._toggle_auto, width=14)
        self.auto_btn.pack(side="left", padx=6)
        ttk.Button(btns, text="📁 文件", command=self._on_pick_file).pack(side="left")
        ttk.Button(btns, text="▶ 发VRC", command=self._send_vrc_manual).pack(side="left", padx=6)
        ttk.Button(btns, text="📋 复制", command=self._copy_result).pack(side="left")
        ttk.Button(btns, text="🧹 清空", command=self._clear_result).pack(side="left", padx=6)

        # ---- 结果区 ----
        result_frame = ttk.LabelFrame(self.root, text=" 识别结果 ")
        result_frame.pack(fill="both", expand=True, **pad)
        self.result_text = tk.Text(result_frame, wrap="word", height=12,
                                   font=("Microsoft YaHei UI", 11))
        self.result_text.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        sb = ttk.Scrollbar(result_frame, command=self.result_text.yview)
        sb.pack(side="right", fill="y", pady=6)
        self.result_text.configure(yscrollcommand=sb.set)

        # ---- 状态栏 ----
        self.status_var = tk.StringVar(value="就绪。选择麦克风后点击「录音」，或开启「自动识别」。")
        status = ttk.Label(self.root, textvariable=self.status_var, anchor="w", relief="sunken")
        status.pack(fill="x", side="bottom")

    def _refresh_devices(self) -> None:
        try:
            devices = list_input_devices()
        except Exception:
            devices = []
        self._device_map = {disp: key for key, disp, _ in devices}
        names = [disp for _, disp, _ in devices]
        self.mic_box["values"] = names
        saved = self.cfg.get("input_device", "")
        sel = next((disp for disp, key in self._device_map.items() if key == saved), None)
        if not sel and names:
            sel = names[0]
        self.mic_var.set(sel or "")

    # ================= 麦克风 =================
    def _on_mic_selected(self, event=None) -> None:
        disp = self.mic_var.get()
        key = self._device_map.get(disp, "")
        self.cfg["input_device"] = key
        save_config(self.cfg)
        # 正在自动识别时立即切换设备
        if self.auto_mode and self.vad:
            self._restart_vad()
        self.status_var.set(f"麦克风已切换: {disp}")

    def _restart_vad(self) -> None:
        try:
            self.vad.stop()
        except Exception:
            pass
        self.vad = None
        self._start_vad()

    # ================= 音量条 / 阈值 / 静音切段 =================
    def _on_meter_click(self, event) -> None:
        w = self.meter_canvas.winfo_width()
        if w <= 0:
            return
        t = max(0.001, min(1.0, event.x / w))
        self.cfg["vad_threshold"] = round(t, 3)
        if self.vad:
            self.vad.threshold = t
        self._update_thresh_label()
        self._refresh_meter()

    def _update_thresh_label(self) -> None:
        self.thresh_label.configure(
            text=f"触发阈值: {float(self.cfg.get('vad_threshold', 0.02)):.3f}")

    def _on_silence_change(self, event=None) -> None:
        try:
            t = float(self.silence_var.get())
            t = max(0.2, min(5.0, t))  # 限制 0.2~5 秒
        except ValueError:
            t = 0.8
        self.silence_var.set(str(t))
        self.cfg["silence_timeout"] = t
        if self.vad:
            self.vad.silence_timeout = t  # 正在监听时实时生效
        save_config(self.cfg)
        self.status_var.set(f"静音切段已设为 {t}s（说话音量低于阈值后静音 {t}s 自动切段）")

    def _current_level(self) -> float:
        if self.vad and self.vad.listening:
            return self.vad.level
        if self.recorder and self.recorder.recording:
            return self.recorder.level
        return 0.0

    def _refresh_meter(self) -> None:
        c = self.meter_canvas
        w = c.winfo_width() or 640
        level = self._current_level()
        self._meter_level_val = level
        c.delete("all")
        c.create_rectangle(0, 0, w, METER_H, fill="#202020", outline="")
        # 音量填充：<50% 绿，<80% 黄，以上红
        lw = int(w * level)
        if lw > 0:
            green_w = min(lw, int(w * 0.5))
            if green_w > 0:
                c.create_rectangle(0, 3, green_w, METER_H - 3, fill="#2ecc71", outline="")
            yellow_w = min(lw, int(w * 0.8))
            if yellow_w > green_w:
                c.create_rectangle(green_w, 3, yellow_w, METER_H - 3, fill="#f1c40f", outline="")
            if lw > yellow_w:
                c.create_rectangle(yellow_w, 3, lw, METER_H - 3, fill="#e74c3c", outline="")
        # 阈值线（红）
        tw = int(w * float(self.cfg.get("vad_threshold", 0.02)))
        c.create_line(tw, 0, tw, METER_H, fill="#ff5252", width=2)
        c.create_polygon(tw - 5, 0, tw + 5, 0, tw, 7, fill="#ff5252", outline="")
        # 电平数值
        c.create_text(8, METER_H // 2, anchor="w",
                      text=f"{level * 100:3.0f}%", fill="#cccccc",
                      font=("Consolas", 9))
        self.root.after(80, self._refresh_meter)

    # ================= 手动录音 =================
    def _on_record_click(self) -> None:
        if self.busy:
            return
        if self.recorder and self.recorder.recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self) -> None:
        self.recorder = Recorder(
            sample_rate=int(self.cfg["sample_rate"]),
            device=resolve_device(self.cfg.get("input_device", "")),
            on_audio=self._on_wav_ready,
        )
        try:
            self.recorder.start()
        except Exception as e:
            messagebox.showerror("录音失败", str(e))
            self.recorder = None
            return
        self.rec_btn.configure(text=REC_TEXT)
        self.status_var.set("录音中… 再次点击「停止并识别」")

    def _stop_recording(self) -> None:
        self.rec_btn.configure(state="disabled")
        self.status_var.set("停止录音，正在处理…")
        wav = self.recorder.stop() if self.recorder else None
        self.rec_btn.configure(state="normal", text=IDLE_TEXT)
        if not wav:
            self.status_var.set("未采集到声音，请重试")
            return
        self._enqueue(wav, "audio/wav", "麦克风录音", auto=False)

    def _on_wav_ready(self, wav: bytes) -> None:
        pass  # 手动流程在 _stop_recording 中处理

    # ================= 自动识别 =================
    def _toggle_auto(self) -> None:
        if self.auto_mode:
            self._stop_auto()
        else:
            self._start_auto()

    def _start_auto(self) -> None:
        if self.busy:
            messagebox.showinfo("提示", "请等待当前识别完成")
            return
        self._start_vad()
        if self.vad is None:
            return
        self.auto_mode = True
        self.auto_btn.configure(text=AUTO_ON_TEXT)
        self.rec_btn.configure(state="disabled")
        self.status_var.set("自动识别中… 说话即识别，静音自动切段，结果可自动发送 VRC")

    def _start_vad(self) -> None:
        self.vad = VadListener(
            sample_rate=int(self.cfg["sample_rate"]),
            device=resolve_device(self.cfg.get("input_device", "")),
            threshold=float(self.cfg.get("vad_threshold", 0.02)),
            silence_timeout=float(self.cfg.get("silence_timeout", 0.8)),
            min_speech_sec=float(self.cfg.get("min_speech_sec", 0.5)),
            on_segment=self._on_vad_segment,
        )
        try:
            self.vad.start()
        except Exception as e:
            messagebox.showerror("自动识别启动失败", str(e))
            self.vad = None

    def _stop_auto(self) -> None:
        vad, self.vad = self.vad, None
        if vad:
            try:
                vad.stop()  # 会 flush 未完成段
            except Exception:
                pass
        self.auto_mode = False
        self.auto_btn.configure(text=AUTO_OFF_TEXT)
        self.rec_btn.configure(state="normal" if not self.busy else "disabled")
        self.status_var.set("已停止自动识别")

    def _on_vad_segment(self, wav: bytes, duration: float) -> None:
        # 音频线程回调：只入队，不碰 UI
        try:
            self._q.put(("自动识别", wav, "audio/wav", True, time.time()))
            self._safe_after(self.status_var.set, f"已切段 {duration:.1f}s，识别中…")
        except Exception:
            pass

    # ================= 文件识别 =================
    def _on_pick_file(self) -> None:
        if self.busy:
            return
        path = filedialog.askopenfilename(
            title="选择音频文件",
            filetypes=[("音频文件", "*.wav *.mp3 *.m4a *.aac *.flac *.ogg *.opus *.webm *.wma"),
                       ("所有文件", "*.*")],
        )
        if not path:
            return
        ext = os.path.splitext(path)[1].lower()
        mime = MIME_MAP.get(ext, "audio/wav")
        try:
            size = os.path.getsize(path)
        except OSError as e:
            messagebox.showerror("读取失败", str(e))
            return
        if size > MAX_FILE_SIZE:
            messagebox.showerror(
                "文件过大",
                f"文件超过 {MAX_FILE_SIZE // (1024 * 1024)}MB，请选择更小的音频文件",
            )
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            messagebox.showerror("读取失败", str(e))
            return
        self._enqueue(data, mime, os.path.basename(path), auto=False)

    # ================= 识别队列（串行）================
    def _enqueue(self, data: bytes, mime: str, source: str, auto: bool) -> None:
        if not auto:
            self._set_busy(True)
            self.status_var.set(f"正在识别「{source}」( {len(data) // 1024} KB )…")
        self._q.put((source, data, mime, auto, time.time()))

    def _queue_worker(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            source, data, mime, auto, started = item
            try:
                api_key = self.key_var.get().strip() or self.cfg.get("api_key", "")
                lang = self.lang_var.get()
                model = self.model_var.get().strip() or "mimo-v2.5-asr"
                base_url = self.cfg.get("base_url", "https://api.xiaomimimo.com/v1/chat/completions")
                text, usage = asr_client.recognize(
                    data, mime, api_key=api_key, base_url=base_url,
                    model=model, language=lang,
                )
                self._safe_after(self._on_result, source, text, usage, started, auto)
            except asr_client.AsrError as e:
                self._safe_after(self._on_result_err, str(e), auto)
            except Exception as e:
                self._safe_after(self._on_result_err, f"未知错误：{e}", auto)

    def _on_result(self, source: str, text: str, usage: dict, started: float, auto: bool) -> None:
        filtered = filter_noise_text(text) if self.cfg.get("filter_noise", True) else (text or "").strip()
        if not filtered:
            if auto:
                self.status_var.set("已过滤无效音频（咳嗽/杂音等），不显示不发送")
            else:
                self.status_var.set("未识别到有效语音（可能为杂音/语气词）")
            self._set_busy(False)
            return

        self._last_text = filtered
        self.result_text.insert("end", f"【{source}】 {time.strftime('%H:%M:%S')}\n")
        self.result_text.insert("end", filtered + "\n\n")
        self.result_text.see("end")
        sec = usage.get("seconds", "-")
        tok = usage.get("prompt_tokens", "-")
        msg = f"完成 ✓  耗时 {sec}s  tokens {tok}"
        if self.vrc_var.get():
            ok = self._send_vrc(filtered)
            msg += "  →  VRC ✓" if ok else "  →  VRC 发送失败"
        self.status_var.set(msg)
        self._set_busy(False)

    def _on_result_err(self, msg: str, auto: bool) -> None:
        self._set_busy(False)
        if auto:
            self.status_var.set(f"识别失败：{msg}")
        else:
            self.status_var.set("识别失败 ✗")
            messagebox.showerror("识别失败", msg)

    # ================= 无效信息过滤 =================
    # ================= VRC 发送 =================
    def _vrc_addr(self) -> Tuple[str, int]:
        host = str(self.cfg.get("vrc_host", "127.0.0.1") or "127.0.0.1")
        try:
            port = int(self.vrc_port_var.get() or 9000)
        except ValueError:
            port = 9000
        return host, port

    def _send_vrc(self, text: str) -> bool:
        host, port = self._vrc_addr()
        return vrc.send_chatbox(text, host=host, port=port)

    def _send_vrc_manual(self) -> None:
        text = self._last_text
        if not text:
            self.status_var.set("暂无可发送的结果（先完成一次识别）")
            return
        ok = self._send_vrc(text)
        self.status_var.set("已发送到 VRC ✓" if ok else "VRC 发送失败（检查 OSC 是否开启）")

    def _on_vrc_toggle(self) -> None:
        self.cfg["vrc_enabled"] = bool(self.vrc_var.get())
        save_config(self.cfg)

    # ================= 其他交互 =================
    def _toggle_key(self) -> None:
        if self.key_entry.cget("show"):
            self.key_entry.configure(show="")
            self.show_key_btn.configure(text="隐藏")
        else:
            self.key_entry.configure(show="•")
            self.show_key_btn.configure(text="显示")

    def _copy_result(self) -> None:
        text = self.result_text.get("1.0", "end").strip()
        if not text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status_var.set("已复制到剪贴板 ✓")

    def _clear_result(self) -> None:
        self.result_text.delete("1.0", "end")

    def _safe_after(self, func, *args) -> None:
        try:
            self.root.after(0, func, *args)
        except tk.TclError:
            pass

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        if self.auto_mode:
            return  # 自动模式下手动按钮保持禁用
        state = "disabled" if busy else "normal"
        self.rec_btn.configure(state=state)
        self.auto_btn.configure(state=state)

    # ================= 生命周期 =================
    def on_close(self) -> None:
        if self.auto_mode and self.vad:
            try:
                self.vad.stop()
            except Exception:
                pass
        if self.recorder and self.recorder.recording:
            try:
                self.recorder.stop()
            except Exception:
                pass
        # 保存设置
        if self.key_var.get().strip():
            self.cfg["api_key"] = self.key_var.get().strip()
        self.cfg["language"] = self.lang_var.get()
        self.cfg["model"] = self.model_var.get().strip()
        try:
            self.cfg["vad_threshold"] = round(float(self.cfg.get("vad_threshold", 0.02)), 3)
        except Exception:
            pass
        try:
            self.cfg["silence_timeout"] = float(self.silence_var.get())
        except ValueError:
            pass
        try:
            self.cfg["vrc_port"] = int(self.vrc_port_var.get() or 9000)
        except ValueError:
            pass
        self.cfg["vrc_enabled"] = bool(self.vrc_var.get())
        self.cfg["input_device"] = self._device_map.get(
            self.mic_var.get(), self.cfg.get("input_device", ""))
        save_config(self.cfg)
        self.root.destroy()
