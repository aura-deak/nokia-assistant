"""账号：注册、登录、登出。全站共用一个登录态。"""

import re
from flask import Blueprint, request, redirect, url_for, render_template
from werkzeug.security import generate_password_hash, check_password_hash

from db import get_db
from helpers import login_user, logout_user, now_str

bp = Blueprint("auth", __name__)

USERNAME_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fa5]{2,16}$")


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("register.html")
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
