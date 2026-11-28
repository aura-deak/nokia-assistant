"""词典与搜索：走免费公开 API，无需密钥。
- 词典：dictionaryapi.dev（英英，含音标、词性、释义、例句）
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


def _text(word):
    """只允许常见词条字符，长度压到 60。"""
    word = (word or "").strip()[:60]
    return re.sub(r"[^A-Za-z0-9'\- ]", " ", word).strip()


def _parse_dict(word):
    r = requests.get("https://api.dictionaryapi.dev/api/v2/entries/en/" + word,
                     headers={"User-Agent": UA}, timeout=TIMEOUT)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list) or not data:
        return None
    entry = data[0]
    out = {"word": entry.get("word", word),
           "phonetic": (entry.get("phonetic") or "").strip(),
           "senses": []}
    for meaning in entry.get("meanings", [])[:4]:
        pos = meaning.get("partOfSpeech", "")
        for d in meaning.get("definitions", [])[:2]:
            sense = {"pos": pos, "def": d.get("definition", "")}
            example = (d.get("example") or "").strip()
            if example:
                sense["example"] = example
            out["senses"].append(sense)
            if len(out["senses"]) >= 6:
                break
    return out if out["senses"] else None


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
    word = _text(request.values.get("q") or "")
    if not word:
        return render_template("tools/dict.html", result=None, word="")
    try:
        result = _parse_dict(word)
    except requests.exceptions.RequestException:
        return msg_page("查询失败", ["词典接口暂时不可用，请稍后再试。"], back="/dict")
    except ValueError:
        return msg_page("查询失败", ["词典接口返回异常，请稍后再试。"], back="/dict")
    if result is None:
        result = {"word": word, "phonetic": "", "senses": [],
                  "not_found": True}
    return render_template("tools/dict.html", result=result, word=word)


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
