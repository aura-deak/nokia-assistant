"""诺基亚助手 · 功能机浏览器友好的动态网站（Flask）。
所有动态内容由服务端渲染，页面输出为最简 HTML（无 CSS/JS）。"""

import os
import secrets

from flask import Flask, render_template, session

from db import init_db, get_db, close_db
from helpers import current_user, register_filters
import auth
import browse
import aichat
import novels


def create_app():
    app = Flask(__name__)
    base = os.path.dirname(os.path.abspath(__file__))
    key_path = os.path.join(base, "secret.key")
    if not os.path.exists(key_path):
        with open(key_path, "w") as f:
            f.write(secrets.token_hex(32))
    app.config.update(
        SECRET_KEY=open(key_path).read().strip(),
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
        return render_template("index.html", user=user)

    @app.context_processor
    def inject_user():
        return {"g_user": current_user()}

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
