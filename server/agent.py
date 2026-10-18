"""
RemoteDesk — 被控端 Agent v4
密码认证 / 多显示器 / 画质动态调节 / 剪贴板同步 / 文件传输 / 内嵌 Web 服务
设备管理 / 运行时密码修改

用法:
  python agent.py                          # 随机密码
  python agent.py -p mypass123             # 指定密码
  python agent.py --fps 60 --quality 70    # 高画质
  python agent.py --web-port 8080          # 自定义 Web 端口

密码也可以写在 config.json 中持久化保存（优先级: 命令行 > config.json > 随机）。
控制端直接浏览器访问 http://<IP>:<web-port> 即可使用
"""

import asyncio
import json
import hashlib
import hmac as _hmac
import secrets
import argparse
import io
import os
import time
import logging
import platform
import base64
from typing import Set, Optional
from pathlib import Path
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler
import threading
import socketserver

import mss
from PIL import Image
import websockets
from websockets.asyncio.server import serve

# ─── Windows API ─────────────────────────────────────

HAS_WIN32 = False
try:
    import ctypes
    import ctypes.wintypes

    INPUT_MOUSE = 0
    INPUT_KEYBOARD = 1
    MOUSEEVENTF_MOVE = 0x0001
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010
    MOUSEEVENTF_MIDDLEDOWN = 0x0020
    MOUSEEVENTF_MIDDLEUP = 0x0040
    MOUSEEVENTF_WHEEL = 0x0800
    MOUSEEVENTF_ABSOLUTE = 0x8000
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", ctypes.c_long), ("dy", ctypes.c_long),
            ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
            ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class INPUT_UNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong), ("union", INPUT_UNION)]

    HAS_WIN32 = True
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s │ %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("agent")


# ─── 屏幕捕获 ────────────────────────────────────────

class ScreenCapture:
    def __init__(self, quality: int = 50, max_width: int = 1920, monitor_index: int = 1):
        self.quality = quality
        self.max_width = max_width
        self.monitor_index = monitor_index
        self.sct = mss.MSS()
        self._last_hash: Optional[bytes] = None
        self._last_frame: Optional[bytes] = None
        self.stats_total = 0
        self.stats_skipped = 0

    @property
    def monitors(self) -> list:
        result = []
        for i, m in enumerate(self.sct.monitors):
            if i == 0:
                continue
            result.append({
                "index": i, "width": m["width"], "height": m["height"],
                "left": m["left"], "top": m["top"],
                "name": f"显示器 {i}" + (" (主)" if i == 1 else ""),
            })
        return result

    @property
    def current_monitor(self) -> dict:
        m = self.sct.monitors[self.monitor_index]
        return {"width": m["width"], "height": m["height"]}

    def set_monitor(self, index: int):
        if 1 <= index < len(self.sct.monitors):
            self.monitor_index = index
            self._last_hash = None
            self._last_frame = None
            logger.info(f"切换到显示器 {index}")

    def set_quality(self, quality: int):
        self.quality = max(10, min(95, quality))
        self._last_hash = None

    def capture(self) -> Optional[bytes]:
        monitor = self.sct.monitors[self.monitor_index]
        screenshot = self.sct.grab(monitor)
        raw = screenshot.bgra
        self.stats_total += 1

        step = max(1, len(raw) // 10000)
        frame_hash = hashlib.md5(raw[::step]).digest()
        if frame_hash == self._last_hash and self._last_frame is not None:
            self.stats_skipped += 1
            return None

        self._last_hash = frame_hash
        img = Image.frombytes("RGB", screenshot.size, raw, "raw", "BGRX")
        if img.width > self.max_width:
            ratio = self.max_width / img.width
            img = img.resize((self.max_width, int(img.height * ratio)), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=self.quality, optimize=True)
        self._last_frame = buf.getvalue()
        return self._last_frame

    def reset_stats(self):
        total, skipped = self.stats_total, self.stats_skipped
        self.stats_total = self.stats_skipped = 0
        return total, skipped


# ─── 输入模拟 ────────────────────────────────────────

KEY_MAP = {
    "Backspace": 0x08, "Tab": 0x09, "Enter": 0x0D, "Shift": 0x10,
    "Control": 0x11, "Alt": 0x12, "Escape": 0x1B, "Space": 0x20, " ": 0x20,
    "ArrowLeft": 0x25, "ArrowUp": 0x26, "ArrowRight": 0x27, "ArrowDown": 0x28,
    "Delete": 0x2E, "Insert": 0x2D, "Home": 0x24, "End": 0x23,
    "PageUp": 0x21, "PageDown": 0x22,
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73,
    "F5": 0x74, "F6": 0x75, "F7": 0x76, "F8": 0x77,
    "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
    "CapsLock": 0x14, "NumLock": 0x90, "ScrollLock": 0x91, "Meta": 0x5B,
}


class InputSimulator:
    # 所有可能"卡住"的修饰键 (Ctrl/Alt/Shift/Win)
    MODIFIER_VKS = [0x10, 0x11, 0x12, 0x5B, 0x5C,   # Shift, Ctrl, Alt, LWin, RWin
                    0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5]  # L/R Shift/Ctrl/Alt

    def __init__(self, width: int, height: int):
        self.screen_width = width
        self.screen_height = height
        self._pressed_keys: set[int] = set()   # 追踪所有按下的键

    def update_screen_size(self, w: int, h: int):
        self.screen_width = w
        self.screen_height = h

    def _send_input(self, inp):
        if HAS_WIN32:
            ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))

    def move_mouse(self, x: int, y: int):
        if not HAS_WIN32: return
        inp = INPUT(); inp.type = INPUT_MOUSE
        inp.union.mi.dx = int(x * 65535 / self.screen_width)
        inp.union.mi.dy = int(y * 65535 / self.screen_height)
        inp.union.mi.dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
        self._send_input(inp)

    def mouse_button(self, button: str, action: str):
        if not HAS_WIN32: return
        flags = {
            ("left","down"):MOUSEEVENTF_LEFTDOWN, ("left","up"):MOUSEEVENTF_LEFTUP,
            ("right","down"):MOUSEEVENTF_RIGHTDOWN, ("right","up"):MOUSEEVENTF_RIGHTUP,
            ("middle","down"):MOUSEEVENTF_MIDDLEDOWN, ("middle","up"):MOUSEEVENTF_MIDDLEUP,
        }.get((button, action))
        if not flags: return
        inp = INPUT(); inp.type = INPUT_MOUSE; inp.union.mi.dwFlags = flags
        self._send_input(inp)

    def mouse_scroll(self, delta: int):
        if not HAS_WIN32: return
        inp = INPUT(); inp.type = INPUT_MOUSE
        inp.union.mi.mouseData = ctypes.c_ulong(delta * 120 & 0xFFFFFFFF)
        inp.union.mi.dwFlags = MOUSEEVENTF_WHEEL
        self._send_input(inp)

    def key_event(self, key: str, action: str):
        if not HAS_WIN32: return
        inp = INPUT(); inp.type = INPUT_KEYBOARD
        vk = KEY_MAP.get(key)
        if vk is not None:
            inp.union.ki.wVk = vk
            inp.union.ki.dwFlags = KEYEVENTF_KEYUP if action == "up" else 0
            # 追踪按键状态
            if action == "up":
                self._pressed_keys.discard(vk)
            else:
                self._pressed_keys.add(vk)
        elif len(key) == 1:
            inp.union.ki.wScan = ord(key)
            inp.union.ki.dwFlags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if action == "up" else 0)
        else:
            return
        self._send_input(inp)

    def release_all_keys(self):
        """客户端断连时释放所有被卡住的键，防止修饰键锁死"""
        if not HAS_WIN32: return
        released = []
        # 1) 释放我们追踪到的按下的键
        for vk in list(self._pressed_keys):
            inp = INPUT(); inp.type = INPUT_KEYBOARD
            inp.union.ki.wVk = vk
            inp.union.ki.dwFlags = KEYEVENTF_KEYUP
            self._send_input(inp)
            released.append(f"0x{vk:02X}")
        self._pressed_keys.clear()
        # 2) 额外保险：无条件释放所有修饰键（防止追踪遗漏）
        for vk in self.MODIFIER_VKS:
            inp = INPUT(); inp.type = INPUT_KEYBOARD
            inp.union.ki.wVk = vk
            inp.union.ki.dwFlags = KEYEVENTF_KEYUP
            self._send_input(inp)
        if released:
            logger.info(f"🔓 释放卡住的按键: {', '.join(released)} + 所有修饰键")
        else:
            logger.info("🔓 安全释放所有修饰键")


# ─── 剪贴板 ──────────────────────────────────────────

class ClipboardManager:
    @staticmethod
    def get_text() -> str:
        if not HAS_WIN32: return ""
        try:
            ctypes.windll.user32.OpenClipboard(0)
            h = ctypes.windll.user32.GetClipboardData(13)
            text = ctypes.c_wchar_p(h).value or "" if h else ""
            ctypes.windll.user32.CloseClipboard()
            return text
        except Exception:
            try: ctypes.windll.user32.CloseClipboard()
            except: pass
            return ""

    @staticmethod
    def set_text(text: str):
        if not HAS_WIN32: return
        try:
            ctypes.windll.user32.OpenClipboard(0)
            ctypes.windll.user32.EmptyClipboard()
            data = text.encode("utf-16-le") + b"\x00\x00"
            h = ctypes.windll.kernel32.GlobalAlloc(0x0002, len(data))
            p = ctypes.windll.kernel32.GlobalLock(h)
            ctypes.memmove(p, data, len(data))
            ctypes.windll.kernel32.GlobalUnlock(h)
            ctypes.windll.user32.SetClipboardData(13, h)
            ctypes.windll.user32.CloseClipboard()
        except Exception:
            try: ctypes.windll.user32.CloseClipboard()
            except: pass


# ─── 配置文件 ────────────────────────────────────────

class ConfigManager:
    """管理 config.json：持久化密码、设备名等配置"""

    def __init__(self, config_path: str = None):
        if config_path:
            self.path = Path(config_path)
        else:
            self.path = Path(__file__).parent / "config.json"
        self._data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"读取配置文件失败: {e}")
        return {}

    def save(self):
        try:
            self.path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.error(f"保存配置文件失败: {e}")

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value):
        self._data[key] = value
        self.save()

    @property
    def password(self) -> Optional[str]:
        return self._data.get("password")

    @password.setter
    def password(self, value: str):
        self._data["password"] = value
        self.save()

    @property
    def device_name(self) -> str:
        return self._data.get("device_name", platform.node())

    @device_name.setter
    def device_name(self, value: str):
        self._data["device_name"] = value
        self.save()


# ─── 认证 ────────────────────────────────────────────

class AuthManager:
    def __init__(self, password: str):
        self.password = password
        self.password_hash = hashlib.sha256(password.encode()).hexdigest()

    def update_password(self, new_password: str):
        """运行时更新密码（已连接的客户端不受影响）"""
        self.password = new_password
        self.password_hash = hashlib.sha256(new_password.encode()).hexdigest()
        logger.info("🔑 连接密码已更新")

    def create_challenge(self) -> str:
        return secrets.token_hex(16)

    def verify(self, challenge: str, response: str) -> bool:
        expected = _hmac.HMAC(
            self.password_hash.encode(), challenge.encode(), hashlib.sha256
        ).hexdigest()
        return _hmac.compare_digest(expected, response)


# ─── 文件传输 ────────────────────────────────────────

class FileTransfer:
    """文件系统浏览 & 传输：浏览目录、上传到指定位置、下载、新建/删除"""

    def __init__(self, save_dir: str = None):
        if save_dir:
            self.default_dir = Path(save_dir)
        else:
            self.default_dir = Path.home() / "Desktop"
        self.default_dir.mkdir(parents=True, exist_ok=True)
        self._is_windows = platform.system() == "Windows"
        logger.info(f"文件传输默认目录: {self.default_dir}")

    # ── 安全检查 ──────────────────────────────────────
    @staticmethod
    def _safe_path(path_str: str) -> Path:
        """解析并验证路径，禁止路径穿越（..）"""
        p = Path(path_str).resolve()
        # 禁止任何包含 .. 的原始输入
        if ".." in Path(path_str).parts:
            raise PermissionError(f"路径穿越被拒绝: {path_str}")
        return p

    # ── 驱动器列表（Windows）────────────────────────
    def get_drives(self) -> list:
        """返回 Windows 驱动器列表，非 Windows 返回 ['/']"""
        if not self._is_windows:
            return [{"name": "/", "path": "/"}]
        drives = []
        # 检查 A-Z 盘符
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            dp = Path(f"{letter}:\\")
            if dp.exists():
                drives.append({"name": f"{letter}:", "path": f"{letter}:\\"})
        return drives

    # ── 浏览目录 ──────────────────────────────────────
    def browse_dir(self, path: str = None) -> dict:
        """浏览指定目录，返回 {path, parent, items}"""
        try:
            # 特殊值：空 / "root" / "drives" → 返回驱动器列表
            if not path or path in ("", "root", "drives"):
                if self._is_windows:
                    return {
                        "path": "",
                        "parent": None,
                        "items": [{"name": d["name"], "path": d["path"],
                                   "type": "drive", "is_dir": True, "size": 0, "mtime": 0}
                                  for d in self.get_drives()],
                    }
                else:
                    path = "/"

            dir_path = self._safe_path(path)
            if not dir_path.exists():
                return {"error": f"路径不存在: {path}"}
            if not dir_path.is_dir():
                return {"error": f"不是目录: {path}"}

            parent = str(dir_path.parent) if dir_path.parent != dir_path else None
            # Windows 盘根的 parent 设为空串（触发驱动器列表）
            if self._is_windows and parent and len(dir_path.parts) == 1:
                parent = ""

            items = []
            try:
                entries = sorted(dir_path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
            except PermissionError:
                return {"path": str(dir_path), "parent": parent, "items": [],
                        "error": "权限不足，无法读取目录"}

            for entry in entries:
                try:
                    st = entry.stat()
                    items.append({
                        "name": entry.name,
                        "path": str(entry),
                        "type": "dir" if entry.is_dir() else "file",
                        "is_dir": entry.is_dir(),
                        "size": st.st_size if entry.is_file() else 0,
                        "mtime": int(st.st_mtime),
                    })
                except (PermissionError, OSError):
                    items.append({
                        "name": entry.name,
                        "path": str(entry),
                        "type": "unknown",
                        "is_dir": False,
                        "size": 0,
                        "mtime": 0,
                    })

            return {"path": str(dir_path), "parent": parent, "items": items}
        except PermissionError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"浏览目录失败: {e}"}

    # ── 上传文件到指定目录 ────────────────────────────
    def save_file(self, dir_path: str, filename: str, data_b64: str) -> dict:
        """保存上传的文件到指定目录"""
        try:
            if not dir_path:
                dest = self.default_dir
            else:
                dest = self._safe_path(dir_path)

            if not dest.is_dir():
                return {"success": False, "error": f"目标目录不存在: {dir_path}"}

            safe_name = Path(filename).name  # 防路径穿越
            if not safe_name:
                return {"success": False, "error": "无效文件名"}

            # 同名文件自动加序号
            target = dest / safe_name
            if target.exists():
                stem = target.stem
                suffix = target.suffix
                i = 1
                while target.exists():
                    target = dest / f"{stem}_{i}{suffix}"
                    i += 1

            data = base64.b64decode(data_b64)
            target.write_bytes(data)
            size_kb = len(data) / 1024
            logger.info(f"📁 文件已保存: {target} ({size_kb:.1f} KB)")
            return {
                "success": True,
                "path": str(target),
                "size": len(data),
                "name": target.name,
            }
        except PermissionError:
            return {"success": False, "error": "权限不足，无法写入"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── 读取文件（用于下载）────────────────────────────
    def read_file(self, file_path: str) -> dict:
        """读取文件内容，返回 base64 编码"""
        try:
            fp = self._safe_path(file_path)
            if not fp.is_file():
                return {"success": False, "error": f"文件不存在: {file_path}"}
            data = fp.read_bytes()
            return {
                "success": True,
                "name": fp.name,
                "data": base64.b64encode(data).decode("ascii"),
                "size": len(data),
            }
        except PermissionError:
            return {"success": False, "error": "权限不足，无法读取"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── 新建文件夹 ────────────────────────────────────
    def create_dir(self, path: str) -> dict:
        """新建文件夹"""
        try:
            dp = self._safe_path(path)
            dp.mkdir(parents=True, exist_ok=True)
            logger.info(f"📂 文件夹已创建: {dp}")
            return {"success": True, "path": str(dp)}
        except PermissionError:
            return {"success": False, "error": "权限不足，无法创建"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── 删除文件/空文件夹 ─────────────────────────────
    def delete_item(self, path: str) -> dict:
        """删除文件或空文件夹"""
        try:
            dp = self._safe_path(path)
            if not dp.exists():
                return {"success": False, "error": f"路径不存在: {path}"}
            if dp.is_file():
                dp.unlink()
                logger.info(f"🗑️ 文件已删除: {dp}")
            elif dp.is_dir():
                dp.rmdir()  # 仅删除空目录
                logger.info(f"🗑️ 文件夹已删除: {dp}")
            else:
                return {"success": False, "error": "不支持的文件类型"}
            return {"success": True, "path": str(dp)}
        except OSError as e:
            return {"success": False, "error": f"删除失败（目录非空？）: {e}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── 复制文件到指定目录（远程内部复制）────────────
    def copy_file(self, src_path: str, dest_dir: str) -> dict:
        """复制文件到指定目录"""
        import shutil
        try:
            src = self._safe_path(src_path)
            if not src.exists():
                return {"success": False, "error": f"源文件不存在: {src_path}"}
            dest = self._safe_path(dest_dir)
            if not dest.is_dir():
                return {"success": False, "error": f"目标目录不存在: {dest_dir}"}
            target = dest / src.name
            # 同名文件自动加序号
            if target.exists():
                stem, suffix = target.stem, target.suffix
                i = 1
                while target.exists():
                    target = dest / f"{stem}_{i}{suffix}"
                    i += 1
            if src.is_file():
                shutil.copy2(str(src), str(target))
                logger.info(f"📋 文件已复制: {src} → {target}")
            elif src.is_dir():
                shutil.copytree(str(src), str(target))
                logger.info(f"📋 目录已复制: {src} → {target}")
            else:
                return {"success": False, "error": "不支持的文件类型"}
            return {"success": True, "src": str(src), "dest": str(target), "name": target.name}
        except PermissionError:
            return {"success": False, "error": "权限不足"}
        except Exception as e:
            return {"success": False, "error": str(e)}


# ─── 内嵌 Web 服务 ───────────────────────────────────

def get_web_html(ws_port: int) -> str:
    """读取 web/index.html 并注入 WebSocket 端口配置"""
    # 先找同级 web 目录
    candidates = [
        Path(__file__).parent.parent / "web" / "index.html",
        Path(__file__).parent / "web" / "index.html",
        Path.cwd() / "web" / "index.html",
    ]
    for p in candidates:
        if p.exists():
            html = p.read_text(encoding="utf-8")
            # 在 </head> 前注入 WS 端口配置
            inject = f'<script>window.__WS_PORT__={ws_port};</script>\n</head>'
            html = html.replace('</head>', inject)
            return html

    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>RemoteDesk</title></head>
<body style="background:#0d1117;color:#e6edf3;display:flex;align-items:center;justify-content:center;height:100vh;font-family:sans-serif">
<div style="text-align:center"><h2>⚠️ 找不到 web/index.html</h2>
<p>请确保 web 目录与 server 目录同级</p></div></body></html>"""


class WebHandler(SimpleHTTPRequestHandler):
    """内嵌 HTTP 服务，提供控制端页面"""
    html_content = ""
    ws_port = 8765

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            content = self.html_content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(content))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_error(404)

    def log_message(self, format, *args):
        pass  # 静默


def start_web_server(host: str, port: int, ws_port: int):
    """在后台线程启动 HTTP 服务"""
    WebHandler.html_content = get_web_html(ws_port)
    WebHandler.ws_port = ws_port

    server = socketserver.TCPServer((host, port), WebHandler)
    server.allow_reuse_address = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


# ─── WebSocket 服务 ──────────────────────────────────

class RemoteDesktopServer:
    def __init__(self, host: str, port: int, fps: int, quality: int,
                 password: str, file_dir: str = None,
                 config: ConfigManager = None):
        self.host = host
        self.port = port
        self.fps = fps
        self.quality = quality
        self.password = password
        self.config = config or ConfigManager()
        self.auth = AuthManager(password)
        self.clipboard = ClipboardManager()
        self.file_transfer = FileTransfer(file_dir)

        self.capture = ScreenCapture(quality=quality)
        screen = self.capture.current_monitor
        self.input_sim = InputSimulator(screen["width"], screen["height"])

        self.clients: dict = {}  # ws -> {addr, connected_at, ...}

    def _system_info(self) -> dict:
        return {
            "hostname": self.config.device_name,
            "os": f"{platform.system()} {platform.release()}",
            "python": platform.python_version(),
        }

    async def _authenticate(self, ws) -> bool:
        challenge = self.auth.create_challenge()
        await ws.send(json.dumps({"type": "auth_challenge", "challenge": challenge}))
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=15)
            data = json.loads(msg)
            if data.get("type") != "auth_response":
                return False
            return self.auth.verify(challenge, data.get("response", ""))
        except (asyncio.TimeoutError, json.JSONDecodeError):
            return False

    async def register(self, ws):
        addr = ws.remote_address
        self.clients[ws] = {
            "addr": f"{addr[0]}:{addr[1]}",
            "connected_at": time.time(),
        }
        screen = self.capture.current_monitor
        logger.info(f"✓ 客户端已认证: {addr[0]}:{addr[1]} │ 在线: {len(self.clients)}")

        await ws.send(json.dumps({
            "type": "connected",
            "screen": screen,
            "monitors": self.capture.monitors,
            "fps": self.fps,
            "quality": self.quality,
            "system": self._system_info(),
            "file_dir": str(self.file_transfer.default_dir),
        }))

        # 通知所有客户端更新在线列表
        await self._broadcast_clients()

    async def unregister(self, ws):
        info = self.clients.pop(ws, {})
        addr = info.get("addr", "?")
        # ★ 关键修复：客户端断连时释放所有被卡住的键
        if not self.clients:  # 最后一个客户端断开
            self.input_sim.release_all_keys()
        logger.info(f"✗ 客户端断开: {addr} │ 在线: {len(self.clients)}")
        await self._broadcast_clients()

    async def _broadcast_clients(self):
        """广播在线客户端列表"""
        clients_list = []
        for w, info in self.clients.items():
            clients_list.append({
                "addr": info["addr"],
                "duration": int(time.time() - info["connected_at"]),
            })
        msg = json.dumps({"type": "clients_list", "clients": clients_list})
        for client in list(self.clients.keys()):
            try:
                await client.send(msg)
            except:
                pass

    def handle_input(self, data: dict):
        t = data.get("type")
        if t == "mouse_move":    self.input_sim.move_mouse(data["x"], data["y"])
        elif t == "mouse_down":  self.input_sim.mouse_button(data.get("button","left"), "down")
        elif t == "mouse_up":    self.input_sim.mouse_button(data.get("button","left"), "up")
        elif t == "mouse_scroll":self.input_sim.mouse_scroll(data.get("delta", 0))
        elif t == "key_down":    self.input_sim.key_event(data["key"], "down")
        elif t == "key_up":      self.input_sim.key_event(data["key"], "up")

    async def handle_control(self, ws, data: dict):
        t = data.get("type")

        if t == "set_quality":
            self.capture.set_quality(data.get("quality", 50))
            self.quality = self.capture.quality
            await ws.send(json.dumps({"type": "quality_changed", "quality": self.quality}))

        elif t == "set_fps":
            self.fps = max(1, min(60, data.get("fps", 30)))
            await ws.send(json.dumps({"type": "fps_changed", "fps": self.fps}))

        elif t == "set_monitor":
            idx = data.get("index", 1)
            self.capture.set_monitor(idx)
            screen = self.capture.current_monitor
            self.input_sim.update_screen_size(screen["width"], screen["height"])
            await ws.send(json.dumps({"type": "monitor_changed", "index": idx, "screen": screen}))

        elif t == "get_clipboard":
            await ws.send(json.dumps({"type": "clipboard_data", "text": self.clipboard.get_text()}))

        elif t == "set_clipboard":
            self.clipboard.set_text(data.get("text", ""))

        elif t == "browse_dir":
            result = self.file_transfer.browse_dir(data.get("path"))
            side = data.get("side", "right")
            await ws.send(json.dumps({"type": "dir_content", "side": side, **result}))

        elif t == "copy_file":
            result = self.file_transfer.copy_file(data.get("src", ""), data.get("dest_dir", ""))
            await ws.send(json.dumps({"type": "file_copied", **result}))

        elif t == "upload_file":
            result = self.file_transfer.save_file(
                data.get("dir_path", ""), data.get("name", ""), data.get("data", ""))
            await ws.send(json.dumps({"type": "file_saved", **result}))

        elif t == "download_file":
            result = self.file_transfer.read_file(data.get("path", ""))
            await ws.send(json.dumps({"type": "file_data", **result}))

        elif t == "create_dir":
            result = self.file_transfer.create_dir(data.get("path", ""))
            await ws.send(json.dumps({"type": "dir_created", **result}))

        elif t == "delete_item":
            result = self.file_transfer.delete_item(data.get("path", ""))
            await ws.send(json.dumps({"type": "item_deleted", **result}))

        elif t == "ping":
            await ws.send(json.dumps({"type": "pong", "ts": data.get("ts", 0)}))

        elif t == "list_clients":
            await self._broadcast_clients()

        elif t == "change_password":
            new_pwd = data.get("password", "").strip()
            if not new_pwd:
                await ws.send(json.dumps({"type": "password_changed",
                                          "success": False, "error": "密码不能为空"}))
                return
            self.password = new_pwd
            self.auth.update_password(new_pwd)
            self.config.password = new_pwd
            logger.info(f"🔑 密码已被客户端 {self.clients.get(ws, {}).get('addr', '?')} 修改")
            await ws.send(json.dumps({"type": "password_changed",
                                      "success": True, "password": new_pwd}))

        elif t == "set_device_name":
            name = data.get("name", "").strip()
            if name:
                self.config.device_name = name
                logger.info(f"📛 设备名已更新: {name}")
                await ws.send(json.dumps({"type": "device_name_changed",
                                          "success": True, "name": name}))

        elif t == "get_agent_info":
            await ws.send(json.dumps({
                "type": "agent_info",
                "password": self.password,
                "device_name": self.config.device_name,
                "system": self._system_info(),
            }))

    async def handler(self, ws):
        addr = ws.remote_address
        logger.info(f"→ 新连接: {addr[0]}:{addr[1]} │ 等待认证...")

        if not await self._authenticate(ws):
            logger.warning(f"✗ 认证失败: {addr[0]}:{addr[1]}")
            await ws.send(json.dumps({"type": "auth_failed", "message": "密码错误"}))
            await ws.close()
            return

        await self.register(ws)
        try:
            async for message in ws:
                try:
                    data = json.loads(message)
                    t = data.get("type", "")
                    if t.startswith("mouse_") or t.startswith("key_"):
                        self.handle_input(data)
                    else:
                        await self.handle_control(ws, data)
                except (json.JSONDecodeError, KeyError):
                    pass
        except websockets.ConnectionClosed:
            pass
        finally:
            await self.unregister(ws)

    async def broadcast_screen(self):
        sent_frames = 0
        last_log = time.time()

        while True:
            interval = 1.0 / self.fps
            if self.clients:
                try:
                    frame = self.capture.capture()
                    if frame is not None:
                        sent_frames += 1
                        dead = set()
                        await asyncio.gather(*(
                            self._safe_send(c, frame, dead)
                            for c in list(self.clients.keys())
                        ))
                        for d in dead:
                            self.clients.pop(d, None)

                    now = time.time()
                    if now - last_log >= 5:
                        elapsed = now - last_log
                        fps_actual = sent_frames / elapsed
                        total, skipped = self.capture.reset_stats()
                        skip_pct = (skipped / max(total, 1)) * 100
                        size_kb = len(self.capture._last_frame or b"") / 1024
                        logger.info(
                            f"📊 {fps_actual:.1f} fps │ {size_kb:.0f} KB/帧 │ "
                            f"跳过 {skip_pct:.0f}% │ 画质 {self.quality} │ "
                            f"客户端 {len(self.clients)}"
                        )
                        sent_frames = 0
                        last_log = now
                except Exception as e:
                    logger.error(f"屏幕捕获错误: {e}")

            await asyncio.sleep(interval)

    async def _safe_send(self, client, data, dead_set):
        try:
            await client.send(data)
        except websockets.ConnectionClosed:
            dead_set.add(client)

    async def run(self, web_port: int):
        screen = self.capture.current_monitor

        # 启动内嵌 Web 服务
        try:
            web_server = start_web_server(self.host, web_port, self.port)
            web_ok = True
        except Exception as e:
            logger.warning(f"Web 服务启动失败: {e}")
            web_ok = False

        print()
        print("╔══════════════════════════════════════════════════════╗")
        print("║            🖥️  RemoteDesk Agent  v4.0               ║")
        print("╠══════════════════════════════════════════════════════╣")
        if web_ok:
            print(f"║  🌐 控制端:  http://{self.host}:{web_port:<28}║")
        print(f"║  🔌 WS:      ws://{self.host}:{self.port:<29}║")
        print(f"║  🔑 密码:    {self.password:<39}║")
        print(f"║  📛 设备名:  {self.config.device_name:<38}║")
        print(f"║  🖥️ 屏幕:    {screen['width']}x{screen['height']:<32}║")
        print(f"║  🎬 帧率:    {self.fps} fps │ 画质: {self.quality:<21}║")
        print(f"║  📁 文件:    {str(self.file_transfer.default_dir):<38}║")
        print(f"║  📄 配置:    {str(self.config.path):<38}║")
        print("╠══════════════════════════════════════════════════════╣")
        if web_ok:
            print("║  浏览器访问上面的控制端地址即可连接                  ║")
        else:
            print("║  直接打开 web/index.html 连接                       ║")
        print("║  Ctrl+C 停止                                         ║")
        print("╚══════════════════════════════════════════════════════╝")
        print()

        async with serve(
            self.handler, self.host, self.port,
            max_size=50 * 1024 * 1024,  # 50MB for file upload
            ping_interval=20, ping_timeout=10,
        ):
            await self.broadcast_screen()


# ─── 入口 ────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="RemoteDesk 被控端 Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               "  python agent.py                       随机密码启动\n"
               "  python agent.py -p 123456             指定密码\n"
               "  python agent.py --fps 60 --quality 70 高画质模式\n"
               "  python agent.py --web-port 9000       自定义 Web 端口\n"
               "  python agent.py --device-name 办公室   设置设备名\n",
    )
    parser.add_argument("--host", default="0.0.0.0", help="监听地址 (默认 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8765, help="WebSocket 端口 (默认 8765)")
    parser.add_argument("--web-port", type=int, default=8000, help="Web 页面端口 (默认 8000)")
    parser.add_argument("--fps", type=int, default=30, help="帧率 1-60 (默认 30)")
    parser.add_argument("--quality", type=int, default=50, help="JPEG 质量 10-95 (默认 50)")
    parser.add_argument("-p", "--password", default=None, help="连接密码 (不指定则读取 config.json 或随机生成)")
    parser.add_argument("--file-dir", default=None, help="文件保存目录 (默认桌面/RemoteDesk_Files)")
    parser.add_argument("--config", default=None, help="配置文件路径 (默认 server/config.json)")
    parser.add_argument("--device-name", default=None, help="设备名称 (保存到配置文件)")
    args = parser.parse_args()

    args.fps = max(1, min(60, args.fps))
    args.quality = max(10, min(95, args.quality))

    # 配置文件管理
    config = ConfigManager(args.config)

    # 设备名 (命令行 > 配置文件 > 系统主机名)
    if args.device_name:
        config.device_name = args.device_name

    # 密码优先级: 命令行 > config.json > 随机生成
    if args.password:
        password = args.password
        config.password = password  # 同步保存到配置文件
    elif config.password:
        password = config.password
        logger.info("📄 从配置文件加载密码")
    else:
        password = secrets.token_urlsafe(8)
        config.password = password  # 首次随机的也存起来

    server = RemoteDesktopServer(
        args.host, args.port, args.fps, args.quality, password, args.file_dir,
        config=config,
    )
    try:
        asyncio.run(server.run(args.web_port))
    except KeyboardInterrupt:
        print("\n✓ Agent 已停止")


if __name__ == "__main__":
    main()
