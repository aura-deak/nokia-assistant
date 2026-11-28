"""网页辅助浏览：服务器代为抓取网页，剥掉 CSS/JS/图片/注释，
输出适合功能机的纯文本 + 链接，并分页以限制单页大小。"""

import bisect
import html
import ipaddress
import math
import re
import socket
import threading
import time
from urllib.parse import quote, urlencode, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
import ssrfguard
from bs4 import BeautifulSoup, Comment
from flask import Blueprint, render_template, request
from ssrfguard import Policy

from helpers import login_required, msg_page

bp = Blueprint("browse", __name__)

# 每页文本预算（字符数），保证单页 HTML 足够小
PAGE_BUDGET = 5000
# 最多抓取的字节数
FETCH_CAP = 3 * 1024 * 1024
UA = ("Mozilla/5.0 (Linux; U; Android 4.1) AppleWebKit/534.30 "
      "(KHTML, like Gecko) Version/4.0 Mobile Safari/534.30")

# 抓取结果缓存：token -> {url, title, segments, costs, prefix, total_pages, ts}
_CACHE = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 64
_CACHE_TTL = 1800

_BLOCK_TAGS = {
    "script", "style", "noscript", "template", "iframe", "frame", "frameset",
    "object", "embed", "applet", "svg", "img", "picture", "source", "video",
    "audio", "canvas", "map", "area", "datalist", "head",
    "meta", "link", "base", "dialog", "nav", "footer",
}

# 仅拦截明确的内网/本机地址段；198.18.0.0/15 为常见本地代理
# fake-ip 模式（如 Clash）使用的虚拟网段，予以放行。
# 198.18.0.0/15 为 Clash 等本地代理 fake-ip 网段，显式放行
_FAKE_IP_NET = ipaddress.ip_network("198.18.0.0/15")
_POLICY = Policy(allowed_networks=(_FAKE_IP_NET,),
                 allowed_ports=frozenset({80, 443, 8080}),
                 allow_proxy=True)   # 本机经 Clash 代理时由代理层解析
_GUARD_SESSION = None
_INLINE_KEEP = {"span", "b", "i", "em", "strong", "u", "s", "code", "small",
                "font", "big", "sub", "sup", "abbr", "cite", "q", "time",
                "mark", "var", "kbd", "tt"}

# robots.txt 缓存：origin -> (parsed, ts)，避免每次浏览都再拉一次
_ROBOTS_CACHE = {}
_ROBOTS_LOCK = threading.Lock()
_ROBOTS_TTL = 3600
_ROBOTS_UA = "NokiaAssistantProxy"


def _origin(url):
    p = urlparse(url)
    return "%s://%s" % (p.scheme, p.netloc)


def _fetch_robots(origin):
    """抓取并解析 robots.txt，失败/超时时返回 None（按宽松处理）。"""
    try:
        rp = RobotFileParser()
        # 走普通请求，ssrfguard 已在 _check_host 验过主机
        r = _http_get(origin + "/robots.txt",
                      timeout=(6, 12), allow_redirects=True,
                      headers={"User-Agent": _ROBOTS_UA + "/1.0"})
        if r.status_code == 200:
            rp.parse(r.text.splitlines())
            return rp
    except requests.exceptions.RequestException:
        pass
    except Exception:
        pass
    return None


def _allowed_by_robots(url):
    """检查目标 URL 是否被 robots.txt 允许；拿不到 robots.txt 视为允许。"""
    o = _origin(url)
    now = time.time()
    with _ROBOTS_LOCK:
        entry = _ROBOTS_CACHE.get(o)
        if entry and now - entry["ts"] < _ROBOTS_TTL:
            rp = entry["rp"]
        else:
            rp = None
    if rp is None:
        rp = _fetch_robots(o) or True  # None 表示宽松放行
        with _ROBOTS_LOCK:
            _ROBOTS_CACHE[o] = {"rp": rp, "ts": now}
            if len(_ROBOTS_CACHE) > 64:
                oldest = min(_ROBOTS_CACHE.items(), key=lambda kv: kv[1]["ts"])[0]
                _ROBOTS_CACHE.pop(oldest, None)
    if rp is True:
        return True, None
    if not rp.can_fetch(_ROBOTS_UA, url):
        return False, "目标网站的 robots.txt 禁止抓取该路径。"
    return True, None


def _check_host(url):
    """ssrfguard 策略检查：仅 http/https，拒绝内网地址与非常用端口。"""
    host = (urlparse(url).hostname or "").lower()
    if host in ("localhost", "localhost.localdomain") or host.endswith(".local"):
        return False
    try:
        _POLICY.check_url(url)
        return True
    except ssrfguard.SSRFGuardError:
        return False


def _http_get(url, **kw):
    """走 ssrfguard 的 Session：先解析并校验地址，再直连已验证的 IP，
    避免 DNS 重绑定攻击。"""
    global _GUARD_SESSION
    if _GUARD_SESSION is None:
        from ssrfguard.requests import Session as _SafeSession
        _GUARD_SESSION = _SafeSession(policy=_POLICY)
    return _GUARD_SESSION.get(url, **kw)


def _fetch(url):
    """抓取并限长。返回 (resp, error)。"""
    r = _http_get(url, timeout=(10, 30), stream=True,
                  headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"},
                  allow_redirects=True)
    chunks, size = [], 0
    for chunk in r.iter_content(chunk_size=65536):
        chunks.append(chunk)
        size += len(chunk)
        if size >= FETCH_CAP:
            break
    r.raw.close()
    body = b"".join(chunks)
    ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    # 编码自动识别与统一转码：HTTP 头声明 -> HTML meta 声明 -> 字符集探测，
    # 最终都转成 UTF-8 字符串
    enc = (r.encoding or "").strip()
    if enc in ("ISO-8859-1", "iso-8859-1", "latin-1", None, ""):
        # requests 拿不到 charset 时默认 latin-1，不可信，改为嗅探
        enc = ""
        head = body[:4096].decode("ascii", errors="ignore")
        m = re.search(r'charset\s*=\s*["\']?\s*([A-Za-z0-9_\-]+)', head, re.I)
        if m:
            enc = m.group(1)
    if not enc:
        try:
            from bs4 import UnicodeDammit
            enc = UnicodeDammit(body).original_encoding or "utf-8"
        except Exception:
            enc = "utf-8"
    try:
        text = body.decode(enc, errors="replace")
    except (LookupError, UnicodeDecodeError):
        text = body.decode("utf-8", errors="replace")
    return r, text, ctype


def _esc(s):
    return html.escape(str(s or ""), quote=True)


def _form_target(form_tag, base_url):
    """表单提交目标 = action 绝对地址 + hidden 字段；非 http(s) 返回 None。"""
    action = urljoin(base_url, form_tag.get("action") or base_url)
    if urlparse(action).scheme not in ("http", "https"):
        return None
    pairs = [(i.get("name"), i.get("value") or "")
             for i in form_tag.find_all("input")
             if (i.get("type") or "").lower() == "hidden" and i.get("name")]
    if pairs:
        action += ("&" if urlparse(action).query else "?") + urlencode(pairs)
    return action


def _render_form(form_tag, base_url):
    """把表单渲染成走代理的小型 GET 表单（保留输入框/下拉/按钮），
    提交时字段并入代理目标 URL。无可见输入控件返回空。"""
    target = _form_target(form_tag, base_url)
    if not target:
        return ""
    fields, nctl, submit_txt = [], 0, ""

    def want_submit(txt):
        nonlocal submit_txt
        if not submit_txt:
            submit_txt = (txt or "").strip()[:20] or "提交"

    for ctl in form_tag.find_all(["input", "select", "textarea", "button"]):
        if nctl >= 8:
            break
        typ = (ctl.get("type") or "").lower()
        name = ctl.get("name")
        if ctl.name == "input":
            if typ in ("hidden", "button", "reset", "file"):
                continue
            if typ in ("submit", "image"):
                want_submit(ctl.get("value") or "")
                continue
            if not name:
                continue
            if typ in ("checkbox", "radio"):
                fields.append('<input type="%s" name="%s" value="%s">'
                              % (typ, _esc(name), _esc(ctl.get("value") or "")))
            else:  # 文本类输入统一为 text
                fields.append('<input type="text" name="%s" value="%s" size="20">'
                              % (_esc(name), _esc(ctl.get("value") or "")))
            nctl += 1
        elif ctl.name == "select" and name:
            opts = []
            for opt in ctl.find_all("option")[:20]:
                ov = opt.get("value", opt.get_text(" ", strip=True))
                ot = (opt.get_text(" ", strip=True) or str(ov))[:30]
                sel = " selected" if opt.has_attr("selected") else ""
                opts.append('<option value="%s"%s>%s</option>'
                            % (_esc(str(ov)[:60]), sel, _esc(ot)))
            fields.append('<select name="%s">%s</select>' % (_esc(name), "".join(opts)))
            nctl += 1
        elif ctl.name == "textarea" and name:
            fields.append('<textarea name="%s" rows="2" cols="22"></textarea>'
                          % _esc(name))
            nctl += 1
        elif ctl.name == "button":
            if typ not in ("button", "reset"):
                want_submit(ctl.get_text(" ", strip=True))
    if nctl == 0:
        return ""
    fields.append('<input type="submit" value="%s">' % _esc(submit_txt))
    return ('<form method="get" action="/browse">'
            '<input type="hidden" name="url" value="%s">%s</form>'
            % (_esc(target), "".join(fields)))


def _clean_and_linearize(soup, base_url):
    """移除注释与无用标签，把 DOM 拉平成
    ('t', 文本) / ('l', 链接, 文本) / ('h', 标题) / ('n', 换行) 片段。"""
    title = ""
    if soup.title and soup.title.get_text(strip=True):
        title = soup.title.get_text(strip=True)[:80]
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    for name in _BLOCK_TAGS:
        for tag in soup.find_all(name):
            tag.decompose()

    segments = []

    def add_text(s):
        if s:
            segments.append(("t", s))

    def add_pre(node):
        add_text(node.get_text())
        segments.append(("n",))

    def handle_link(tag):
        href = (tag.get("href") or "").strip()
        text = tag.get_text(" ", strip=True)
        if not href or href.startswith("#"):
            return
        if href.startswith(("javascript:", "data:", "vbscript:")):
            return
        absu = urljoin(base_url, href)
        psch = urlparse(absu).scheme
        if psch not in ("http", "https", "mailto", "tel"):
            return
        if not text:
            text = absu[:60]
        segments.append(("l", absu, text[:100]))

    def walk(node):
        for child in node.children:
            if isinstance(child, Comment):
                continue
            if not getattr(child, "name", None):  # 文本节点
                add_text(str(child))
                continue
            name = child.name.lower()
            if name == "br" or name == "hr":
                segments.append(("n",))
            elif name == "a":
                handle_link(child)
            elif name == "form":
                fh = _render_form(child, base_url)
                if fh:
                    segments.append(("f", fh))
                segments.append(("n",))
                walk(child)
                segments.append(("n",))
            elif name in ("input", "select", "textarea"):
                # 表单内控件已在表单块中渲染；孤立输入框只保留其值
                if child.find_parent("form") is None:
                    v = child.get("value") or ""
                    if v and (child.get("type") or "").lower() != "hidden":
                        add_text(v)
            elif name == "button":
                # 表单内按钮已在表单块中；孤立按钮保留文字
                if child.find_parent("form") is None:
                    t = child.get_text(" ", strip=True)
                    if t:
                        add_text("[" + t[:30] + "]")
            elif name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                t = child.get_text(" ", strip=True)
                if t:
                    segments.append(("h", t[:120]))
                    segments.append(("n",))
            elif name == "li":
                segments.append(("t", "- "))
                walk(child)
                segments.append(("n",))
            elif name == "pre":
                segments.append(("n",))
                add_pre(child)
                segments.append(("n",))
            elif name in ("tr",):
                segments.append(("n",))
                walk(child)
            elif name in ("td", "th"):
                walk(child)
                segments.append(("t", " "))
            elif name in _INLINE_KEEP:
                walk(child)
            else:  # 其余块级/未知标签
                segments.append(("n",))
                walk(child)
                segments.append(("n",))

    root = soup.body if soup.body else soup
    walk(root)
    return title, segments


def _seg_cost(seg):
    if seg[0] == "n":
        return 1
    if seg[0] == "t":
        return len(seg[1])
    if seg[0] == "h":
        return len(seg[1]) + 8
    if seg[0] == "f":
        return len(seg[1]) + 16
    return len(seg[2]) + 32  # 链接


def _store_cache(url, title, segments):
    costs = [_seg_cost(s) for s in segments]
    prefix, total = [], 0
    for c in costs:
        total += c
        prefix.append(total)
    total_pages = max(1, math.ceil(total / PAGE_BUDGET))
    token = "f" + format(int(time.time() * 1000), "x")[-10:]
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            oldest = min(_CACHE.items(), key=lambda kv: kv[1]["ts"])[0]
            _CACHE.pop(oldest, None)
        _CACHE[token] = {"url": url, "title": title, "segments": segments,
                         "prefix": prefix, "total": total,
                         "total_pages": total_pages, "ts": time.time()}
    return token


def _page_of(entry, page):
    """按预算切出第 page 页片段。"""
    segments = entry["segments"]
    prefix = entry["prefix"]
    total_pages = entry["total_pages"]
    start_cost = (page - 1) * PAGE_BUDGET
    # 本页应从"累计成本已超过 (page-1)*预算"的那一段开始；
    # 恰好在边界上的一段属于前一页，这里取 bisect_right。
    i = bisect.bisect_right(prefix, start_cost)
    out = []
    for idx in range(i, len(segments)):
        out.append(segments[idx])
        if prefix[idx] >= page * PAGE_BUDGET and page < total_pages:
            break
    return out


def _segments_to_html(segments, entry, page):
    """把片段渲染为 HTML 字符串（全部转义）。"""
    parts = []
    last_nl = True
    for seg in segments:
        kind = seg[0]
        if kind == "n":
            if not last_nl:
                parts.append("<br>")
                last_nl = True
        elif kind == "t":
            t = html.escape(seg[1], quote=False)
            if t.strip():
                parts.append(t)
                last_nl = False
        elif kind == "h":
            parts.append("<b>%s</b><br>" % html.escape(seg[1], quote=False))
            last_nl = True
        elif kind == "l":
            href, text = seg[1], seg[2]
            if urlparse(href).scheme in ("http", "https"):
                target = "/browse?url=" + quote(href, safe="")
            else:
                target = href
            parts.append('[<a href="%s">%s</a>]' % (html.escape(target, quote=True),
                                                    html.escape(text, quote=False)))
            last_nl = False
        elif kind == "f":
            parts.append(seg[1])
            last_nl = False
    return "".join(parts)


def _normalize_url(u):
    u = (u or "").strip()
    if not u:
        return None
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", u):
        u = "http://" + u
    return u


@bp.route("/browse")
@login_required
def browse_page():
    url = _normalize_url(request.args.get("url"))
    if not url:
        return render_template("browse/index.html")
    page = max(1, int(request.args.get("page", 1) or 1))
    token = request.args.get("t", "")

    entry = None
    if token:
        with _CACHE_LOCK:
            entry = _CACHE.get(token)
            if entry and time.time() - entry["ts"] > _CACHE_TTL:
                _CACHE.pop(token, None)
                entry = None
    if entry is None:
        # 重新抓取（token 失效或首次访问）
        if not _check_host(url):
            return msg_page("无法浏览", ["目标地址不可访问（仅支持公网 http/https 地址）"], back="/browse")
        ok, why = _allowed_by_robots(url)
        if not ok:
            return msg_page("被 robots.txt 拒绝",
                            [why or "", "出于对网站规则的尊重，不抓取该页面。",
                             '<a href="/browse">返回</a>'], back="/browse")
        # 表单代理提交：除 url/t/page 外的 GET 参数并入目标地址
        extra = [(k, v) for k, v in request.args.items()
                 if k not in ("url", "t", "page") and v]
        if extra:
            url = url + ("&" if urlparse(url).query else "?") + urlencode(extra)
        try:
            r, text, ctype = _fetch(url)
        except requests.exceptions.Timeout:
            return msg_page("抓取超时", ["目标网站响应太慢，请稍后重试。", '<a href="/browse?url=%s">重试</a>' % quote(url, safe="")], back="/browse")
        except requests.exceptions.RequestException as e:
            return msg_page("抓取失败", ["原因: " + str(e)[:200]], back="/browse")
        if r.status_code >= 400:
            return msg_page("目标站返回错误", ["HTTP 状态码: %d" % r.status_code,
                             '<a href="/browse?url=%s">重试</a>' % quote(url, safe="")],
                            back="/browse")
        final_url = r.url
        if "html" in ctype or "xml" in ctype or ctype == "":
            soup = BeautifulSoup(text, "html.parser")
            title, segments = _clean_and_linearize(soup, final_url)
        elif ctype.startswith("text/"):
            title = final_url
            segments = []
            for line in text.splitlines():
                segments.append(("t", line))
                segments.append(("n",))
        else:
            return msg_page("暂不支持该类型",
                            ["内容类型: %s，仅支持网页与纯文本。" % (ctype or "未知")],
                            back="/browse")
        if not title:
            title = final_url[:80]
        token = _store_cache(final_url, title, segments)
        with _CACHE_LOCK:
            entry = _CACHE[token]

    total_pages = entry["total_pages"]
    if page > total_pages:
        page = total_pages
    segs = _page_of(entry, page)
    body_html = _segments_to_html(segs, entry, page)

    def purl(p):
        return "/browse?url=%s&t=%s&page=%d" % (quote(entry["url"], safe=""), token, p)

    return render_template("browse/page.html", title=entry["title"],
                           url=entry["url"], body_html=body_html,
                           page=page, total_pages=total_pages,
                           prev_url=(purl(page - 1) if page > 1 else None),
                           next_url=(purl(page + 1) if page < total_pages else None))


@bp.route("/browse/search")
@login_required
def browse_search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return render_template("browse/index.html")
    # 主流引擎的 robots.txt 都禁搜索路径；wiby.me 对通用 UA 开放 /?q= 搜索，
    # 国内可直连，且本身是轻量老网页风格，适合功能机浏览。
    url = "https://wiby.me/?q=" + quote(q, safe="")
    return _redirect_proxy(url)


def _redirect_proxy(url):
    # 直接在服务端把搜索 URL 送入抓取（少一次跳转，功能机更快）
    from flask import redirect
    return redirect("/browse?url=" + quote(url, safe=""))
