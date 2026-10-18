# 架构总览

## 远程桌面的本质

不管是向日葵、TeamViewer、RustDesk，还是 Windows 自带的 RDP——所有远程桌面软件都在解决同一个问题：

> **让你坐在 A 电脑前，操控 B 电脑，就像坐在 B 电脑前一样。**

拆解这个问题，只有两个核心数据流：

```
画面流（单向）：  B 的屏幕画面 ───→ A 的显示器
操控流（单向）：  A 的鼠标键盘 ───→ B 的操作系统
```

所有的复杂性都来自于如何**高效、低延迟、安全**地实现这两个数据流。

## 5 个核心子系统

我们把这个问题分解成 5 个子系统，每个子系统解决一个具体问题：

```
被控端 (agent.py)                    控制端 (浏览器 index.html)

 ┌──────────────────────────────┐    ┌──────────────────────────────┐
 │  [1] 屏幕捕获                 │    │  [5] 认证                    │
 │  mss.grab() → BGRA 原始像素   │    │  Web Crypto API              │
 │                               │    │  SHA-256 + HMAC 计算          │
 │  [2] 编码压缩                 │    │                              │
 │  Pillow JPEG 压缩             │    │  [6] 画面渲染                 │
 │  差帧检测（跳过不变的帧）       │    │  <canvas> + drawImage()      │
 │                               │    │                              │
 │          ┌─── WebSocket 全双工 ───┐ │                              │
 │          │  画面: 二进制 JPEG   →  │ │                              │
 │          │  控制: JSON 文本    ←→  │ │  [7] 输入捕获 & 坐标映射      │
 │          └────────────────────────┘ │  mousedown/keydown 事件       │
 │                               │    │  canvas 坐标 → 屏幕坐标       │
 │  [3] 输入模拟                 │    │                              │
 │  Win32 SendInput API          │    │                              │
 │  ctypes 直接调用               │    │                              │
 │                               │    │                              │
 │  [4] 认证                     │    │                              │
 │  HMAC-SHA256 Challenge-Response│   │                              │
 └──────────────────────────────┘    └──────────────────────────────┘
```

## 数据流时序

一次完整的远程操控过程：

```
时间 →

Agent                WebSocket              Browser
  │                                            │
  │  ───── auth_challenge {challenge} ────→    │  1. 连接建立，发起认证
  │  ←──── auth_response {hmac} ──────────    │  2. 浏览器计算 HMAC 应答
  │  ───── connected {screen, monitors} ──→   │  3. 认证成功，返回屏幕信息
  │                                            │
  │  ═══════ [binary] JPEG 帧 ═════════════→  │  4. 开始推送画面
  │  ═══════ [binary] JPEG 帧 ═════════════→  │     30fps = 每 33ms 一帧
  │  (屏幕没变，跳过)                           │     差帧检测，省带宽
  │  ═══════ [binary] JPEG 帧 ═════════════→  │
  │                                            │
  │  ←───── {mouse_move, x, y} ───────────    │  5. 用户移动鼠标
  │  (SendInput 移动远程鼠标)                    │
  │  ←───── {mouse_down, button} ─────────    │  6. 用户点击
  │  ←───── {key_down, key: "a"} ─────────    │  7. 用户按键
  │                                            │
  │  ←───── {set_quality, quality: 80} ───    │  8. 调整画质
  │  ───── {quality_changed, quality: 80} →   │
  │                                            │
  │  ←───── {ping, ts} ──────────────────     │  9. 延迟测量
  │  ───── {pong, ts} ───────────────────→    │
```

## 每个子系统的详细文档

| 文档 | 内容 |
|------|------|
| [01-screen-capture.md](01-screen-capture.md) | 怎么拿到屏幕像素？各平台 API 对比 |
| [02-encoding.md](02-encoding.md) | JPEG 压缩、差帧检测、带宽优化 |
| [03-transport.md](03-transport.md) | WebSocket 协议设计、消息类型、为什么不用 HTTP |
| [04-input-simulation.md](04-input-simulation.md) | SendInput API、坐标映射、键盘 Unicode 处理 |
| [05-auth-security.md](05-auth-security.md) | HMAC-SHA256 挑战应答、为什么不能明文密码 |
| [06-production-gap.md](06-production-gap.md) | 和商业产品的差距、改进方向 |
