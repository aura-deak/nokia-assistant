"""云小说：上传 TXT，服务端检测章节；登录看自己的书架，
任何人凭分享链接（/n/随机码）即可阅读，页面按字符数分页保持小巧。"""

import json
import os
import re
import secrets
import threading

from flask import (Blueprint, abort, redirect, render_template, request)

from db import get_db, NOVEL_DIR
from helpers import current_user, is_privileged, login_required, msg_page, now_str

bp = Blueprint("novel", __name__)

MAX_NOVEL_BYTES = 15 * 1024 * 1024
PAGE_CHARS = 500           # 阅读页每页字符数（硬切）
CHAPTERS_PER_PAGE = 40     # 目录每页章节数
QUOTA_BYTES = 2 * 1024 * 1024   # 普通用户书库总容量；特权用户无上限

# 文本内存缓存：novel_id -> 全文（限制数量防止占满内存）
_TEXT_CACHE = {}
_TEXT_CACHE_LOCK = threading.Lock()
_TEXT_CACHE_MAX = 6

_CH_RE = re.compile(
    r"^\s*(第\s*[0-9０-９一二三四五六七八九十百千万零〇两]+\s*[章节回卷集部篇]\s*\S.{0,40}"
    r"|序章\s*\S.{0,30}|楔子\s*\S.{0,30}|引子\s*\S.{0,30}"
    r"|尾声\s*\S.{0,30}|后记\s*\S.{0,30}|番外\s*\S.{0,30})\s*$")


def _used_bytes(db, uid):
    row = db.execute("SELECT COALESCE(SUM(size),0) AS s FROM novels WHERE owner_id=?",
                     (uid,)).fetchone()
    return row["s"]


def _decode_text(data):
    """尝试常见编码解码 TXT。"""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, ValueError):
            pass
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return data.decode("utf-16")
        except (UnicodeDecodeError, ValueError):
            pass
    return data.decode("utf-8", errors="replace")


def _detect_chapters(text):
    """返回 [{'t': 标题, 'o': 字符偏移}]；不足两章视为无章节。"""
    found = []
    offset = 0
    for line in text.split("\n"):
        stripped = line.rstrip()
        if 2 <= len(stripped.strip()) <= 50 and _CH_RE.match(stripped):
            found.append({"t": stripped.strip()[:50], "o": offset})
        offset += len(line) + 1
    if len(found) < 2:
        return []
    return found


def _get_text(novel_id):
    with _TEXT_CACHE_LOCK:
        t = _TEXT_CACHE.get(novel_id)
        if t is not None:
            return t
    path = os.path.join(NOVEL_DIR, "%d.txt" % novel_id)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        text = _decode_text(f.read()).replace("\r\n", "\n").replace("\r", "\n")
    with _TEXT_CACHE_LOCK:
        if len(_TEXT_CACHE) >= _TEXT_CACHE_MAX:
            _TEXT_CACHE.pop(next(iter(_TEXT_CACHE)))
        _TEXT_CACHE[novel_id] = text
    return text


def _quota_view(user):
    """返回 (是否特权, 已用字节, 上限字节或None)，供页面展示与上传校验。"""
    db = get_db()
    privileged = is_privileged(user["username"])
    used = _used_bytes(db, user["id"])
    return privileged, used, (None if privileged else QUOTA_BYTES)


def _drop_cache(novel_id):
    with _TEXT_CACHE_LOCK:
        _TEXT_CACHE.pop(novel_id, None)


def _get_novel(code):
    row = get_db().execute("SELECT * FROM novels WHERE code=?", (code,)).fetchone()
    if row is None:
        abort(404)
    return row


def _split_pages(text):
    """直接按固定字数硬切分页，保证功能机上每页足够小。"""
    text = text or ""
    return [text[i:i + PAGE_CHARS] for i in range(0, len(text), PAGE_CHARS)] or [""]


def _chapter_slice(text, chapters, ci):
    if not chapters:
        return text
    start = chapters[ci]["o"]
    end = chapters[ci + 1]["o"] if ci + 1 < len(chapters) else len(text)
    return text[start:end]


@bp.route("/novel")
def shelf():
    user = current_user()
    if not user:
        return render_template("novel/index.html", novels=None)
    privileged, used, quota = _quota_view(user)
    rows = get_db().execute(
        "SELECT * FROM novels WHERE owner_id=? ORDER BY id DESC",
        (user["id"],)).fetchall()
    novels = []
    for row in rows:
        n = dict(row)
        n["nchapters"] = len(json.loads(n["chapters"] or "[]"))
        novels.append(n)
    return render_template("novel/index.html", novels=novels,
                           privileged=privileged, used_kb=used // 1024,
                           quota_kb=quota // 1024 if quota else 0)


@bp.route("/novel/upload", methods=["GET", "POST"])
@login_required
def upload():
    user = current_user()
    privileged, used, quota = _quota_view(user)
    if request.method == "GET":
        return render_template("novel/upload.html", privileged=privileged,
                               used_kb=used // 1024,
                               quota_kb=quota // 1024 if quota else 0)
    title = (request.form.get("title") or "").strip()[:60]
    data = None
    f = request.files.get("novel_file")
    if f and f.filename:
        data = f.read(MAX_NOVEL_BYTES + 1)
        if len(data) > MAX_NOVEL_BYTES:
            return msg_page("文件过大", ["小说文件不能超过 15MB"], back="/novel/upload")
        if not title:
            base = os.path.basename(f.filename)
            title = os.path.splitext(base)[0][:60]
    if not data:
        pasted = (request.form.get("content") or "").strip()
        if not pasted:
            return msg_page("提示", ["请选择 TXT 文件或直接粘贴文本"], back="/novel/upload")
        data = pasted.encode("utf-8")
        if len(data) > MAX_NOVEL_BYTES:
            return msg_page("内容过大", ["文本不能超过 15MB"], back="/novel/upload")
    if not title:
        title = "未命名小说"
    if not privileged and used + len(data) > quota:
        return msg_page("超出书库容量",
                        ["普通用户书库总容量为 2MB（2048KB）。",
                         "当前已用 %dKB，本次上传还需 %dKB。" % (used // 1024, len(data) // 1024),
                         "请先在书架删除部分小说，或联系管理员把你加入特权用户。"],
                        back="/novel")
    text = _decode_text(data).replace("\r\n", "\n").replace("\r", "\n").strip("\ufeff")
    chapters = _detect_chapters(text)
    db = get_db()
    code = secrets.token_hex(6)
    cur = db.execute(
        "INSERT INTO novels(owner_id,code,title,size,char_count,chapters,views,created_at) "
        "VALUES(?,?,?,?,?,?,0,?)",
        (user["id"], code, title, len(data), len(text), json.dumps(chapters, ensure_ascii=False),
         now_str()))
    nid = cur.lastrowid
    path = os.path.join(NOVEL_DIR, "%d.txt" % nid)
    try:
        with open(path, "wb") as out:
            out.write(text.encode("utf-8"))
        db.commit()
    except OSError:
        db.execute("DELETE FROM novels WHERE id=?", (nid,))
        db.commit()
        return msg_page("保存失败", ["服务器写入文件失败，请重试"], back="/novel/upload")
    return redirect("/n/%s" % code)


@bp.route("/n/<code>")
def view(code):
    novel = _get_novel(code)
    db = get_db()
    db.execute("UPDATE novels SET views=views+1 WHERE id=?", (novel["id"],))
    db.commit()
    owner = db.execute("SELECT username FROM users WHERE id=?",
                       (novel["owner_id"],)).fetchone()
    chapters = json.loads(novel["chapters"] or "[]")
    user = current_user()
    is_owner = bool(user and user["id"] == novel["owner_id"])
    return render_template("novel/view.html", novel=novel,
                           owner_name=(owner["username"] if owner else "未知"),
                           nchapters=len(chapters), is_owner=is_owner)


@bp.route("/n/<code>/chapters")
def chapters(code):
    novel = _get_novel(code)
    chapters = json.loads(novel["chapters"] or "[]")
    if not chapters:
        return redirect("/n/%s/read?c=0&p=1" % code)
    try:
        cp = max(1, int(request.args.get("cp", 1)))
    except ValueError:
        cp = 1
    total = len(chapters)
    pages = (total + CHAPTERS_PER_PAGE - 1) // CHAPTERS_PER_PAGE
    cp = min(cp, pages)
    part = chapters[(cp - 1) * CHAPTERS_PER_PAGE: cp * CHAPTERS_PER_PAGE]
    return render_template("novel/chapters.html", novel=novel, part=part,
                           cp=cp, pages=pages)


@bp.route("/n/<code>/read")
def read(code):
    novel = _get_novel(code)
    text = _get_text(novel["id"])
    if text is None:
        return msg_page("错误", ["小说文件丢失，请联系上传者"], back="/n/%s" % code)
    chapters = json.loads(novel["chapters"] or "[]")
    if not chapters:
        chapters = [{"t": "全文", "o": 0}]
    try:
        ci = max(0, int(request.args.get("c", 0)))
        pi = max(1, int(request.args.get("p", 1)))
    except ValueError:
        ci, pi = 0, 1
    ci = min(ci, len(chapters) - 1)
    pages = _split_pages(_chapter_slice(text, chapters, ci))
    pi = min(pi, len(pages))
    return render_template("novel/read.html", novel=novel,
                           chapter=chapters[ci], ci=ci, nchapters=len(chapters),
                           content=pages[pi - 1], page=pi, pages=len(pages),
                           has_chapters=(novel["chapters"] != "[]"))


@bp.route("/novel/<int:nid>/delete", methods=["GET", "POST"])
@login_required
def delete(nid):
    user = current_user()
    db = get_db()
    novel = db.execute("SELECT * FROM novels WHERE id=? AND owner_id=?",
                       (nid, user["id"])).fetchone()
    if novel is None:
        abort(404)
    if request.method == "GET":
        return render_template("novel/confirm_delete.html", novel=novel)
    db.execute("DELETE FROM novels WHERE id=?", (nid,))
    db.commit()
    _drop_cache(nid)
    try:
        os.remove(os.path.join(NOVEL_DIR, "%d.txt" % nid))
    except OSError:
        pass
    return redirect("/novel")
