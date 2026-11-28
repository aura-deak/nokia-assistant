"""词典与搜索：走免费公开 API，无需密钥。
- 词典：有道 jsonapi（英汉/汉英双向，含音标、词性、释义）
- 翻译：Google gtx（自动识别源语言，中英文互译），失败回退 MyMemory
- 搜索：维基百科（返回相关词条摘要）
固定域名硬编码，不把用户输入拼进 URL 主机部分，无 SSRF 面。"""

import re
import requests
from urllib.parse import quote

from flask import Blueprint, render_template, request

from helpers import login_required, msg_page

bp = Blueprint("tools", __name__)

TIMEOUT = (6, 15)
UA = "Mozilla/5.0 (Linux; U; Android 4.1) AppleWebKit/534.30 (KHTML, like Gecko)"


def _clean_query(q, limit=60):
    """只允许常见字符（含中文），长度压到 limit，去掉控制字符。"""
    q = (q or "").strip()[:limit]
    return re.sub(r"[\x00-\x1f\x7f]", "", q)


def _flatten_i(items):
    """有道释义 i 字段是 str 与 {'#text':..} 的混合列表，拼成纯文本。"""
    parts = []
    for it in items or []:
        if isinstance(it, str):
            parts.append(it)
        elif isinstance(it, dict):
            parts.append(str(it.get("#text", "")))
    return "".join(parts).strip()


def _parse_dict(word):
    r = requests.get("https://dict.youdao.com/jsonapi", params={"q": word},
                     headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    out = {"word": (data.get("input") or word).strip(),
           "phonetic": "", "senses": [], "not_found": False}
    # ec = 英汉（英文词），ce = 汉英（中文词）
    node = None
    for key in ("ec", "ce"):
        w = (data.get(key) or {}).get("word")
        if w:
            node = w[0]
            break
    if node is None:
        out["not_found"] = True
        return out
    uk = (node.get("ukphone") or "").strip()
    us = (node.get("usphone") or "").strip()
    if uk and us:
        out["phonetic"] = "英 /%s/  美 /%s/" % (uk, us)
    elif uk or us:
        out["phonetic"] = "/%s/" % (uk or us)
    for tr in node.get("trs", [])[:8]:
        first = (tr.get("tr") or [{}])[0]
        text = _flatten_i(first.get("l", {}).get("i", []))
        if text:
            out["senses"].append(text)
    if not out["senses"]:
        out["not_found"] = True
    return out


def _has_cjk(text):
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _translate(text):
    """含中文译英文，否则译中文；gtx 为主，失败回退 MyMemory。"""
    to_en = _has_cjk(text)
    tl = "en" if to_en else "zh-CN"
    # 1) Google gtx（自动识别源语言）
    try:
        r = requests.get("https://translate.googleapis.com/translate_a/single",
                         params={"client": "gtx", "sl": "auto", "tl": tl,
                                 "dt": "t", "q": text},
                         headers={"User-Agent": UA}, timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            parts = [seg[0] for seg in (data[0] or []) if seg and seg[0]]
            if parts:
                return {"text": "".join(parts), "from": data[2] if len(data) > 2 else ""}
    except (requests.exceptions.RequestException, ValueError, IndexError):
        pass
    # 2) MyMemory 回退
    langpair = ("zh-CN|en" if to_en else "en|zh-CN")
    r = requests.get("https://api.mymemory.translated.net/get",
                     params={"q": text, "langpair": langpair},
                     headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    return {"text": data["responseData"]["translatedText"], "from": "mymemory"}


def _wiki_search(q):
    r = requests.get("https://zh.wikipedia.org/w/api.php",
                     params={"action": "query", "list": "search",
                             "srsearch": q, "srlimit": "6", "format": "json",
                             "utf8": "1"},
                     headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    hits = []
    for it in r.json().get("query", {}).get("search", []):
        title = it.get("title", "")
        snip = re.sub(r"<[^>]+>", "", it.get("snippet", ""))
        snip = snip.replace("&quot;", '"').replace("&amp;", "&")[:200]
        url = "https://zh.wikipedia.org/wiki/" + quote(title.replace(" ", "_"))
        hits.append({"title": title, "snip": snip,
                     "url": "/browse?url=" + quote(url, safe="")})
    return hits


@bp.route("/dict", methods=["GET", "POST"])
@login_required
def dictionary():
    word = _clean_query(request.values.get("q"), 60)
    trans_in = _clean_query(request.values.get("t"), 500)
    trans = None
    if trans_in:
        try:
            trans = _translate(trans_in)
        except (requests.exceptions.RequestException, ValueError, KeyError):
            trans = {"text": "翻译接口暂时不可用，请稍后再试。", "from": ""}
        trans["in"] = trans_in
    if not word:
        return render_template("tools/dict.html", result=None, word="", trans=trans)
    try:
        result = _parse_dict(word)
    except requests.exceptions.RequestException:
        return msg_page("查询失败", ["词典接口暂时不可用，请稍后再试。"], back="/dict")
    except ValueError:
        return msg_page("查询失败", ["词典接口返回异常，请稍后再试。"], back="/dict")
    if result is None:
        result = {"word": word, "phonetic": "", "senses": [],
                  "not_found": True}
    return render_template("tools/dict.html", result=result, word=word, trans=trans)


@bp.route("/search", methods=["GET", "POST"])
@login_required
def search():
    q = (request.values.get("q") or "").strip()[:100]
    if not q:
        return render_template("tools/search.html", hits=None, q="")
    try:
        hits = _wiki_search(q)
    except requests.exceptions.RequestException:
        return msg_page("搜索失败", ["搜索接口暂时不可用，请稍后再试。"], back="/search")
    except ValueError:
        return msg_page("搜索失败", ["搜索接口返回异常，请稍后再试。"], back="/search")
    return render_template("tools/search.html", hits=hits, q=q)
