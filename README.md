声明：整个项目都是deepseek写的，上传都是ai搞的，有bug也不定修（
# MiMoASR —— 小米 MiMo 语音识别桌面工具

基于小米开放平台 **MiMo-V2.5-ASR**（OpenAI API 兼容）的轻量语音识别客户端，支持 VRChat chatbox 发送。

- 🎙 麦克风录音直接识别（16kHz 单声道，上传小、识别快）
- 🔀 麦克风选择：下拉切换，选择即保存，下次启动自动恢复
- 📊 实时音量条 + 触发阈值：条上单击/拖动即可设置
- 🎧 自动识别：音量过阈值开始录音，静音自动切段（VAD）
- 📁 支持识别音频文件：wav / mp3 / m4a / aac / flac / ogg / opus / webm / wma
- 🕹 识别结果一键发送到 VRChat chatbox（OSC，可自动/手动）
- 🚫 无效信息过滤：咳嗽、清痰、环境噪音等非语音不显示、不发送
- ⚡ 性能占用极低：Tkinter 界面，空闲内存约 **70MB**
- 🖥 单文件可执行程序，免安装（双击即用）

## 获取 API Key

1. 注册小米开放平台：https://platform.xiaomimimo.com
2. 在控制台创建 API Key（或充值/领取 Token Plan）
3. 填入程序设置框，或设置环境变量 `MIMO_API_KEY`

> API Key 仅保存在本机 `%APPDATA%\MiMoASR\config.json`，不会上传到别处。

## 使用说明

### 基本识别
1. 打开程序，在顶部填入 API Key
2. 选择麦克风（可选，下次启动自动恢复上次选择）
3. 点击「🎙 录音」开始说话，再次点击「⏹ 停止并识别」
4. 或点击「📁 文件」选择已有音频

### 自动识别（推荐）
1. 在音量条上单击/拖动设置触发阈值（红线下方的音量会触发识别）
2. 在「静音切段(秒)」输入框设置说话停顿多久后切段（回车生效，范围 0.2~5s）
3. 点击「🎧 自动识别」——说话即识别，停顿超过设定时长自动切段
4. 再次点击「⏹ 停止自动识别」结束

> 切段时低于阈值的尾音（句尾弱音、音量下降部分）会完整保留在语音段内，不会截断。

### 发送到 VRChat
1. VRChat 中：设置 → OSC → 启用 OSC
2. 勾选「识别结果发送到 VRChat chatbox」（OSC 端口默认 9000）
3. 自动识别/手动识别的结果会自动发到 VRChat chatbox；也可点「▶ 发VRC」手动发送上一条结果

### 无效信息过滤
开启自动识别后，以下内容自动拦截（不显示、不发送）：
- 咳嗽、清痰等短促非语音（VAD 最短语音时长过滤）
- 纯语气词（嗯、啊、哦、呃、咳咳、emmm 等）
- 纯标点、空内容

> 请求严格使用官方格式（仅 user + input_audio），不加 system 提示——MiMo-V2.5-ASR 会把 system 文本当内容转写，导致输出变成 prompt 本身。

## 运行方式

### 已打包版本（推荐）
直接运行 `dist\MiMoASR.exe`。

### 源码运行
```bash
pip install -r requirements.txt
python main.py
```

### 重新打包
```bash
build.bat
# 产物：dist\MiMoASR.exe
```

## 测试

```bash
cd D:\CODE\MiMoASR
PYTHONPATH=. python tests\test_client.py    # mock API 请求构造
PYTHONPATH=. python tests\test_features.py  # VAD / OSC / 过滤 / UI 冒烟
```

## 项目结构

```
MiMoASR/
├── main.py              # 入口（含高分屏 DPI 适配）
├── mimo_asr/
│   ├── config.py        # 配置读写（%APPDATA%\MiMoASR\config.json）
│   ├── asr_client.py    # MiMo API 调用（含非语音过滤 system prompt）
│   ├── recorder.py      # sounddevice 录音（设备选择 + 实时音量）
│   ├── vad.py           # VAD 自动识别（阈值触发/静音切段/短音丢弃）
│   ├── vrc.py           # VRChat OSC 发送（手写 OSC，零依赖）
│   └── ui.py            # Tkinter 界面
├── tests/               # 单元测试（无需真实 Key / 麦克风）
├── build.bat            # 一键打包脚本
└── icon.ico             # 程序图标
```

## 性能说明

| 指标 | 数值 |
|---|---|
| 空闲内存占用 | ~71MB（onefile 解压器 + Python + numpy） |
| 录音采样率 | 16kHz 单声道（1 秒 ≈ 30KB，上传最小化） |
| 界面 | Tkinter 原生控件，识别串行队列，后台线程不阻塞 UI |
| VRC 发送 | 手写 OSC UDP（零第三方依赖，UDP 无连接不阻塞） |

## 配置项（config.json）

| 键 | 默认 | 说明 |
|---|---|---|
| `api_key` | "" | 小米开放平台 API Key |
| `language` | "auto" | 识别语言（auto/zh/en/ja/ko...） |
| `input_device` | "" | 麦克风标识 `hostapi:设备名`，空 = 默认 |
| `vad_threshold` | 0.02 | 自动识别触发阈值（0~1），音量条上拖动设置 |
| `silence_timeout` | 0.8 | 静音多少秒切段，界面可调（0.2~5s） |
| `min_speech_sec` | 0.5 | 最短语音段，短于此丢弃（过滤咳嗽等） |
| `vrc_enabled` | false | 识别结果自动发 VRChat |
| `vrc_host` / `vrc_port` | 127.0.0.1 / 9000 | VRChat OSC 地址 |
| `filter_noise` | true | 无效信息过滤总开关 |

## API 参考

官方文档：https://mimo.mi.com/docs/zh-CN/api/audio/Speech-Recognition

- 接口：`POST https://api.xiaomimimo.com/v1/chat/completions`
- 模型：`mimo-v2.5-asr`
- 请求头：`api-key: $MIMO_API_KEY`（或 `Authorization: Bearer`）
- 音频格式：`data:{MIME_TYPE};base64,...`
- 识别文本位于响应 `choices[0].message.content`
