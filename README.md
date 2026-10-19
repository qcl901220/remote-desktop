# 🖥️ RemoteDesk

**用 Python + 浏览器从零实现远程桌面，理解屏幕共享与远程控制的核心原理。**

> 被控端 1 个 Python 文件（~1000行），控制端 1 个 HTML 文件（零依赖），完整实现向日葵/ToDesk 的核心功能。
>
> 这不是一个要替代商业远程桌面的产品——这是一个帮你**彻底搞懂远程桌面是怎么工作的**学习项目。

<!-- 
TODO: 在这里放一张效果截图或 GIF
![demo](docs/images/demo.gif)
-->

## 🤔 为什么做这个项目？

你用过向日葵、ToDesk、TeamViewer，但有没有想过：

- **屏幕画面是怎么从一台电脑传到另一台的？**
- **鼠标键盘事件是怎么"隔空"操控远程电脑的？**
- **密码验证为什么不用明文传输也能认证？**
- **向日葵免费版为什么会卡？它在哪里动了手脚？**

这个项目用最简单的技术栈回答这些问题。读完代码和文档，你会完全理解远程桌面软件的内部原理。

## 🏗️ 架构总览

远程桌面本质上只做两件事：**把画面送过去，把操作传回来。**

```
被控端 (agent.py)                              控制端 (浏览器)
┌────────────────────┐                        ┌────────────────────┐
│                    │   JPEG 画面帧 (二进制)   │                    │
│  ① 屏幕捕获 (mss)   │ ──────────────────────→ │  ⑤ Canvas 渲染     │
│  ② JPEG 压缩       │                        │                    │
│  ③ 差帧检测(跳过不变) │                        │  ⑥ 鼠标/键盘事件捕获  │
│                    │   JSON 控制消息          │                    │
│  ④ 输入模拟         │ ←────────────────────── │  ⑦ 坐标映射         │
│    (SendInput API) │      WebSocket          │    canvas → 远程屏幕 │
│                    │                        │                    │
│  🔐 HMAC-SHA256    │   Challenge-Response    │  🔐 SHA-256 纯 JS   │
│     认证           │ ←────────────────────→  │    (兼容非 HTTPS)   │
└────────────────────┘                        └────────────────────┘
```

整个系统分为 **5 个核心子系统**：

| # | 子系统 | 解决的问题 | 技术方案 | 详细文档 |
|---|--------|-----------|---------|---------|
| ① | 屏幕捕获 | 怎么拿到屏幕像素？ | `mss` 库 (跨平台) | [📖 屏幕捕获原理](docs/01-screen-capture.md) |
| ② | 编码压缩 | 原始像素太大怎么办？ | JPEG 压缩 + 差帧跳过 | [📖 编码与带宽优化](docs/02-encoding.md) |
| ③ | 网络传输 | 画面和指令怎么传？ | WebSocket 全双工 | [📖 传输协议设计](docs/03-transport.md) |
| ④ | 输入模拟 | 怎么"隔空"操控？ | Windows SendInput API | [📖 输入模拟原理](docs/04-input-simulation.md) |
| ⑤ | 认证安全 | 怎么防止未授权访问？ | HMAC-SHA256 挑战应答 | [📖 认证与安全](docs/05-auth-security.md) |

> 📖 **架构总览文档**：[docs/architecture.md](docs/architecture.md)
>
> 📖 **和商业产品的差距**：[docs/06-production-gap.md](docs/06-production-gap.md) — 诚实讲这个项目和向日葵/RustDesk 差在哪，以及改进方向

## ✨ 已实现的功能

| 功能 | 说明 |
|------|------|
| 🔐 密码认证 | HMAC-SHA256 Challenge-Response，密码不明文传输 |
| 🖥️ 多显示器 | 在线切换被控端的多个显示器 |
| 🎛️ 画质调节 | 实时调整帧率 (1-60fps) 和 JPEG 质量 (10-95) |
| 📋 剪贴板同步 | 双向文本剪贴板同步 |
| 📁 文件传输 | 左栏本地拖拽上传，右栏远程文件浏览下载 |
| 💻 设备管理 | 保存常用设备（名称/IP/备注），一键连接 |
| 🔑 密码管理 | config.json 持久化 + 运行时可修改连接密码 |
| 🇨🇳 中文输入 | IME 输入法支持，compositionend + KEYEVENTF_UNICODE |
| ⛶ 浮动工具栏 | 全屏模式鼠标移到顶部自动呼出工具栏 |
| 🔄 自动重连 | 网络波动 5 秒自动重连 |
| 📊 实时监控 | FPS、延迟、带宽实时显示 |
| ⛶ 全屏模式 | F11 一键全屏 |
| 🌐 零安装控制端 | Agent 内嵌 Web 服务，浏览器直接访问 |

## 🚀 30 秒快速体验

### 环境要求

- **被控端**：Windows（需要 Python 3.8+）
- **控制端**：任意设备的现代浏览器

### 启动

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动被控端（在 Windows 电脑上运行）
cd server
python agent.py

# 终端会显示：
# ╔══════════════════════════════════════════════════════╗
# ║            🖥️  RemoteDesk Agent  v4.0               ║
# ║  🌐 控制端:  http://0.0.0.0:8000                    ║
# ║  🔑 密码:    aBcDeFgH                               ║
# ╚══════════════════════════════════════════════════════╝
```

```bash
# 3. 浏览器打开控制端
#    在同一局域网的任意设备上访问：
http://<被控端IP>:8000
```

输入密码，点连接，完事。

### 可选参数

```bash
python agent.py -p 123456           # 指定密码（保存到 config.json）
python agent.py --fps 60            # 高帧率
python agent.py --quality 80        # 高画质
python agent.py --web-port 9000     # 自定义 Web 端口
python agent.py --device-name 办公室  # 设备名
```

## 📐 核心原理速览

以下是每个子系统的关键思路。完整讲解见 `docs/` 目录。

### ① 屏幕捕获：怎么拿到屏幕像素？

```python
import mss
sct = mss.MSS()
screenshot = sct.grab(sct.monitors[1])  # 抓取主显示器
raw_bgra = screenshot.bgra              # 原始 BGRA 像素，1920×1080 ≈ 8MB
```

`mss` 在 Windows 上调用 DXGI Desktop Duplication API（GPU 加速），比传统 GDI BitBlt 快得多。

> 📖 [为什么不用 PIL.ImageGrab？各平台捕获 API 的差异？→ 详见文档](docs/01-screen-capture.md)

### ② 编码压缩：8MB 原始像素怎么传？

```python
# 1. JPEG 压缩：8MB → 50-150KB（压缩比 50-100x）
img.save(buf, format="JPEG", quality=50)

# 2. 差帧检测：屏幕没变就不传（省带宽 + 省 CPU）
sample = raw_bgra[::step]              # 采样 ~10000 像素
frame_hash = hashlib.md5(sample).digest()
if frame_hash == last_hash:
    return None  # 跳过！
```

办公场景下（打字、看文档），60-80% 的帧都被跳过了。

> 📖 [JPEG vs H.264？差帧检测的采样策略？→ 详见文档](docs/02-encoding.md)

### ③ 传输协议：为什么用 WebSocket？

```
HTTP:    客户端请求 → 服务端响应（单向，每次都要新请求）
WebSocket: 建立连接后，双方随时发消息（全双工、持久连接）
```

远程桌面需要：服务端不断推画面，客户端不断推键鼠事件——天然适合 WebSocket。

画面帧用**二进制消息**发送（省去 base64 膨胀），控制指令用 **JSON 文本消息**：

```
Server → Client:  [binary] JPEG 帧数据
Client → Server:  {"type":"mouse_move","x":500,"y":300}
Client → Server:  {"type":"key_down","key":"a"}
```

> 📖 [完整协议设计、消息类型列表 → 详见文档](docs/03-transport.md)

### ④ 输入模拟：怎么"隔空"点鼠标？

```python
# Windows SendInput API — 绝对坐标定位
abs_x = int(x * 65535 / screen_width)   # 归一化到 0-65535
abs_y = int(y * 65535 / screen_height)
inp.union.mi.dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
SendInput(1, byref(inp), sizeof(inp))
```

控制端浏览器捕获鼠标事件 → 坐标映射（canvas 像素 → 远程屏幕像素）→ WebSocket 发给 Agent → Agent 调用 SendInput 移动鼠标。

> 📖 [为什么用 ctypes 而不是 pynput？键盘事件的 Unicode 处理？→ 详见文档](docs/04-input-simulation.md)

### ⑤ 认证：密码怎么不传明文也能验证？

```
Agent                                Browser
  │ 1. 生成随机 challenge              │
  │ ────── challenge ──────────────→  │
  │                                   │ 2. response = HMAC(SHA256(密码), challenge)
  │ ←───── response ───────────────  │
  │ 3. 本地计算同样的 HMAC              │
  │    比对是否一致                     │
  │ ────── 认证成功/失败 ─────────→    │
```

每次连接的 challenge 都不同 → 即使被截获也无法重放。

> 📖 [为什么不用简单的密码比对？HMAC 的数学原理？→ 详见文档](docs/05-auth-security.md)

## 📊 性能数据

实测环境：局域网千兆网络，1080p 屏幕

| 指标 | 数值 |
|------|------|
| 画面延迟 | 30-80ms |
| 帧率 | 25-30 fps（办公场景） |
| 带宽（活跃操作） | 2-5 Mbps |
| 带宽（静止画面） | ~0 Mbps（差帧跳过） |
| 单帧大小 (quality=50) | 30-150 KB |
| 差帧跳过率（办公） | 60-80% |

## 🔭 和商业产品的差距

这个项目是学习用的，和真正的商业远程桌面有明确差距：

| 维度 | RemoteDesk (本项目) | 商业产品 (向日葵/RustDesk) |
|------|-------|---------|
| 编码 | JPEG 逐帧 | H.264/H.265 视频编码 |
| 传输 | WebSocket (TCP) | WebRTC (UDP, P2P) |
| NAT 穿透 | ❌ 仅局域网/VPN | ✅ STUN/TURN/ICE |
| 音频 | ❌ | ✅ |
| 安全 | HMAC 认证 | TLS + 端到端加密 |
| 多平台 | Windows 被控 | 全平台 |

> 📖 [详细差距分析和改进方向 → docs/06-production-gap.md](docs/06-production-gap.md)

## 📁 项目结构

```
remote-desktop/
├── server/
│   └── agent.py              # 被控端 —— 全部后端逻辑（~1000行）
├── web/
│   └── index.html            # 控制端 —— 全部前端（HTML+CSS+JS 单文件）
├── docs/                     # 原理文档
│   ├── architecture.md       #   架构总览
│   ├── 01-screen-capture.md  #   屏幕捕获
│   ├── 02-encoding.md        #   编码与带宽
│   ├── 03-transport.md       #   传输协议
│   ├── 04-input-simulation.md#   输入模拟
│   ├── 05-auth-security.md   #   认证安全
│   └── 06-production-gap.md  #   和商业产品的差距
├── config.example.json       # 配置模板
├── requirements.txt          # Python 依赖
├── LICENSE                   # MIT
└── README.md                 # ← 你正在看的文件
```

## 🛠️ 技术栈

| 组件 | 技术 | 为什么选它 |
|------|------|-----------|
| 屏幕捕获 | `mss` | 跨平台、GPU 加速、纯 Python |
| 图像压缩 | `Pillow` | Python 图像处理标准库 |
| 网络通信 | `websockets` | 异步 WebSocket，API 简洁 |
| 输入模拟 | `ctypes` + Win32 API | 零依赖、可靠、支持 Unicode |
| 控制端渲染 | HTML5 Canvas | 浏览器原生，零依赖 |
| 认证 | HMAC-SHA256 | 密码学标准，纯 JS 实现 + Web Crypto API 双模式 |

## 📜 License

MIT — 随便用，注明出处就行。

## 🙏 致谢

- [mss](https://github.com/BoboTiG/python-mss) — 跨平台屏幕截图
- [Pillow](https://github.com/python-pillow/Pillow) — Python 图像处理
- [websockets](https://github.com/python-websockets/websockets) — Python WebSocket
- [RustDesk](https://github.com/rustdesk/rustdesk) — 优秀的开源远程桌面（Rust 实现，本项目的学习参考）
