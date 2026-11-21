"""模型分层：
- 免费模型（radeon / NVIDIA NIM）：所有人可用，含未登录游客；
- 本地聚合 magpie（127.0.0.1:1234）：特权用户（config.json）可用；
- 个人接口：登录用户在 AI 设置里自行配置。
密钥保存在 data/api_keys.json（已在 .gitignore 中，不入 git）。"""

import json
import os

from db import DATA_DIR
from helpers import is_privileged

KEYS_PATH = os.path.join(DATA_DIR, "api_keys.json")


def _load():
    try:
        with open(KEYS_PATH, "rb") as f:
            data = json.loads(f.read().decode("utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _free_groups():
    out = []
    for g in _load().get("free_groups", []):
        if isinstance(g, dict) and g.get("base_url") and g.get("api_key") and g.get("models"):
            out.append(g)
    return out


def _magpie():
    g = _load().get("magpie")
    if isinstance(g, dict) and g.get("base_url") and g.get("api_key") and g.get("models"):
        return g
    return {}


def _free_options():
    return [("free:" + m, "免费·" + m)
            for g in _free_groups() for m in (g.get("models") or [])]


def _magpie_options():
    return [("magpie:" + m, "本地·" + m) for m in (_magpie().get("models") or [])]


def model_options(user, personal_model):
    """当前用户可用的模型选项 [(value, label)] 与默认选中值。"""
    opts = []
    if user and personal_model:
        opts.append(("personal", "我的接口·" + personal_model))
    opts.extend(_free_options())
    if user and is_privileged(user["username"]):
        opts.extend(_magpie_options())
    return opts, (opts[0][0] if opts else None)


def valid_label(label, user, personal_model):
    opts, _ = model_options(user, personal_model)
    return label in dict(opts)


def resolve_model(label, user, settings_row):
    """把消息上的模型标签解析为 (base_url, api_key, model_id, temperature|None)。
    解析失败（不可用/无权限）返回 None。"""
    label = label or ""
    if label.startswith("free:"):
        mid = label[len("free:"):]
        for g in _free_groups():
            if mid in (g.get("models") or []):
                return g["base_url"], g["api_key"], mid, g.get("temperature")
        return None
    if label.startswith("magpie:"):
        if not (user and is_privileged(user["username"])):
            return None
        mid = label[len("magpie:"):]
        g = _magpie()
        if mid in (g.get("models") or []):
            return g["base_url"], g["api_key"], mid, g.get("temperature")
        return None
    if not user or settings_row is None:      # personal 仅登录用户可用
        return None
    base_url = (settings_row["base_url"] or "").strip()
    api_key = (settings_row["api_key"] or "").strip()
    model = (settings_row["model"] or "").strip()
    if not base_url or not api_key or not model:
        return None
    try:
        temp = float(settings_row["temperature"])
    except (TypeError, ValueError):
        temp = None
    return base_url, api_key, model, temp


def model_display(label):
    """把模型标签转成页面上的友好名称。"""
    if not label:
        return "免费模型"
    if label.startswith("free:"):
        return "免费·" + label[len("free:"):]
    if label.startswith("magpie:"):
        return "本地·" + label[len("magpie:"):]
    if label == "personal":
        return "我的接口"
    return label
