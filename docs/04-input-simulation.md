# 04 — 输入模拟原理

## 问题是什么

控制端用户在浏览器里点鼠标、敲键盘——这些操作需要在被控端的 Windows 系统上**真实执行**。

就像有一个"虚拟的手"在操控被控端的鼠标和键盘。

## Windows 输入模拟 API

Windows 提供了 `SendInput` API，可以在程序中模拟鼠标和键盘事件。操作系统会把这些模拟事件和真实硬件输入一样处理。

### 为什么用 ctypes 而不是 pynput？

| 方案 | 优点 | 缺点 |
|------|------|------|
| **ctypes + SendInput** | 零依赖、绝对坐标精确、支持 Unicode | 代码稍多 |
| pynput | API 简洁 | 额外依赖、服务模式下可能失败 |
| pyautogui | 最简单 | 慢、没有按键状态追踪 |

`pynput` 在某些场景下会出问题（比如作为 Windows 服务运行时无法访问桌面会话），而 `ctypes` 直接调用 Windows API，只要在桌面会话中就一定能工作。

## 鼠标模拟

### 核心概念：绝对坐标

SendInput 的鼠标移动使用 **0-65535 的归一化坐标**，而不是像素坐标：

```python
# 像素坐标 → 归一化坐标
abs_x = int(x * 65535 / screen_width)
abs_y = int(y * 65535 / screen_height)

inp = INPUT()
inp.type = INPUT_MOUSE
inp.union.mi.dx = abs_x
inp.union.mi.dy = abs_y
inp.union.mi.dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
SendInput(1, byref(inp), sizeof(inp))
```

### 为什么是 65535？

这是 Windows API 的设计——用 16 位无符号整数表示整个屏幕的范围。好处是**与分辨率无关**：不管屏幕是 1080p 还是 4K，(32768, 32768) 永远是屏幕正中央。

### 坐标映射链

```
浏览器 canvas 上的点击位置
        ↓  (canvas 坐标 → 远程屏幕坐标)
    remote_x = (click_x - canvas.left) * screenW / canvas.width
    remote_y = (click_y - canvas.top)  * screenH / canvas.height
        ↓  (远程屏幕坐标 → SendInput 坐标)
    abs_x = remote_x * 65535 / screenW
    abs_y = remote_y * 65535 / screenH
        ↓
    SendInput() 执行移动
```

### 鼠标按钮和滚轮

```python
# 左键按下
inp.union.mi.dwFlags = MOUSEEVENTF_LEFTDOWN

# 右键松开
inp.union.mi.dwFlags = MOUSEEVENTF_RIGHTUP

# 滚轮（正数向上，负数向下）
inp.union.mi.mouseData = delta * 120  # 120 = 一个"刻度"
inp.union.mi.dwFlags = MOUSEEVENTF_WHEEL
```

## 键盘模拟

### 两种键：功能键 vs 字符键

```python
# 功能键：用虚拟键码 (VK code)
KEY_MAP = {
    "Backspace": 0x08, "Tab": 0x09, "Enter": 0x0D,
    "Shift": 0x10, "Control": 0x11, "Alt": 0x12,
    "ArrowLeft": 0x25, "ArrowUp": 0x26, ...
    "F1": 0x70, "F2": 0x71, ...
}

inp.union.ki.wVk = KEY_MAP["Enter"]  # 虚拟键码
inp.union.ki.dwFlags = 0             # 按下

# 字符键：用 Unicode 扫描码（支持中文！）
inp.union.ki.wScan = ord("好")       # Unicode 码点
inp.union.ki.dwFlags = KEYEVENTF_UNICODE  # Unicode 模式
```

### 为什么要区分？

- `"Enter"`、`"F5"`、`"Shift"` 这些是**功能键**，有对应的虚拟键码 (VK code)
- `"a"`、`"好"`、`"π"` 这些是**字符键**，用 Unicode 扫描码更可靠

`KEYEVENTF_UNICODE` 标志让 Windows 直接处理 Unicode 码点，完美支持中文、日文、Emoji 输入。

## 关键陷阱：修饰键卡住

远程桌面的经典 bug：

> 用户在远程桌面里按了 Ctrl+C（复制），但网络断了——Ctrl 的 keydown 事件传过去了，keyup 还没来得及传。
>
> 结果：被控端的 Ctrl 键永远"按着"，之后所有操作都变成了 Ctrl+操作。

### 解决方案：断连时释放所有按键

```python
class InputSimulator:
    MODIFIER_VKS = [0x10, 0x11, 0x12, 0x5B, ...]  # Shift, Ctrl, Alt, Win

    def __init__(self):
        self._pressed_keys = set()  # 追踪所有按下的键

    def key_event(self, key, action):
        # ... 发送按键 ...
        if action == "down":
            self._pressed_keys.add(vk)
        else:
            self._pressed_keys.discard(vk)

    def release_all_keys(self):
        """客户端断连时调用——释放所有卡住的键"""
        for vk in self._pressed_keys:
            send_keyup(vk)
        self._pressed_keys.clear()
        # 额外保险：无条件释放所有修饰键
        for vk in self.MODIFIER_VKS:
            send_keyup(vk)
```

在 WebSocket 断开事件中调用 `release_all_keys()`：

```python
async def unregister(self, ws):
    if not self.clients:  # 最后一个客户端断开
        self.input_sim.release_all_keys()
```

## 代码位置

- 输入模拟：`server/agent.py` — `InputSimulator` 类
- 坐标映射（浏览器侧）：`web/index.html` — `rXY(e)` 函数
- 按键追踪：`server/agent.py` — `_pressed_keys` 集合

## 商业产品怎么做的？

- **低级驱动注入**：一些产品使用内核级驱动（而非用户态 SendInput）来模拟输入，可以在更多场景下工作（如游戏全屏、安全桌面）
- **输入法兼容**：处理 IME 输入法的组合状态（候选窗口、拼音串）
- **游戏手柄透传**：转发 Xbox/PlayStation 控制器输入
- **多点触控**：处理触屏设备的多指手势
