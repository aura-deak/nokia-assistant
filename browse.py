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
from urllib.parse import urljoin, quote, urlparse

import requests
from bs4 import BeautifulSoup, Comment
from flask import Blueprint, render_template, request

from helpers import msg_page

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
    "audio", "canvas", "map", "area", "form", "input", "button", "select",
    "option", "textarea", "label", "fieldset", "legend", "datalist", "head",
    "meta", "link", "base", "dialog", "nav", "footer",
}

# 仅拦截明确的内网/本机地址段；198.18.0.0/15 为常见本地代理
# fake-ip 模式（如 Clash）使用的虚拟网段，予以放行。
_BLOCKED_NETS = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
    "169.254.0.0/16", "172.16.0.0/12", "192.168.0.0/16",
    "::/128", "::1/128", "fc00::/7", "fe80::/10",
)]
_FAKE_IP_NET = ipaddress.ip_network("198.18.0.0/15")
_INLINE_KEEP = {"span", "b", "i", "em", "strong", "u", "s", "code", "small",
                "font", "big", "sub", "sup", "abbr", "cite", "q", "time",
                "mark", "var", "kbd", "tt"}


def _check_host(url):
    """基本 SSRF 防护：仅 http/https，拒绝内网地址。"""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        return False
    host = p.hostname or ""
    if host in ("localhost", "localhost.localdomain") or host.endswith(".local"):
        return False
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except OSError:
        return True  # 解析失败交给 requests 报错
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if addr in _FAKE_IP_NET:
            continue
        if any(addr in net for net in _BLOCKED_NETS):
            return False
    return True


def _fetch(url):
    """抓取并限长。返回 (resp, error)。"""
    r = requests.get(url, timeout=(10, 30), stream=True,
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
    # 编码：HTTP 头 -> 页面声明由 bs4 处理；兜底用字符集检测
    if r.encoding is None:
        try:
            r.encoding = r.apparent_encoding or "utf-8"
        except Exception:
            r.encoding = "utf-8"
    text = body.decode(r.encoding or "utf-8", errors="replace")
    return r, text, ctype


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
    return "".join(parts)


def _normalize_url(u):
    u = (u or "").strip()
    if not u:
        return None
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", u):
        u = "http://" + u
    return u


@bp.route("/browse")
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
def browse_search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return render_template("browse/index.html")
    url = "https://cn.bing.com/search?q=" + quote(q, safe="")
    return _redirect_proxy(url)


def _redirect_proxy(url):
    # 直接在服务端把搜索 URL 送入抓取（少一次跳转，功能机更快）
    from flask import redirect
    return redirect("/browse?url=" + quote(url, safe=""))
