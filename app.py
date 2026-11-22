"""诺基亚助手 · 功能机浏览器友好的动态网站（Flask）。
所有动态内容由服务端渲染，页面输出为最简 HTML（无 CSS/JS）。"""

import os
import secrets

from flask import Flask, render_template, request, session, redirect, url_for

from db import init_db, get_db, close_db
from helpers import current_user, msg_page, register_filters
import auth
import browse
import aichat
import novels
import ratelimit


def create_app():
    app = Flask(__name__)
    base = os.path.dirname(os.path.abspath(__file__))
    key_path = os.path.join(base, "secret.key")
    key = (os.environ.get("SECRET_KEY") or "").strip()
    if not key and os.path.exists(key_path):
        key = open(key_path).read().strip()
    if len(key) < 64:                # 缺失或强度不足：重新生成 32 字节随机密钥
        key = secrets.token_hex(32)
        with open(key_path, "w") as f:
            f.write(key)
    try:
        os.chmod(key_path, 0o600)    # 密钥文件仅本人可读
    except OSError:
        pass
    app.config.update(
        SECRET_KEY=key,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,
        JSON_AS_ASCII=False,
    )
    init_db()
    app.teardown_appcontext(close_db)
    register_filters(app)
    from aikeys import model_display
    app.jinja_env.filters["aidisplay"] = model_display

    app.register_blueprint(auth.bp)
    app.register_blueprint(browse.bp)
    app.register_blueprint(aichat.bp)
    app.register_blueprint(novels.bp)

    @app.route("/")
    def index():
        user = current_user()
        if user is None:  # 首页不放登录表单，未登录直接去登录页
            return redirect(url_for("auth.login_page"))
        return render_template("index.html", user=user)

    @app.context_processor
    def inject_user():
        return {"g_user": current_user()}

    @app.before_request
    def rate_limit_guard():
        """IP + 用户名双维度限流（60 秒滑动窗口）。"""
        if request.endpoint in (None, "static"):
            return None
        ip = request.remote_addr or "?"
        if ip in ("127.0.0.1", "::1"):
            # 经 Cloudflare 隧道访问时取真实访客地址
            fwd = (request.headers.get("CF-Connecting-IP")
                   or (request.headers.get("X-Forwarded-For") or "").split(",")[0])
            fwd = (fwd or "").strip()[:64]
            if fwd:
                ip = fwd
        uname = ""
        user = current_user()
        if user:
            uname = user["username"]
        elif request.method == "POST" and request.endpoint in ("auth.login_page",
                                                               "auth.register"):
            uname = (request.form.get("username") or "").strip()[:32]
        ok, dim = ratelimit.check(ip, uname or None)
        if not ok:
            label = "IP" if dim == "ip" else "账号"
            return msg_page("请求过于频繁",
                            ["触发了%s限流，请稍后再试。" % label], back="/"), 429
        return None

    @app.after_request
    def no_cache(resp):
        # 功能机代理可能缓存页面；聊天状态需要手动刷新可见，必须禁缓存
        resp.headers["Cache-Control"] = "no-store, max-age=0"
        return resp

    @app.errorhandler(404)
    def not_found(_e):
        from helpers import msg_page
        return msg_page("页面不存在", ["请从主页重新进入。"], back="/"), 404

    @app.errorhandler(413)
    def too_large(_e):
        from helpers import msg_page
        return msg_page("内容过大", ["上传的文件超出限制（16MB）。"], back="/"), 413

    @app.errorhandler(500)
    def server_error(_e):
        from helpers import msg_page
        return msg_page("服务器开小差了", ["请返回重试。"], back="/"), 500

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, threaded=True)
