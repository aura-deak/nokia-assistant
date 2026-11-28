"""AI 对话：OpenAI 兼容接口，流式生成。生成在服务端后台线程进行，
状态分「等待开始输出」(pending) 与「正在输出」(streaming)，
页面带 meta 自动刷新，功能机浏览器无需手动刷新即可看到回复增长。
- 未登录游客：使用 data/api_keys.json 里的免费模型（radeon / NIM）；
- 特权用户（config.json）：额外可用 127.0.0.1:1234 的 magpie 聚合模型；
- 登录用户：也可配置个人接口（随账号保存）。
游客会话以 Cookie 中的 guest_key 归属，7 天未活动自动清理。"""

import datetime
import json
import threading
import time

import requests
from flask import (Blueprint, abort, redirect, render_template, request,
                   url_for)

from aikeys import model_display, model_options, resolve_model, valid_label
from db import get_db
from helpers import current_user, guest_key, is_privileged, login_required, msg_page, now_str

bp = Blueprint("ai", __name__, url_prefix="/ai")

CONTEXT_MESSAGES = 12          # 每次请求携带的历史条数
GEN_TIMEOUT = (10, 180)        # 连接/读超时
GUEST_TTL_DAYS = 7             # 游客会话保留天数
FLUSH_INTERVAL = 0.6           # 流式内容写库的最小间隔（秒）
_GEN_LOCKS = {}
_GEN_LOCKS_GUARD = threading.Lock()


def _conv_lock(cid):
    with _GEN_LOCKS_GUARD:
        lock = _GEN_LOCKS.get(cid)
        if lock is None:
            lock = threading.Lock()
            _GEN_LOCKS[cid] = lock
        return lock


def _get_settings(db, uid):
    row = db.execute("SELECT * FROM ai_settings WHERE user_id=?", (uid,)).fetchone()
    if row is None:
        row = {"base_url": "", "api_key": "", "model": "",
               "system_prompt": "", "temperature": "0.7"}
    return row


def _personal_model(user):
    if not user:
        return ""
    return (_get_settings(get_db(), user["id"])["model"] or "").strip()


def _own_conv(db, cid, user):
    """会话归属校验：登录用户按 user_id，游客按 guest_key。"""
    if user:
        row = db.execute("SELECT * FROM conversations WHERE id=? AND user_id=?",
                         (cid, user["id"])).fetchone()
    else:
        row = db.execute(
            "SELECT * FROM conversations WHERE id=? AND user_id=0 AND guest_key=?",
            (cid, guest_key())).fetchone()
    if row is None:
        abort(404)
    return row


def _list_convs(db, user):
    if user:
        return db.execute(
            "SELECT * FROM conversations WHERE user_id=? ORDER BY updated_at DESC LIMIT 30",
            (user["id"],)).fetchall()
    gk = guest_key()
    cutoff = (datetime.datetime.now() -
              datetime.timedelta(days=GUEST_TTL_DAYS)).strftime("%Y-%m-%d %H:%M")
    db.execute("DELETE FROM messages WHERE conv_id IN "
               "(SELECT id FROM conversations WHERE user_id=0 AND guest_key=? AND updated_at<?)",
               (gk, cutoff))
    db.execute("DELETE FROM conversations WHERE user_id=0 AND guest_key=? AND updated_at<?",
               (gk, cutoff))
    db.commit()
    return db.execute(
        "SELECT * FROM conversations WHERE user_id=0 AND guest_key=? "
        "ORDER BY updated_at DESC LIMIT 30", (gk,)).fetchall()


def _start_generation(cid):
    t = threading.Thread(target=_generate, args=(cid,), daemon=True)
    t.start()


def _completions_url(base_url):
    base = (base_url or "").strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def _generate(cid):
    """后台线程：按消息上记录的模型标签调用接口，写回待生成消息。"""
    from db import DB_PATH
    import sqlite3
    lock = _conv_lock(cid)
    with lock:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        conv = con.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
        pending = con.execute(
            "SELECT * FROM messages WHERE conv_id=? AND role='assistant' "
            "AND status='pending' ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
        if conv is None or pending is None:
            con.close()
            return

        user_obj = None
        settings_row = None
        if conv["user_id"]:
            urow = con.execute("SELECT username FROM users WHERE id=?",
                               (conv["user_id"],)).fetchone()
            if urow:
                user_obj = {"id": conv["user_id"], "username": urow["username"]}
                settings_row = _get_settings(con, conv["user_id"])

        def fail(text):
            con.execute("UPDATE messages SET status='error', content=? WHERE id=?",
                        (text[:400], pending["id"]))
            con.commit()
            con.close()

        resolved = resolve_model(pending["model"], user_obj, settings_row)
        if resolved is None:
            return fail("所选模型不可用或无权限，请重新发送并选择模型。")
        base_url, api_key, model_id, temp = resolved

        rows = con.execute(
            "SELECT role, content FROM messages WHERE conv_id=? AND "
            "(role='user' OR status='done') ORDER BY id DESC LIMIT ?",
            (cid, CONTEXT_MESSAGES)).fetchall()
        history = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
        payload = {"model": model_id, "messages": history, "stream": False}
        sys_prompt = ""
        if settings_row is not None:
            sys_prompt = (settings_row["system_prompt"] or "").strip()
        if sys_prompt:
            payload["messages"] = [{"role": "system", "content": sys_prompt}] + payload["messages"]
        if temp is None and settings_row is not None:
            try:
                temp = float(settings_row["temperature"])
            except (TypeError, ValueError):
                temp = None
        if temp is not None:
            payload["temperature"] = temp

        try:
            sse_payload = dict(payload)
            sse_payload["stream"] = True
            r = requests.post(_completions_url(base_url),
                              headers={"Authorization": "Bearer " + api_key,
                                       "Content-Type": "application/json",
                                       "Accept": "text/event-stream"},
                              json=sse_payload, timeout=GEN_TIMEOUT, stream=True)
            if r.status_code != 200:
                return fail("接口返回 HTTP %d: %s" % (r.status_code, r.text[:200]))
            r.encoding = "utf-8"   # SSE 响应常无 charset，不指定会被当 latin1 解成乱码
            content = ""
            got_token = False
            last_flush = time.monotonic()
            for raw in r.iter_lines(decode_unicode=True):
                if not raw:
                    continue
                line = raw.strip()
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line == "[DONE]":
                    break
                try:
                    chunk = json.loads(line)
                except ValueError:
                    continue
                try:
                    delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                except (AttributeError, IndexError, TypeError):
                    delta = {}
                piece = delta.get("content") if isinstance(delta, dict) else None
                if piece:
                    content += piece
                    if not got_token:
                        con.execute("UPDATE messages SET status='streaming', content=? "
                                    "WHERE id=?", (content, pending["id"]))
                        con.commit()
                        got_token = True
                    elif time.monotonic() - last_flush >= FLUSH_INTERVAL:
                        con.execute("UPDATE messages SET content=? WHERE id=?",
                                    (content, pending["id"]))
                        con.commit()
                        last_flush = time.monotonic()
            if not content:
                return fail("接口未返回内容")
            con.execute("UPDATE messages SET status='done', content=? WHERE id=?",
                        (content, pending["id"]))
            con.execute("UPDATE conversations SET updated_at=? WHERE id=?",
                        (now_str(), cid))
            con.commit()
        except requests.exceptions.Timeout:
            return fail("请求接口超时，请重试。")
        except requests.exceptions.RequestException as e:
            return fail("请求接口失败: " + str(e)[:300])
        except Exception as e:
            return fail("生成出错: " + str(e)[:300])
        finally:
            con.close()


@bp.route("/")
def index():
    user = current_user()
    db = get_db()
    convs = _list_convs(db, user)
    opts, default_model = model_options(user, _personal_model(user))
    return render_template("ai/index.html", convs=convs, user=user,
                           opts=opts, default_model=default_model)


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    user = current_user()
    db = get_db()
    test_result = None
    if request.method == "POST":
        if request.form.get("do") == "test":
            base_url = (request.form.get("base_url") or "").strip().rstrip("/")
            api_key = (request.form.get("api_key") or "").strip()
            if not api_key:
                api_key = (_get_settings(db, user["id"])["api_key"] or "").strip()
            if not base_url or not api_key:
                test_result = "请先填写接口地址和密钥"
            else:
                try:
                    r = requests.get(base_url + "/models",
                                     headers={"Authorization": "Bearer " + api_key},
                                     timeout=(10, 20))
                    if r.status_code == 200:
                        try:
                            ids = [m.get("id", "") for m in r.json().get("data", [])][:8]
                            test_result = "连接成功。可用模型: " + (", ".join(ids) or "未知")
                        except Exception:
                            test_result = "连接成功，但模型列表格式异常"
                    else:
                        test_result = "HTTP %d: %s" % (r.status_code, r.text[:150])
                except Exception as e:
                    test_result = "连接失败: " + str(e)[:150]
        else:
            base_url = (request.form.get("base_url") or "").strip()
            api_key = (request.form.get("api_key") or "").strip()
            model = (request.form.get("model") or "").strip()
            sys_prompt = (request.form.get("system_prompt") or "").strip()[:500]
            temp = request.form.get("temperature") or "0.7"
            if temp not in ("0", "0.3", "0.5", "0.7", "1.0", "1.3"):
                temp = "0.7"
            db.execute(
                "INSERT INTO ai_settings(user_id,base_url,api_key,model,system_prompt,temperature) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                "base_url=excluded.base_url, model=excluded.model, "
                "system_prompt=excluded.system_prompt, temperature=excluded.temperature, "
                "api_key=CASE WHEN excluded.api_key='' THEN ai_settings.api_key "
                "ELSE excluded.api_key END",
                (user["id"], base_url, api_key, model, sys_prompt, temp))
            db.commit()
            test_result = "已保存。密钥留空表示保持不变。"
    settings = _get_settings(db, user["id"])
    return render_template("ai/settings.html", s=settings,
                           test_result=test_result,
                           has_key=bool(settings["api_key"]))


def _pick_model(user):
    """从表单取模型标签并校验；非法则回退默认。"""
    opts, default = model_options(user, _personal_model(user))
    label = request.form.get("model") or ""
    if label not in dict(opts):
        label = default or ""
    return label


@bp.route("/new", methods=["POST"])
def new_conv():
    user = current_user()
    content = (request.form.get("content") or "").strip()
    if not content:
        return msg_page("提示", ["请先输入要发送的内容"], back="/ai/")
    if len(content) > 8000:
        content = content[:8000]
    model = _pick_model(user)
    db = get_db()
    title = content[:20].replace("\n", " ")
    if user:
        cur = db.execute("INSERT INTO conversations(user_id,guest_key,title,created_at,updated_at) "
                         "VALUES(?,?,?,?,?)", (user["id"], "", title, now_str(), now_str()))
    else:
        cur = db.execute("INSERT INTO conversations(user_id,guest_key,title,created_at,updated_at) "
                         "VALUES(0,?,?,?,?)", (guest_key(), title, now_str(), now_str()))
    cid = cur.lastrowid
    db.execute("INSERT INTO messages(conv_id,role,content,model,status,created_at) VALUES(?,?,?,?,?,?)",
               (cid, "user", content, model, "", now_str()))
    db.execute("INSERT INTO messages(conv_id,role,content,model,status,created_at,started_at) "
               "VALUES(?,?,?,?,?,?,?)",
               (cid, "assistant", "", model, "pending", now_str(), time.time()))
    db.commit()
    _start_generation(cid)
    return redirect("/ai/c/%d" % cid)


@bp.route("/c/<int:cid>")
def chat(cid):
    user = current_user()
    db = get_db()
    conv = _own_conv(db, cid, user)
    rows = db.execute("SELECT * FROM messages WHERE conv_id=? ORDER BY id", (cid,)).fetchall()
    now_ts = time.time()
    msgs = []
    for row in rows:
        m = dict(row)
        if m["status"] in ("pending", "streaming") and m.get("started_at"):
            m["elapsed"] = max(0, int(now_ts - m["started_at"]))
        else:
            m["elapsed"] = 0
        msgs.append(m)
    opts, default_model = model_options(user, _personal_model(user))
    auto_refresh = any(m["status"] in ("pending", "streaming") for m in msgs)
    gen_state = "done"
    for m in reversed(msgs):
        if m["role"] == "assistant":
            gen_state = m["status"] or "done"
            break
    return render_template("ai/chat.html", conv=conv, msgs=msgs,
                           opts=opts, default_model=default_model,
                           auto_refresh=auto_refresh, gen_state=gen_state)


@bp.route("/c/<int:cid>/send", methods=["POST"])
def send(cid):
    user = current_user()
    db = get_db()
    _own_conv(db, cid, user)
    content = (request.form.get("content") or "").strip()
    if not content:
        return redirect("/ai/c/%d" % cid)
    if len(content) > 8000:
        content = content[:8000]
    model = _pick_model(user)
    db.execute("INSERT INTO messages(conv_id,role,content,model,status,created_at) VALUES(?,?,?,?,?,?)",
               (cid, "user", content, model, "", now_str()))
    db.execute("INSERT INTO messages(conv_id,role,content,model,status,created_at,started_at) "
               "VALUES(?,?,?,?,?,?,?)",
               (cid, "assistant", "", model, "pending", now_str(), time.time()))
    db.commit()
    _start_generation(cid)
    return redirect("/ai/c/%d" % cid)


@bp.route("/c/<int:cid>/retry", methods=["POST"])
def retry(cid):
    user = current_user()
    db = get_db()
    _own_conv(db, cid, user)
    last = db.execute("SELECT * FROM messages WHERE conv_id=? ORDER BY id DESC LIMIT 1",
                      (cid,)).fetchone()
    if last and last["role"] == "assistant" and last["status"] in ("pending", "error", "done"):
        if last["status"] != "pending":
            model = last["model"]
            db.execute("DELETE FROM messages WHERE id=?", (last["id"],))
            db.execute("INSERT INTO messages(conv_id,role,content,model,status,created_at,started_at) "
                       "VALUES(?,?,?,?,?,?,?)",
                       (cid, "assistant", "", model, "pending", now_str(), time.time()))
            db.commit()
            _start_generation(cid)
    return redirect("/ai/c/%d" % cid)


@bp.route("/c/<int:cid>/delete", methods=["POST"])
def delete(cid):
    user = current_user()
    db = get_db()
    _own_conv(db, cid, user)
    db.execute("DELETE FROM messages WHERE conv_id=?", (cid,))
    db.execute("DELETE FROM conversations WHERE id=?", (cid,))
    db.commit()
    return redirect("/ai/")
