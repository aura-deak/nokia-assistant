"""账号：注册、登录、登出。全站共用一个登录态。"""

import io
import json
import re
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
    cap = SpecCaptcha(width=CAPTCHA_WIDTH, height=CAPTCHA_HEIGHT, length=CAPTCHA_LEN)
    fixed = _captcha_fixed()
    if fixed:
        cap._chars = fixed
    code = cap.text()
    session["cap"] = code
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
    return bool(want) and bool(got) and got.lower() == want.lower()


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("register.html")
    if not _check_captcha():
        return render_template("register.html", error="验证码错误，请重新输入")
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    if not USERNAME_RE.match(username):
        return render_template("register.html", error="用户名须为2-16位字母/数字/下划线/中文")
    if len(password) < 4 or len(password) > 64:
        return render_template("register.html", error="密码长度须为4-64位")
    db = get_db()
    if db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
        return render_template("register.html", error="该用户名已被注册")
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
        return render_template("login.html", next=nxt)
    if not _check_captcha():
        return render_template("login.html", error="验证码错误，请重新输入", next=nxt)
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    row = get_db().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if row and check_password_hash(row["password_hash"], password):
        login_user(row["id"], row["username"])
        return redirect(nxt)
    return render_template("login.html", error="用户名或密码错误", next=nxt)


@bp.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("index"))
