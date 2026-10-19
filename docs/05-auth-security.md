# 05 — 认证与安全

## 问题是什么

远程桌面需要密码保护——但怎么验证密码？

最天真的方案：客户端发密码明文，服务端比对。**这是不安全的**——任何在同一网络的人抓包就能看到密码。

## HMAC-SHA256 Challenge-Response 认证

我们使用**挑战-应答 (Challenge-Response)** 机制。密码本身永远不在网络上传输。

### 完整流程

```
Agent (服务端)                              Browser (客户端)
     │                                           │
     │  ① 生成随机 challenge                      │
     │     challenge = random_hex(16)             │
     │                                           │
     │  ──── {"challenge": "a1b2c3..."} ────→     │
     │                                           │  ② 用户输入密码
     │                                           │     password_hash = SHA256(password)
     │                                           │     response = HMAC-SHA256(password_hash, challenge)
     │                                           │
     │  ←── {"response": "x7y8z9..."} ────────   │
     │                                           │
     │  ③ 服务端用自己存的 password_hash           │
     │     expected = HMAC-SHA256(password_hash, challenge)
     │     比对 expected == response              │
     │                                           │
     │  ──── 认证成功 / 认证失败 ────────────→     │
```

### 为什么安全？

1. **密码不传输**：网络上只传 challenge（随机数）和 response（HMAC 值）
2. **不可重放**：每次连接的 challenge 都是随机的，即使截获了 response 也不能用于下次连接
3. **不可逆推**：从 HMAC 值无法反推出 password_hash（单向函数）

### 攻击者能看到什么？

```
抓包看到的数据：
  challenge = "a1b2c3d4e5f6g7h8"
  response  = "9f2a...（64位十六进制）"

攻击者想要的：
  password  = ???

攻击者能做什么？
  ❌ 从 response 反推 password — HMAC-SHA256 是单向的
  ❌ 用截获的 response 重放 — 下次 challenge 不同
  ⚠️ 暴力破解 — 如果密码简单（如 "123456"），可以遍历常见密码
```

## 代码实现

### 服务端 (Python)

```python
import hashlib
import hmac as _hmac
import secrets

class AuthManager:
    def __init__(self, password: str):
        # 存储密码的 SHA-256 哈希，而不是明文
        self.password_hash = hashlib.sha256(password.encode()).hexdigest()

    def create_challenge(self) -> str:
        return secrets.token_hex(16)  # 32 字符的随机十六进制

    def verify(self, challenge: str, response: str) -> bool:
        expected = _hmac.HMAC(
            self.password_hash.encode(),   # 密钥 = 密码哈希
            challenge.encode(),            # 消息 = challenge
            hashlib.sha256                 # 哈希算法
        ).hexdigest()
        return _hmac.compare_digest(expected, response)  # 时间恒定比较
```

### 客户端 (JavaScript)

```javascript
// SHA-256
async function sha256(text) {
    const buf = await crypto.subtle.digest(
        'SHA-256',
        new TextEncoder().encode(text)
    );
    return [...new Uint8Array(buf)]
        .map(x => x.toString(16).padStart(2, '0'))
        .join('');
}

// HMAC-SHA256
async function hmacSha256(key, message) {
    const cryptoKey = await crypto.subtle.importKey(
        'raw',
        new TextEncoder().encode(key),
        { name: 'HMAC', hash: 'SHA-256' },
        false,
        ['sign']
    );
    const sig = await crypto.subtle.sign(
        'HMAC',
        cryptoKey,
        new TextEncoder().encode(message)
    );
    return [...new Uint8Array(sig)]
        .map(x => x.toString(16).padStart(2, '0'))
        .join('');
}
```

### 注意事项

**`hmac.compare_digest()` 而不是 `==`**

普通的字符串比较 `==` 在发现第一个不同字符时就返回 False——这意味着**比较时间和正确字符数量相关**。攻击者可以通过计时来逐字节猜密码（时序攻击）。

`hmac.compare_digest()` 保证**不管是否匹配，比较时间恒定**，防御时序攻击。

**Web Crypto API 的安全上下文要求**

`crypto.subtle` 只在**安全上下文**中可用：
- ✅ `https://` 任何域名
- ✅ `http://localhost` 或 `http://127.0.0.1`
- ❌ `http://192.168.1.100`（局域网 IP，非安全上下文）

**这是一个实际踩过的坑**：通过局域网 IP 访问时，`crypto.subtle` 为 `undefined`，SHA-256 和 HMAC 计算静默失败，认证永远通不过。

我们的解决方案：**纯 JavaScript SHA-256/HMAC-SHA256 回退实现**。优先使用 `crypto.subtle`（更快），不可用时自动降级到纯 JS 实现。注意 JS 位运算产生 32 位有符号整数，转十六进制时必须用 `(w >>> 0).toString(16)` 避免负数输出。

## 密码持久化

密码保存在 `config.json` 中：

```json
{
  "password": "your-password-here",
  "device_name": "办公室电脑"
}
```

优先级：命令行 `-p` > config.json > 随机生成

运行时可以通过 WebSocket 修改密码（已连接的客户端不受影响）。

## 代码位置

- 认证管理：`server/agent.py` — `AuthManager` 类
- 认证流程：`server/agent.py` — `_authenticate()` 方法
- 浏览器加密：`web/index.html` — `sha256()` + `hmacSha256()` 函数

## 这个方案的局限

1. **没有传输加密**：认证后的数据（画面帧、键鼠事件）是明文的。局域网内问题不大，但走公网就不安全了。解决方案：加 TLS（WSS）。
2. **没有防暴力破解**：攻击者可以不断尝试连接。解决方案：失败 N 次后锁定/延迟。
3. **密码哈希存在 config.json**：拿到文件就等于拿到密码。解决方案：操作系统级密钥存储（Keychain/Credential Manager）。

这些都是可以逐步改进的方向。对于局域网/VPN 使用场景，当前方案已经足够。
