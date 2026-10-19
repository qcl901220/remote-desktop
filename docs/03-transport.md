# 03 — 传输协议设计

## 问题是什么

屏幕帧需要从被控端发到控制端，鼠标键盘事件需要从控制端发回被控端。

核心需求：**双向、实时、持久连接。**

## 为什么选 WebSocket？

### 先排除不合适的

| 方案 | 问题 |
|------|------|
| HTTP 轮询 | 30fps = 每秒 30 个 HTTP 请求，开销巨大 |
| HTTP 长轮询 | 单向，画面推送和事件回传不能同时 |
| Server-Sent Events | 只能服务端→客户端，鼠标事件传不回去 |
| WebRTC | 真正的最优解，但实现复杂度高 10 倍 |
| 原始 TCP | 浏览器不支持 |

### WebSocket 的特点

```
HTTP:       每次通信都要 "请求→响应"（半双工，有头部开销）
WebSocket:  一次握手，之后随时双向发消息（全双工，头部开销 2-14 字节）
```

```
1. 浏览器发起 HTTP 请求，带 Upgrade: websocket 头
2. 服务端同意升级，返回 101 Switching Protocols
3. 之后就是持久的双向通道，直到一方关闭

┌──────────┐         ┌──────────┐
│  Agent   │ ←═══════→ │ Browser  │
│          │  全双工     │          │
│ 随时发帧  │  持久连接   │ 随时发事件 │
└──────────┘         └──────────┘
```

## 消息设计：二进制 vs JSON

WebSocket 支持两种消息类型：**文本 (string)** 和 **二进制 (binary)**。

我们的设计：

| 数据 | 消息类型 | 原因 |
|------|---------|------|
| JPEG 画面帧 | **二进制** | 避免 base64 编码膨胀 33% |
| 控制指令 | **JSON 文本** | 可读、易调试、结构灵活 |

```python
# 服务端发送画面（二进制，高效）
await ws.send(jpeg_bytes)          # 直接发 bytes

# 服务端发送控制消息（JSON 文本，可读）
await ws.send(json.dumps({"type": "connected", "screen": {...}}))
```

```javascript
// 浏览器接收——根据数据类型分流
ws.onmessage = (e) => {
    if (typeof e.data === 'string') {
        handleJson(JSON.parse(e.data));   // JSON 控制消息
    } else {
        renderFrame(e.data);              // 二进制 JPEG 帧
    }
};
```

## 完整消息类型列表

### Agent → Browser

| 消息类型 | 格式 | 说明 |
|---------|------|------|
| (画面帧) | binary | JPEG 字节流，无 JSON 包装 |
| `auth_challenge` | JSON | `{type, challenge}` — 认证挑战 |
| `connected` | JSON | `{type, screen, monitors, fps, quality, system}` |
| `auth_failed` | JSON | 密码错误 |
| `pong` | JSON | `{type, ts}` — 延迟测量响应 |
| `quality_changed` | JSON | 画质变更确认 |
| `fps_changed` | JSON | 帧率变更确认 |
| `monitor_changed` | JSON | 显示器切换确认 + 新分辨率 |
| `clipboard_data` | JSON | 剪贴板内容 |
| `clients_list` | JSON | 在线客户端列表 |
| `dir_content` | JSON | 目录内容（文件管理器） |
| `file_data` | JSON | 文件内容 base64（下载） |
| `password_changed` | JSON | 密码修改结果 |
| `agent_info` | JSON | 被控端信息（密码、设备名等） |

### Browser → Agent

| 消息类型 | 格式 | 说明 |
|---------|------|------|
| `auth_response` | JSON | `{type, response}` — HMAC 应答 |
| `mouse_move` | JSON | `{type, x, y}` |
| `mouse_down` / `mouse_up` | JSON | `{type, button}` |
| `mouse_scroll` | JSON | `{type, delta}` |
| `key_down` / `key_up` | JSON | `{type, key}` |
| `set_quality` | JSON | `{type, quality}` |
| `set_fps` | JSON | `{type, fps}` |
| `set_monitor` | JSON | `{type, index}` |
| `get_clipboard` / `set_clipboard` | JSON | 剪贴板操作 |
| `ping` | JSON | `{type, ts}` — 延迟测量 |
| `ime_text` | JSON | `{type, text}` — 中文输入法完成文本 |
| `browse_dir` | JSON | `{type, path, side}` |
| `change_password` | JSON | `{type, password}` |
| `set_device_name` | JSON | `{type, name}` |
| `get_agent_info` | JSON | `{type}` — 请求被控端配置信息 |

## 带宽分析

```
一帧 JPEG:    50-150 KB
JSON 控制消息: 50-200 bytes
WebSocket 头:  2-14 bytes/消息

画面带宽 (30fps, quality=50):
  100KB × 30 = 3,000KB/s ≈ 3MB/s ≈ 24Mbps (最大)
  考虑差帧跳过 60-80%:  ≈ 1-5Mbps (实际)

控制消息带宽:
  mousemove: ~60 bytes × 60fps = 3.6KB/s (可忽略)
  键盘:  ~50 bytes × 偶尔
```

画面帧占了 99.9% 的带宽。控制消息基本可以忽略。

## mousemove 节流

鼠标移动事件在浏览器中触发频率非常高（可达 200+ 次/秒）。如果每次都发 WebSocket 消息，会导致不必要的消息风暴。

我们用 `requestAnimationFrame` 节流：

```javascript
let pendingMove = null;

canvas.addEventListener('mousemove', (e) => {
    pendingMove = { x: ..., y: ... };  // 只记录最新位置
});

(function flush() {
    if (pendingMove) {
        ws.send(JSON.stringify({type: 'mouse_move', ...pendingMove}));
        pendingMove = null;
    }
    requestAnimationFrame(flush);  // 每帧最多发一次 = 60fps 上限
})();
```

这样鼠标移动消息最多 60 次/秒，而不是 200+ 次/秒。

## 代码位置

- 服务端 WebSocket：`server/agent.py` — `RemoteDesktopServer.handler()`
- 客户端 WebSocket：`web/index.html` — `connect()` 函数
- 消息分发：`server/agent.py` — `handle_input()` + `handle_control()`

## 商业产品怎么做的？

商业产品最终会用 **WebRTC**：
- UDP 传输（比 TCP 低延迟，丢帧比卡顿好）
- P2P 直连（不经过服务器）
- 内置 NAT 穿透（ICE/STUN/TURN）
- 浏览器原生支持 `<video>` + MediaStream

但 WebRTC 的信令交换、ICE 候选协商、编解码器配置的复杂度远高于 WebSocket——这也是为什么我们学习阶段先用 WebSocket。
