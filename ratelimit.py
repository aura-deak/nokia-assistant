"""IP 与用户名双维度限流（内存滑动窗口，60 秒）。
限额在 config.json 的 rate_limit 中配置，默认 IP 120/分、用户名 60/分。"""

import json
import threading
import time
from collections import defaultdict, deque

from helpers import CONFIG_PATH

_DEFAULT = {"ip_per_minute": 120, "user_per_minute": 60}
_WINDOW = 60.0
_windows = defaultdict(deque)
_lock = threading.Lock()
_counter = 0


def _limits():
    try:
        with open(CONFIG_PATH, "rb") as f:
            cfg = json.loads(f.read().decode("utf-8"))
        r = (cfg.get("rate_limit") or {}) if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        r = {}
    out = dict(_DEFAULT)
    for k in out:
        v = r.get(k)
        if isinstance(v, int) and v > 0:
            out[k] = v
    return out


def _allow(key, limit):
    global _counter
    now = time.time()
    with _lock:
        dq = _windows[key]
        while dq and dq[0] <= now - _WINDOW:
            dq.popleft()
        ok = len(dq) < limit
        dq.append(now)
        _counter += 1
        if _counter % 512 == 0:      # 定期清理空窗口，防内存增长
            for k in [k for k, d in _windows.items() if not d]:
                del _windows[k]
    return ok


def check(ip, username=None):
    """返回 (ok, 超限维度)；ip 先于用户名检查，维度为 'ip' 或 'user'。"""
    lim = _limits()
    if not _allow("ip:" + str(ip), lim["ip_per_minute"]):
        return False, "ip"
    if username and not _allow("user:" + str(username), lim["user_per_minute"]):
        return False, "user"
    return True, None
