# 01 — 屏幕捕获原理

## 问题是什么

远程桌面的第一步：**怎么把屏幕当前显示的内容变成一个图像？**

操作系统的屏幕本质上是 GPU 帧缓冲区中的一块像素数据。屏幕捕获就是把这块数据复制到应用程序可以访问的内存中。

## 各平台的捕获 API

| 平台 | API | 原理 | 性能 |
|------|-----|------|------|
| **Windows 8+** | DXGI Desktop Duplication | GPU 直接复制帧缓冲区 | ⭐⭐⭐ 最快 |
| Windows (旧) | GDI BitBlt | CPU 从 DC 中拷贝 | ⭐ 慢 |
| Linux X11 | XShm | 共享内存读取 X Server 帧 | ⭐⭐ |
| Linux Wayland | PipeWire + Portal | 需要用户授权的安全 API | ⭐⭐ |
| macOS | CGDisplayStream | Core Graphics 流式捕获 | ⭐⭐ |

## 我们的方案：mss

```python
import mss

sct = mss.MSS()                       # 初始化捕获器
monitor = sct.monitors[1]             # monitors[0] = 全部显示器, [1] = 主显示器
screenshot = sct.grab(monitor)        # 抓取！

# 返回的数据
screenshot.size   # (1920, 1080) — 宽高
screenshot.bgra   # bytes — 原始 BGRA 像素数据
                  # 1920 × 1080 × 4 bytes = 8,294,400 bytes ≈ 8MB
```

### 为什么选 mss？

| 库 | 速度 (1080p) | 跨平台 | 依赖 |
|----|-------------|--------|------|
| `mss` | ~15ms | ✅ Win/Linux/Mac | 纯 Python |
| `PIL.ImageGrab` | ~30ms | ⚠️ Win/Mac 仅 | Pillow |
| `dxcam` | ~5ms | ❌ 仅 Windows | 需要 DX |
| `pyautogui` | ~100ms | ✅ | 重量级 |

`mss` 在 Windows 上底层使用 DXGI Desktop Duplication（与 `dxcam` 相同的系统 API），但封装成了跨平台接口。对于 30fps 的远程桌面，15ms 的捕获延迟完全够用（每帧预算 33ms）。

## 多显示器处理

```python
sct.monitors
# [
#   {"left":0, "top":0, "width":3840, "height":1080},  # [0] 虚拟桌面（全部合并）
#   {"left":0, "top":0, "width":1920, "height":1080},  # [1] 主显示器
#   {"left":1920, "top":0, "width":1920, "height":1080} # [2] 副显示器
# ]

# 切换显示器 = 改变 grab() 的 monitor 参数
def set_monitor(self, index: int):
    self.monitor_index = index
```

`monitors[0]` 是所有显示器的合并视图。如果直接抓 `[0]`，你会得到一个超宽的图像（多屏横向拼接），带宽浪费。所以我们让用户选择具体的显示器。

## 性能数据

1080p 屏幕，mss 实测：

| 操作 | 耗时 | 数据量 |
|------|------|--------|
| `sct.grab()` | ~15ms | 8 MB (BGRA) |
| JPEG 编码 (quality=50) | ~8ms | 50-150 KB |
| 总计每帧 | ~23ms | 50-150 KB |

23ms/帧意味着理论上限 ~43fps。实际加上 WebSocket 传输，30fps 是比较稳的。

## 代码位置

→ `server/agent.py` — `ScreenCapture` 类

## 商业产品怎么做的？

向日葵/RustDesk 在 Windows 上同样使用 DXGI Desktop Duplication——这是 Windows 8+ 官方推荐的高性能屏幕捕获 API，没有比它更快的方式了。

差别在于后续环节：它们拿到原始帧后，用 **GPU 硬件编码器**（NVENC/QSV/AMF）直接编码成 H.264/H.265 视频流，整个流程不经过 CPU。而我们是 CPU 做 JPEG 编码——这是性能差距的主要来源。
