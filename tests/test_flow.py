"""端到端测试：注册/登录、网页代理、AI 对话、云小说。"""

import re
import sys
import time
import random
import json
import os

import requests

BASE = "http://127.0.0.1:8000"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("PASS", name)
    else:
        fail += 1
        print("FAIL", name, extra[:300])


def size_kb(text):
    return round(len(text.encode("utf-8")) / 1024, 1)


SUFFIX = str(random.randint(1000, 9999))
s = requests.Session()

# 测试配置：固定验证码、放宽限流（避免测试流量触发），特权名单空
CFG_PATH = os.path.join(BASE_DIR, "config.json")
_orig_cfg = open(CFG_PATH, encoding="utf-8").read() if os.path.exists(CFG_PATH) else ""
CAPTCHA_FIXED = "1234"

def write_cfg(**kw):
    cfg = {"privileged_users": [], "captcha_fixed": CAPTCHA_FIXED,
           "rate_limit": {"ip_per_minute": 5000, "user_per_minute": 5000}}
    cfg.update(kw)
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)

write_cfg()

# ---- 1. 注册与登录 ----
s.get(BASE + "/captcha.png")
r = s.post(BASE + "/register", data={"username": "测试用户" + SUFFIX, "password": "pass1234",
                                     "captcha": CAPTCHA_FIXED},
           allow_redirects=False)
check("注册重定向", r.status_code == 302, r.text)
r = s.get(BASE + "/")
check("主页显示登录用户", "测试用户" + SUFFIX in r.text, r.text)

r2 = requests.get(BASE + "/logout", cookies=dict(s.cookies), allow_redirects=False)
r3 = requests.get(BASE + "/")
check("登出后未登录", "测试用户" not in r3.text)
UNAME = "测试用户" + SUFFIX
lt = requests.Session()
lt.get(BASE + "/captcha.png")
r = lt.post(BASE + "/login",
            data={"username": UNAME, "password": "pass1234", "next": "/",
                  "captcha": CAPTCHA_FIXED},
            allow_redirects=False)
check("登录重定向", r.status_code == 302)

# 错误密码
lt.get(BASE + "/captcha.png")
rbad = lt.post(BASE + "/login", data={"username": UNAME, "password": "wrong",
                                      "captcha": CAPTCHA_FIXED})
check("错误密码被拒绝", "用户名或密码错误" in rbad.text)

# ---- 1b. 验证码 / 安全配置 ----
r = requests.get(BASE + "/captcha.png")
check("验证码为PNG图片", r.status_code == 200
      and r.headers.get("Content-Type") == "image/png"
      and r.content[:8] == b"\x89PNG\r\n\x1a\n", r.headers.get("Content-Type"))
r = requests.post(BASE + "/register", data={"username": "nocap" + SUFFIX, "password": "pass1234"},
                  allow_redirects=False)
check("注册缺验证码被拒绝", r.status_code == 200 and "验证码" in r.text, r.text[:200])
r = requests.post(BASE + "/login", data={"username": UNAME, "password": "pass1234",
                                         "captcha": "zzzz"})
check("错误验证码被拒绝", "验证码" in r.text, r.text[:200])
ck = requests.Session()
ck.get(BASE + "/captcha.png")
r = ck.post(BASE + "/register", data={"username": "capuser" + SUFFIX, "password": "pass1234",
                                      "captcha": CAPTCHA_FIXED}, allow_redirects=False)
check("正确验证码注册成功", r.status_code == 302, r.text[:200])
r = ck.post(BASE + "/register", data={"username": "capuser2" + SUFFIX, "password": "pass1234",
                                      "captcha": CAPTCHA_FIXED}, allow_redirects=False)
check("验证码一次性", r.status_code == 200 and "验证码" in r.text, r.text[:200])
lk = requests.Session()
lk.get(BASE + "/captcha.png")
r = lk.post(BASE + "/login", data={"username": UNAME, "password": "pass1234",
                                   "captcha": CAPTCHA_FIXED}, allow_redirects=False)
check("正确验证码登录成功", r.status_code == 302, str(r.status_code))
sc = r.headers.get("Set-Cookie", "")
check("会话Cookie有效期30天", "Expires=" in sc, sc[:120])
keyfile = os.path.join(BASE_DIR, "secret.key")
kval = open(keyfile, encoding="utf-8").read().strip()
check("SECRET_KEY为32字节随机(64位hex)", bool(re.fullmatch(r"[0-9a-f]{64}", kval)),
      kval[:8] + "...")
check("secret.key权限600", (os.stat(keyfile).st_mode & 0o777) == 0o600,
      oct(os.stat(keyfile).st_mode & 0o777))

# ---- 2. 网页代理（线性化单元测试） ----
sys.path.insert(0, BASE_DIR)
from bs4 import BeautifulSoup
from browse import _clean_and_linearize, _normalize_url, _check_host

r = requests.get(BASE + "/browse?url=https://example.com", allow_redirects=False)
check("未登录浏览跳转登录", r.status_code == 302 and "/login" in r.headers.get("Location", ""),
      str(r.status_code))

html_doc = """<!DOCTYPE html><html><head><title>测试页</title>
<link rel="stylesheet" href="x.css"><style>body{color:red}</style></head>
<body><!-- 这是一个注释 -->
<script>alert(1)</script><nav><a href="/top">导航项</a></nav>
<p>第一段 <a href="sub/page.html">相对链接</a> 与 <a href="javascript:x()">坏链接</a></p>
<img src="big.jpg"><h2>小标题</h2><ul><li>条目一</li><li>条目二</li></ul>
<form action="/search"><input type="hidden" name="hl" value="zh">
<input type="text" name="q"><select name="ct"><option value="1">网页</option></select>
<button>搜索一下</button></form>
<button onclick="x()">独立按钮</button>
<table><tr><td>甲</td><td>乙</td></tr></table>
</body></html>"""
title, segs = _clean_and_linearize(BeautifulSoup(html_doc, "html.parser"),
                                   "http://example.com/dir/")
flat = repr(segs)
check("标题提取", title == "测试页", title)
check("注释已去除", "这是一个注释" not in flat, flat)
check("脚本已去除", "alert" not in flat, flat)
check("样式已去除", "color:red" not in flat, flat)
check("图片已去除", "big.jpg" not in flat, flat)
check("表单保留并走代理", 'action="/browse"' in flat and 'name="q"' in flat, flat)
check("表单hidden并入目标URL", "hl=zh" in flat, flat)
check("下拉框保留", "<select" in flat and "网页" in flat, flat)
check("提交按钮保留", 'type="submit"' in flat and "搜索一下" in flat, flat)
check("孤立按钮文字保留", "[独立按钮]" in flat, flat)
check("表单控件不重复渲染", flat.count('name="q"') == 1, flat)
check("导航已去除", "导航项" not in flat, flat)
check("相对链接转绝对", ("l", "http://example.com/dir/sub/page.html", "相对链接") in segs, flat)
check("javascript链接被丢弃", "javascript:x()" not in flat, flat)
check("列表保留", "条目一" in flat and "条目二" in flat, flat)
check("表格文字保留", "甲" in flat and "乙" in flat, flat)
check("URL补全协议", _normalize_url("example.com") == "http://example.com")
check("SSRF拦截本机", not _check_host("http://127.0.0.1:8080/admin")
      and not _check_host("http://192.168.1.1/")
      and not _check_host("http://localhost/")
      and not _check_host("ftp://example.com"))

# 代理分页单元测试
from browse import _store_cache, _CACHE, _page_of, _segments_to_html
paras = ["段落%03d " % i + "内容文字" * 40 for i in range(60)]
segs = []
for p in paras:
    segs.append(("t", p))
    segs.append(("n",))
token = _store_cache("http://x.example/", "分页测试", segs)
entry = _CACHE[token]
check("代理分页多于1页", entry["total_pages"] > 1, str(entry["total_pages"]))
joined = ""
seen_pages = []
for pg in range(1, entry["total_pages"] + 1):
    page_html = _segments_to_html(_page_of(entry, pg), entry, pg)
    seen_pages.append(page_html)
    joined += page_html
check("分页拼接覆盖全部段落", all(p in joined for p in paras), "")
first = re.search(r"段落(\d+)", seen_pages[0]).group(1)
check("分页顺序正确(首页段000,末页含段059)",
      first == "000" and "段落059" in seen_pages[-1], "first=" + first)

# 小说分页单元测试
from novels import _split_pages, PAGE_CHARS
ntext = "\n".join("第%03d段 " % i + "正文文字" * 40 for i in range(100))
npages = _split_pages(ntext)
check("小说分页多于1页", len(npages) > 1, str(len(npages)))
check("小说每页不超500字", all(len(p) <= PAGE_CHARS for p in npages),
      max(len(p) for p in npages))
check("小说分页拼接还原原文", "".join(npages) == ntext, "")

# ---- 3. 网页代理（在线） ----
r = s.get(BASE + "/browse", params={"url": "https://example.com"})
check("代理example.com成功", "Example Domain" in r.text and "example" in r.text.lower(), r.text)
check("代理页面无脚本残留", "<script" not in r.text, r.text[:2000])
check("代理页面无样式残留", "style=" not in r.text and "<style" not in r.text, r.text)
check("代理页面无img", "<img" not in r.text, r.text)
check("代理页面小(%sKB)" % size_kb(r.text), size_kb(r.text) < 10, "")
check("响应禁缓存", r.headers.get("Cache-Control") == "no-store, max-age=0",
      str(r.headers.get("Cache-Control")))
links = re.findall(r'href="(/browse\?url=[^"]+)"', r.text)
check("包含原文链接或代理链接", len(links) >= 1 or "原文" in r.text, r.text)

# 有外链的页面（example.com 底部有 iana.org 链接）
if "iana.org" in r.text:
    links = re.findall(r'href="(/browse\?url=[^"]+)"', r.text)
    check("外链已重写为代理链接", any("iana.org" in u for u in links), r.text)
    check("链接文字保留", "More information" in r.text, r.text)
else:
    print("SKIP example.com 外链测试（网络不可达或页面变更），已由单元测试覆盖")

# 无效地址
r = s.get(BASE + "/browse", params={"url": "http://127.0.0.1:1/x"})
check("内网地址被拦截", "不可访问" in r.text, r.text)
r = s.get(BASE + "/browse", params={"url": "http://no-such-host.invalid/x"})
check("不存在域名报错", ("抓取失败" in r.text or "不可访问" in r.text or "返回错误" in r.text), r.text)

# 表单代理集成测试：起一个带表单的本地站点，经 /browse 提交并看到结果
import threading as _th
from http.server import BaseHTTPRequestHandler, HTTPServer

class _Site(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def do_GET(self):
        if self.path.startswith("/search"):
            import urllib.parse as _up
            q = _up.parse_qs(_up.urlparse(self.path).query).get("q", [""])[0]
            body = ("<html><title>搜索</title><body>你的查询是：%s"
                    "<a href='/'>回首页</a></body></html>" % q).encode()
        else:
            body = ("<html><title>主页</title><body><form action='/search'>"
                    "<input type='hidden' name='hl' value='zh'>"
                    "<input type='text' name='q'><button>搜</button></form>"
                    "</body></html>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

_site_srv = HTTPServer(("127.0.0.1", 5802), _Site)
_th.Thread(target=_site_srv.serve_forever, daemon=True).start()
time.sleep(0.4)
r = s.get(BASE + "/browse", params={"url": "http://127.0.0.1:5802/"})
check("本地站点被SSRF拦截", "不可访问" in r.text, r.text[:200])
import browse as _browse
from urllib.parse import quote as _uq
import app as _appmod
_tc = _appmod.app.test_client()
_orig_check = _browse._check_host
_orig_get = _browse._http_get
_browse._check_host = lambda u: True    # 进程内放行 5802（dev 服务进程不受影响）
_browse._http_get = requests.get        # 绕过 ssrfguard 直连本地 mock
try:
    _tc.get("/captcha.png")
    _tc.post("/login", data={"username": UNAME, "password": "pass1234",
                             "captcha": CAPTCHA_FIXED})
    # 表单首页：搜索框、按钮保留
    r2 = _tc.get("/browse?url=" + _uq("http://127.0.0.1:5802/", safe=""))
    check("代理页保留搜索框", 'name="q"' in r2.text, r2.text[:400])
    check("代理页保留提交按钮", 'type="submit"' in r2.text and "搜" in r2.text, r2.text[:400])
    check("代理表单action走代理", 'action="/browse"' in r2.text, r2.text[:400])
    check("hidden已并入代理目标", "hl%3Dzh" in r2.text or "hl=zh" in r2.text, r2.text[:400])
    # 表单提交：字段并入目标 URL，代理抓到结果页
    r2 = _tc.get("/browse?url=" + _uq("http://127.0.0.1:5802/search?q=功能机", safe=""))
    check("表单参数并入目标URL完成代理", "你的查询是：功能机" in r2.text, r2.text[:400])
finally:
    _browse._check_host = _orig_check
    _browse._http_get = _orig_get
    _site_srv.shutdown()

# ---- 4. AI 对话 ----
KEYS_PATH = os.path.join(BASE_DIR, "data", "api_keys.json")
CFG_PATH = os.path.join(BASE_DIR, "config.json")
orig_keys = open(KEYS_PATH, encoding="utf-8").read()
orig_cfg = open(CFG_PATH, encoding="utf-8").read()
try:
    # 临时换用测试密钥文件（免费组与 magpie 都指向本地 mock）
    with open(KEYS_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "free_groups": [{"name": "MockFree", "base_url": "http://127.0.0.1:5801/v1",
                             "api_key": "test-key-123", "models": ["mock-a", "mock-b"]}],
            "magpie": {"name": "MockLocal", "base_url": "http://127.0.0.1:5801/v1",
                       "api_key": "test-key-123", "models": ["mock-b", "mag-x"]}
        }, f)

    r = requests.get(BASE + "/ai/")
    check("游客可进入AI页", "游客模式" in r.text and "免费·mock-a" in r.text, r.text)
    check("未登录进不了设置页",
          requests.get(BASE + "/ai/settings", allow_redirects=False).status_code == 302)

    # 个人接口（存于数据库）
    r = s.post(BASE + "/ai/settings", data={
        "base_url": "http://127.0.0.1:5801/v1", "api_key": "test-key-123",
        "model": "mock-a", "system_prompt": "你是一个助手", "temperature": "0.7"})
    check("保存AI设置", "已保存" in r.text, r.text)
    r = s.post(BASE + "/ai/settings", data={"do": "test", "base_url": "http://127.0.0.1:5801/v1",
                                            "api_key": "test-key-123"})
    check("测试连接成功", "连接成功" in r.text and "mock-a" in r.text, r.text)
    r = s.post(BASE + "/ai/settings", data={"do": "test", "base_url": "http://127.0.0.1:5801/v1",
                                            "api_key": "wrong"})
    check("错误密钥测试失败", "HTTP 401" in r.text, r.text)
    check("登录用户默认选中个人接口", "我的接口·mock-a" in s.get(BASE + "/ai/").text, "")

    r = s.post(BASE + "/ai/new", data={"content": "你好，第一条消息"}, allow_redirects=False)
    check("新会话创建", r.status_code == 302 and "/ai/c/" in r.headers.get("Location", ""))
    chat_url = BASE + r.headers["Location"]
    time.sleep(0.3)
    r0 = s.get(chat_url)
    pending = "正在生成" in r0.text
    time.sleep(3)
    r = s.get(chat_url)
    check("后台生成完成可刷新查看", "收到你的消息" in r.text and "第二行用于测试换行渲染" in r.text, r.text)
    check("生成是异步的(先pending后完成)", pending, r0.text)
    check("多行转<br>", "第二行用于测试换行渲染。" in r.text and "<br>" in r.text, r.text)
    check("默认用个人接口模型", "模型=mock-a" in r.text, r.text)
    check("显示所用模型标签", "模型: 我的接口" in r.text, r.text)
    check("对话页面小(%sKB)" % size_kb(r.text), size_kb(r.text) < 10, "")

    r = s.post(chat_url + "/send", data={"content": "第二条"}, allow_redirects=False)
    time.sleep(3)
    r = s.get(chat_url)
    check("续聊成功", "「第二条」" in r.text, r.text)

    # 游客：免登录使用免费模型
    g = requests.Session()
    r = g.post(BASE + "/ai/new", data={"content": "游客提问", "model": "free:mock-a"},
               allow_redirects=False)
    check("游客发起新会话", r.status_code == 302 and "/ai/c/" in r.headers.get("Location", ""),
          r.text[:200])
    gchat = BASE + r.headers["Location"]
    time.sleep(3)
    r = g.get(gchat)
    check("游客免登录使用免费模型", "模型=mock-a" in r.text and "收到你的消息" in r.text, r.text[:400])
    check("游客页显示免费模型标签", "模型: 免费·mock-a" in r.text, r.text[:400])
    check("游客历史会话可见", "游客提问" in g.get(BASE + "/ai/").text, "")
    r = g.post(gchat + "/send", data={"content": "游客第二条", "model": "free:mock-b"},
               allow_redirects=False)
    time.sleep(3)
    r = g.get(gchat)
    check("游客切换免费模型", "模型=mock-b" in r.text, r.text[:400])

    # 会话隔离：登录用户与游客互不可见
    gid = int(gchat.rstrip("/").split("/")[-1])
    check("登录用户看不到游客会话", s.get(BASE + "/ai/c/%d" % gid).status_code == 404)
    check("游客看不到登录用户会话", g.get(chat_url).status_code == 404)

    # 非特权用户请求 magpie 模型 → 静默回退免费模型
    s2 = requests.Session()
    s2.get(BASE + "/captcha.png")
    s2.post(BASE + "/register", data={"username": "用户二" + SUFFIX, "password": "pass1234",
                                      "captcha": CAPTCHA_FIXED})
    r = s2.post(BASE + "/ai/new", data={"content": "普通用户消息", "model": "magpie:mock-b"},
                allow_redirects=False)
    time.sleep(3)
    r = s2.get(BASE + r.headers["Location"])
    check("非特权用户magpie回退免费模型", "模型=mock-a" in r.text and "模型=mock-b" not in r.text,
          r.text[:400])

    # 特权用户（config.json）可用 magpie
    write_cfg(privileged_users=["用户二" + SUFFIX])
    check("特权用户页可见本地模型", "本地·mag-x" in s2.get(BASE + "/ai/").text, "")
    r = s2.post(BASE + "/ai/new", data={"content": "特权消息", "model": "magpie:mock-b"},
                allow_redirects=False)
    time.sleep(3)
    r = s2.get(BASE + r.headers["Location"])
    check("特权用户magpie生成成功", "模型=mock-b" in r.text, r.text[:400])
    write_cfg()
    check("取消特权后看不到本地模型", "本地·" not in s2.get(BASE + "/ai/").text, "")

    # 内置模型密钥全部失效时给出明确错误
    with open(KEYS_PATH, "w", encoding="utf-8") as f:
        json.dump({}, f)
    r = s2.post(BASE + "/ai/new", data={"content": "错误测试", "model": "free:mock-a"},
                allow_redirects=False)
    err_url = BASE + r.headers["Location"]
    time.sleep(1.5)
    r = s2.get(err_url)
    check("模型不可用给明确错误", "不可用或无权限" in r.text, r.text[:400])
    check("失败可重试按钮存在", "重试" in r.text, r.text[:400])
finally:
    with open(KEYS_PATH, "w", encoding="utf-8") as f:
        f.write(orig_keys)
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        f.write(orig_cfg)
check("AI密钥与配置已还原", open(KEYS_PATH, encoding="utf-8").read() == orig_keys
      and open(CFG_PATH, encoding="utf-8").read() == orig_cfg, "")

# ---- 5. 云小说 ----
# 未登录上传被拒
r = requests.get(BASE + "/novel/upload", allow_redirects=False)
check("未登录上传跳转登录", r.status_code == 302 and "/login" in r.headers.get("Location", ""))
# 未登录书架提示
r = requests.get(BASE + "/novel")
check("未登录书架提示登录", "无需登录" in r.text or "登录" in r.text, r.text)

BOOK_TITLE = "我的测试小说" + SUFFIX
with open("/tmp/test_novel_utf8.txt", "rb") as f:
    r = s.post(BASE + "/novel/upload",
               files={"novel_file": ("测试小说.txt", f, "text/plain")},
               data={"title": BOOK_TITLE}, allow_redirects=False)
check("上传UTF-8小说", r.status_code == 302 and "/n/" in r.headers.get("Location", ""),
      str(r.status_code))
view_url = BASE + r.headers["Location"]
code = r.headers["Location"].split("/n/")[1]
r = s.get(view_url)
check("书名正确", BOOK_TITLE in r.text, r.text)
check("识别出3章", "共3章" in r.text, r.text)
check("显示分享链接", "/n/" + code in r.text, r.text)

# 未登录凭链接阅读
anon = requests.Session()
r = anon.get(BASE + "/n/" + code)
check("免登录打开分享链接", BOOK_TITLE in r.text and "开始阅读" in r.text, r.text)
check("免登录页面不显示删除", "删除此书" not in r.text, r.text)
r = anon.get(BASE + "/n/" + code + "/chapters")
check("目录3章", r.text.count("read?c=") == 3, r.text)
for c, p in [(0, 1), (1, 1), (2, 2)]:
    r = anon.get(BASE + "/n/%s/read?c=%d&p=%d" % (code, c, p))
    check("读第%d章第%d页" % (c + 1, p), r.status_code == 200 and "第%d章" % (c + 1) in r.text, r.text[:500])
check("阅读页小(<8KB)", size_kb(r.text) < 8, size_kb(r.text))

# GBK 编码
GBK_TITLE = "GBK小说" + SUFFIX
with open("/tmp/test_novel_gbk.txt", "rb") as f:
    r = s.post(BASE + "/novel/upload",
               files={"novel_file": ("GBK小说.txt", f, "text/plain")},
               data={"title": GBK_TITLE}, allow_redirects=False)
gcode = r.headers["Location"].split("/n/")[1]
r = anon.get(BASE + "/n/%s/read?c=0&p=1" % gcode)
check("GBK小说正确解码", "测试章节标题" in r.text, r.text[:500])

# 删除 GBK 小说（从书架解析其编号）
r = s.get(BASE + "/novel")
m = re.search(r'href="/n/%s".*?href="/novel/(\d+)/delete"' % gcode, r.text, re.S)
check("书架列出GBK小说", m is not None, r.text)
nid = m.group(1) if m else "0"
r = s.get(BASE + "/novel/%s/delete" % nid)
check("删除确认页", "删除确认" in r.text, r.text)
r = s.post(BASE + "/novel/%s/delete" % nid)
check("删除后链接失效", requests.get(BASE + "/n/" + gcode).status_code == 404)
r = s.get(BASE + "/novel")
check("书架不再显示已删小说", GBK_TITLE not in r.text, r.text)

# ---- 5b. 书库配额：普通用户 2MB，特权用户无上限 ----
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
orig_cfg = open(CONFIG_PATH, encoding="utf-8").read()
try:
    qname = "配额用户" + SUFFIX
    sq = requests.Session()
    sq.get(BASE + "/captcha.png")
    sq.post(BASE + "/register", data={"username": qname, "password": "pass1234",
                                      "captcha": CAPTCHA_FIXED})
    r = sq.get(BASE + "/novel/upload")
    check("上传页显示配额", "已用 0KB / 2048KB" in r.text, r.text)

    big1 = b"a" * (1500 * 1024)   # 1.5MB
    r = sq.post(BASE + "/novel/upload",
                files={"novel_file": ("big1.txt", big1, "text/plain")},
                data={"title": "大书一"}, allow_redirects=False)
    check("普通用户上传1.5MB成功", r.status_code == 302, str(r.status_code))

    big2 = b"b" * (1024 * 1024)   # 1MB，累计将超 2MB
    r = sq.post(BASE + "/novel/upload",
                files={"novel_file": ("big2.txt", big2, "text/plain")},
                data={"title": "大书二"}, allow_redirects=False)
    check("超2MB被拒绝", r.status_code == 200 and "超出书库容量" in r.text, r.text)
    check("拒绝页显示用量", "已用 1500KB，本次上传还需 1024KB" in r.text, r.text)

    r = sq.get(BASE + "/novel")
    check("书架显示已用容量", "已用 1500KB / 2048KB" in r.text, r.text)

    # 写入 config.json 成为特权用户（不重启服务，应即时生效）
    write_cfg(privileged_users=[qname])
    r = sq.post(BASE + "/novel/upload",
                files={"novel_file": ("big2.txt", big2, "text/plain")},
                data={"title": "大书二"}, allow_redirects=False)
    check("特权用户不受限制(免重启生效)", r.status_code == 302, r.text[:300])
    r = sq.get(BASE + "/novel/upload")
    check("特权用户上传页显示无限制", "容量无限制" in r.text, r.text)
    r = sq.get(BASE + "/novel")
    check("特权用户书架显示无限制", "容量无限制" in r.text and "已用 2524KB" in r.text, r.text)

    # 清理大书，保持测试数据体积可控
    r = sq.get(BASE + "/novel")
    for nid2 in set(re.findall(r'href="/novel/(\d+)/delete"', r.text)):
        sq.post(BASE + "/novel/%s/delete" % nid2)
    check("清理配额测试大书", "大书一" not in sq.get(BASE + "/novel").text, "")
finally:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(orig_cfg)
check("config.json已还原", json.loads(open(CONFIG_PATH, encoding="utf-8").read())
      == json.loads(orig_cfg), "")

# ---- 6. 页面全局检查 ----
for path in ["/", "/browse", "/ai/", "/ai/settings", "/novel"]:
    r = s.get(BASE + path)
    check("页面 %s 无CSS/JS" % path,
          "<script" not in r.text and "<style" not in r.text and "style=" not in r.text, path)
    check("页面 %s 体积<%sKB" % (path, 8), size_kb(r.text) < 8, size_kb(r.text))

# ---- 7. IP 与用户名双维度限流（最后执行，避免影响其它测试）----
write_cfg(rate_limit={"ip_per_minute": 1000000, "user_per_minute": 4})
rl = requests.Session()
rl.get(BASE + "/captcha.png")
rl.post(BASE + "/register", data={"username": "限流用户" + SUFFIX, "password": "pass1234",
                                  "captcha": CAPTCHA_FIXED}, allow_redirects=False)
hit = None
for _i in range(8):
    r = rl.get(BASE + "/ai/")
    if r.status_code == 429:
        hit = r
        break
check("用户名维度限流生效", hit is not None and "账号限流" in hit.text,
      hit.text[:200] if hit else "未触发")
write_cfg(rate_limit={"ip_per_minute": 1, "user_per_minute": 1000000})
r = requests.get(BASE + "/")
check("IP维度限流生效", r.status_code == 429 and "IP限流" in r.text, r.text[:200])

# 恢复真实配置
with open(CFG_PATH, "w", encoding="utf-8") as f:
    f.write(_orig_cfg)
check("config.json已还原为原样", open(CFG_PATH, encoding="utf-8").read() == _orig_cfg, "")

print("\n== 通过 %d / %d ==" % (ok, ok + fail))
sys.exit(1 if fail else 0)
