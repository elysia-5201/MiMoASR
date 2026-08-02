"""MiMo 语音识别 - 程序入口"""
from __future__ import annotations

import ctypes
import sys
import tkinter as tk

from mimo_asr.ui import App


def main() -> int:
    # Windows 高分屏 DPI 感知（清晰不模糊）
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
