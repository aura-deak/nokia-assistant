"""账号：注册、登录、登出。全站共用一个登录态。"""

import io
import json
import re
import secrets
from flask import (Blueprint, make_response, request, redirect, session,
                   url_for, render_template)
from werkzeug.security import generate_password_hash, check_password_hash

from easy_captcha import SpecCaptcha

from db import get_db
from helpers import CONFIG_PATH, login_user, logout_user, now_str

bp = Blueprint("auth", __name__)

USERNAME_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fa5]{2,16}$")
CAPTCHA_WIDTH, CAPTCHA_HEIGHT, CAPTCHA_LEN = 120, 40, 4


def _captcha_fixed():
    """config.json 的 captcha_fixed：测试用固定验证码，正式部署留空。"""
    try:
        with open(CONFIG_PATH, "rb") as f:
            cfg = json.loads(f.read().decode("utf-8"))
        v = cfg.get("captcha_fixed") if isinstance(cfg, dict) else None
        if isinstance(v, str) and 2 <= len(v) <= 8:
            return v
    except (OSError, ValueError):
        pass
    return ""


@bp.route("/captcha.png")
def captcha_png():
    # 页面渲染时已经把验证码定下来了，这里只负责画出来，
    # 图片晚到或重载都不会换掉用户看到的那个码。
    code = session.get("cap") or _new_code()
    session["cap"] = code
    cap = SpecCaptcha(width=CAPTCHA_WIDTH, height=CAPTCHA_HEIGHT, length=CAPTCHA_LEN)
    cap._chars = code
    buf = io.BytesIO()
    cap.out(buf)
    resp = make_response(buf.getvalue())
    resp.headers["Content-Type"] = "image/png"
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _check_captcha():
    """校验并一次性作废验证码。"""
    got = (request.form.get("captcha") or "").strip()
    want = session.pop("cap", None)
    session.pop("cap_tok", None)
    return bool(want) and bool(got) and got.lower() == want.lower()


def _new_code():
    """生成一个验证码文本，config.json 里配了 captcha_fixed 就用它。"""
    fixed = _captcha_fixed()
    if fixed:
        return fixed
    cap = SpecCaptcha(width=CAPTCHA_WIDTH, height=CAPTCHA_HEIGHT, length=CAPTCHA_LEN)
    cap.check_alpha()
    return cap.text()


def _prepare_captcha(force=False):
    """页面渲染前先把验证码放进会话，返回图片的缓存标识。

    功能机会延迟加载图片，如果等图片请求才生成验证码，用户第一次提交时
    会话里还没有码，必然误报验证码错误。这里改成渲染页面时就生成。
    """
    if force or not session.get("cap"):
        session["cap"] = _new_code()
        session["cap_tok"] = secrets.token_hex(4)
    return session.get("cap_tok", "")


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("register.html", cap_token=_prepare_captcha())
    if not _check_captcha():
        return render_template("register.html", error="验证码错误，请重新输入",
                               cap_token=_prepare_captcha(force=True))
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    if not USERNAME_RE.match(username):
        return render_template("register.html", error="用户名须为2-16位字母/数字/下划线/中文",
                               cap_token=_prepare_captcha(force=True))
    if len(password) < 4 or len(password) > 64:
        return render_template("register.html", error="密码长度须为4-64位",
                               cap_token=_prepare_captcha(force=True))
    db = get_db()
    if db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
        return render_template("register.html", error="该用户名已被注册",
                               cap_token=_prepare_captcha(force=True))
    cur = db.execute("INSERT INTO users(username,password_hash,created_at) VALUES(?,?,?)",
                     (username, generate_password_hash(password), now_str()))
    db.commit()
    login_user(cur.lastrowid, username)
    return redirect(url_for("index"))


@bp.route("/login", methods=["GET", "POST"])
def login_page():
    nxt = request.values.get("next") or "/"
    if not nxt.startswith("/"):  # 只允许站内跳转
        nxt = "/"
    if request.method == "GET":
        return render_template("login.html", next=nxt, cap_token=_prepare_captcha())
    if not _check_captcha():
        return render_template("login.html", error="验证码错误，请重新输入", next=nxt,
                               cap_token=_prepare_captcha(force=True))
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    row = get_db().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if row and check_password_hash(row["password_hash"], password):
        login_user(row["id"], row["username"])
        return redirect(nxt)
    return render_template("login.html", error="用户名或密码错误", next=nxt,
                           cap_token=_prepare_captcha(force=True))


@bp.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("index"))
