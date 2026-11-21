"""公共辅助：登录态、提示页、模板过滤器。"""

import datetime
import functools
import json
import os
import secrets
from flask import session, redirect, url_for, render_template, request
from markupsafe import Markup, escape


def now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def current_user():
    """返回 {'id':.., 'username':..} 或 None。登录态存于签名 Cookie。"""
    uid = session.get("uid")
    if not uid:
        return None
    return {"id": uid, "username": session.get("uname", "")}


def login_user(user_id, username):
    session["uid"] = user_id
    session["uname"] = username
    session.permanent = True


def logout_user():
    session.pop("uid", None)
    session.pop("uname", None)


def guest_key():
    """游客身份键：存于签名 Cookie，未登录者的会话靠它区分。"""
    k = session.get("gkey")
    if not k:
        k = secrets.token_hex(16)
        session["gkey"] = k
        session.permanent = True
    return k


CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def load_privileged():
    """读取 config.json 的 privileged_users（用户名列表）。
    每次调用都重新读文件，修改后无需重启即可生效。"""
    try:
        with open(CONFIG_PATH, "rb") as f:
            cfg = json.loads(f.read().decode("utf-8"))
    except (OSError, ValueError):
        return []
    users = cfg.get("privileged_users") if isinstance(cfg, dict) else None
    return users if isinstance(users, list) else []


def is_privileged(username):
    return username in load_privileged()


def login_required(view):
    """页面需要登录；未登录跳登录页并记住来源。"""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            return redirect(url_for("auth.login_page", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)
    return wrapped


def msg_page(title, body_lines, back="/"):
    """统一的纯文本提示页。"""
    return render_template("message.html", title=title,
                           body_lines=body_lines, back=back)


def nl2br(text):
    """转义后把换行转成 <br>，用于服务端渲染的文本。
    注意：Markup.replace 会转义参数，须先转回普通 str 再替换。"""
    escaped = str(escape(text or ""))
    return Markup(escaped.replace("\r\n", "\n").replace("\n", "<br>"))


def register_filters(app):
    app.jinja_env.filters["nl2br"] = nl2br
