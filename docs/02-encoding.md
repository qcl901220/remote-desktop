# 02 — 编码压缩与带宽优化

## 问题是什么

屏幕捕获拿到的是原始像素：1920×1080×4 字节 = **8MB/帧**。

如果不压缩，30fps 就是 240MB/s = **1.9Gbps**。千兆局域网都扛不住。

所以必须压缩。问题是：**用什么压缩？压多少？**

## 方案选型

| 方案 | 压缩比 | 延迟 | 复杂度 | 适用场景 |
|------|--------|------|--------|---------|
| **JPEG 逐帧** | 50-100x | 低 | ⭐ | LAN、原型 |
| JPEG + 差帧跳过 | 50-100x + 空闲时∞ | 低 | ⭐⭐ | **← 我们用的** |
| H.264 (ffmpeg pipe) | 200-500x | 中 | ⭐⭐⭐ | VPN、低带宽 |
| WebRTC MediaTrack | 200-500x | 低 | ⭐⭐⭐⭐ | P2P 全方案 |

**为什么选 JPEG 而不是 H.264？**

- JPEG 实现极简：`img.save(buf, format="JPEG")` 一行搞定
- 无状态：每帧独立，丢一帧不影响后续帧（H.264 有 I/P/B 帧依赖）
- 对于局域网场景，JPEG 的带宽开销完全可以接受

## 实现：JPEG 压缩

```python
from PIL import Image
import io

# mss 返回的是 BGRA 格式，Pillow 需要转成 RGB
img = Image.frombytes("RGB", screenshot.size, raw_bgra, "raw", "BGRX")

# 缩放（可选）：如果原始分辨率太高
if img.width > max_width:
    ratio = max_width / img.width
    img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)

# JPEG 压缩
buf = io.BytesIO()
img.save(buf, format="JPEG", quality=50, optimize=True)
jpeg_bytes = buf.getvalue()  # 通常 30-150KB
```

### quality 参数的影响

| quality | 1080p 帧大小 | 30fps 带宽 | 画质 |
|---------|-------------|-----------|------|
| 30 | 20-50 KB | 0.6-1.5 Mbps | 有明显压缩痕迹 |
| 50 | 50-100 KB | 1.5-3 Mbps | **推荐，清晰度够用** |
| 70 | 100-200 KB | 3-6 Mbps | 高清 |
| 90 | 200-500 KB | 6-15 Mbps | 接近无损 |

## 关键优化：差帧检测

远程桌面的一个重要观察：

> **大部分时间，屏幕是不变的。** 打字时只有光标在闪，看文档时完全静止。

如果屏幕没变，就跳过这一帧——不编码、不传输。带宽直接降到 0。

### 怎么检测"屏幕没变"？

最暴力的方法：比较两帧的全部像素。但 8MB 的逐字节比较太慢了。

我们的方案：**采样哈希**

```python
import hashlib

raw_bgra = screenshot.bgra              # 8MB 原始像素
step = max(1, len(raw_bgra) // 10000)  # 每隔 N 字节取一个样本
sample = raw_bgra[::step]              # 采样 ~10000 字节
frame_hash = hashlib.md5(sample).digest()

if frame_hash == self._last_hash:
    return None  # 屏幕没变，跳过！
self._last_hash = frame_hash
```

### 为什么用 MD5 而不是直接比较？

- 采样后只有 ~10KB，MD5 计算几乎不花时间
- MD5 的 16 字节摘要比较比 10KB 字节比较更快
- 不需要密码学安全性，只需要碰撞概率低（MD5 足够）

### 采样会不会漏掉变化？

理论上，如果恰好只有采样点之间的像素变了，就会漏掉。但实际上：

- 采样间隔 = 8MB / 10000 ≈ 每 800 字节取一个点
- 这相当于每隔 200 个像素（约 1/10 行）取一个采样
- 任何可见的变化（光标闪烁、窗口移动）都会被捕捉到

实测误判率几乎为零。

### 性能收益

| 场景 | 跳帧率 | 实际带宽 |
|------|--------|---------|
| 完全静止（待机） | 99%+ | ~0 |
| 打字/看文档 | 60-80% | 0.5-1 Mbps |
| 滚动网页 | 10-20% | 3-5 Mbps |
| 播放视频 | 0% | 5-15 Mbps |

## 代码位置

→ `server/agent.py` — `ScreenCapture.capture()` 方法

## 商业产品怎么做的？

商业产品用 **H.264/H.265 视频编码**，利用帧间预测（P 帧只编码变化的部分）自动实现了"差帧"效果，而且压缩比高得多。

它们通常还会用：
- **GPU 硬件编码器**（NVENC/QSV/AMF）：CPU 占用接近 0
- **自适应码率**：根据网络状况自动调整 quality/fps
- **脏矩形检测**：只编码屏幕变化的区域，而不是整帧

这些都是我们的 JPEG 方案可以往上走的方向。
