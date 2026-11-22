# 诺基亚助手

一个给功能机浏览器用的 Flask 网站。目标设备是诺基亚这类老手机：屏幕小，内存少，浏览器只认最基本的 HTML，页面稍大就打不开。站点因此没有 CSS，没有 JavaScript，也不做布局，所有页面由服务端拼成纯 HTML，单页控制在 8KB 以内。

响应头统一带 `Cache-Control: no-store`。功能机浏览器和运营商网关都爱缓存页面，而 AI 回复要手动刷新才能看到，不关掉缓存就会一直显示旧内容。主导航给每个入口挂了 `accesskey` 数字键，部分机型能直接按数字跳转。

账号全站共用一次登录，签名 Cookie 有效期 30 天。注册和登录都要填图形验证码，由 easy-captcha 生成，120×40 PNG，用完即作废。全站按 IP 和用户名两个维度限流，默认每分钟 120 次和 60 次，额度写在 `config.json` 里。数据存在 SQLite（`data/nokia.db`），小说正文单独存成 UTF-8 文本文件，放在 `data/novels/`。

## 三个服务

### 网页浏览 `/browse`

服务器替手机抓取网页，去掉 CSS、JavaScript、图片和注释，剩下的正文按 5000 字符左右分页，链接全部改写成站内代理链接。搜索框、下拉框、按钮这类交互元素保留，表单转成经代理的 GET 提交，输入的内容并进目标地址，所以用必应搜索是能用的。这个功能需要登录。

抓取用 [ssrfguard](https://pypi.org/project/ssrfguard/) 做防护。它先把域名解析出来，校验每个地址，然后直连这个已验证的 IP，域名中途改指向骗不过去，重定向的每一跳同样检查。内网地址和 80/443/8080 以外的端口直接拒绝。

### AI 对话 `/ai/`

兼容 OpenAI 的 `/chat/completions` 接口，模型分三档：

- 免费模型：Radeon 和 NVIDIA NIM，密钥在 `data/api_keys.json` 的 `free_groups` 里。不登录也能用，游客会话靠 Cookie 里的随机键区分，7 天不动就清掉。
- 本地聚合 magpie（`127.0.0.1:1234`）：同一个文件的 `magpie` 段，只有 `config.json` 里 `privileged_users` 列出的账号能用。
- 个人接口：登录用户在设置页填自己的地址、密钥和模型，存在数据库里，跟着账号走。

每条消息可以选模型，回复下方显示实际用的哪个。接口是非流式的，消息发出后服务端后台线程去请求，页面显示「正在生成回复」，手动点刷新就能看到结果，出错时旁边有重试按钮。会话可以连续聊，每次带最近 12 条上下文。设置页有「测试连接」按钮，填完可以先验证密钥。

### 云小说 `/novel`

登录后可以传 TXT（UTF-8、GBK、Big5、UTF-16 自动识别）或者直接粘贴文本，单文件最大 15MB。上传要账号，阅读不用：别人拿到 `/n/<随机码>` 链接就能看，不需要注册。

大书可以分段传：书名填一样的，内容就追加到那本书末尾，章节重新切一遍，书架里不会多出一本。单本书累计上限 50MB。

书架页列出自己的书，有阅读、目录、分享链接和删除。每个账号的书库配额 2MB，`config.json` 里 `privileged_users` 的账号不限额。配置每次请求都重新读取，改完不用重启。

章节有两种识别方式。一行 `#==#标题` 作分隔符，分隔符这行本身不进正文，第一个分隔符之前的文字算作「前言」；没有分隔符时退回识别「第 X 章」这类标题。正文每页 500 字硬切，600 字的章节就是第 500 字前后两页。

翻页会自动记进度。登录用户的进度存数据库，游客的存在 Cookie 里，书架和书页上都有「继续阅读」，点了直接回到上次看到的位置。

## 运行

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./run.sh            # 默认 0.0.0.0:8000
PORT=8080 ./run.sh  # 换端口
```

手机浏览器打开 `http://<电脑IP>:8000/`。

## 配置

`config.json` 和 `data/api_keys.json` 都在 .gitignore 里，仓库只放了同名的 example 文件作格式参考。

`config.json`：

```json
{
  "privileged_users": ["Alice"],
  "rate_limit": {"ip_per_minute": 120, "user_per_minute": 60},
  "captcha_fixed": ""
}
```

- `privileged_users`：特权用户名，可以用 magpie 模型，书库不限量。
- `rate_limit`：限流额度，缺哪项就用默认值。
- `captcha_fixed`：填了就用固定验证码，只给测试用，正式部署留空。

`data/api_keys.json` 放内置模型的地址和密钥，`api_keys.example.json` 里有完整结构：`free_groups` 是数组，每组一个 `base_url`、一个 `api_key` 和一个 `models` 列表；`magpie` 是一组同样的字段。文件权限建议 600。

`secret.key` 是会话签名密钥，首次启动自动生成 32 字节随机值，权限 600，可以用环境变量 `SECRET_KEY` 覆盖。删掉这个文件，所有登录态一起失效。

## 测试

```sh
.venv/bin/python tests/mock_openai.py &   # 模拟 OpenAI 接口，监听 5801
.venv/bin/python tests/test_flow.py       # 端到端测试
```

测试会临时改写 `config.json`（固定验证码、放开限流），跑完还原。当前 134 项断言，覆盖注册登录（含验证码异常路径）、代理清洗与分页、表单代理提交、ssrfguard 拦截、AI 三层模型与异步生成、小说上传与章节分页、阅读进度、配额限制，以及每个页面无 CSS/JS 且小于 8KB。

## 已知限制

- 代理只把表单转成 GET，搜索这类场景够用；需要 POST 还带登录态的表单提交不了。
- 免费模型的密钥写在服务器本地文件里，所有访客共用同一个 key。
- 经 cloudflare quick tunnel 上传几 MB 以上的文件容易 502。大书走局域网，或者用同名分段分几次传。
- ssrfguard 只放行 80、443、8080 端口。跑 Clash 这类 fake-ip 代理的机器额外放行了 `198.18.0.0/15`，走系统代理时解析由代理层完成，IP 固定那层保护就交出去了。

## 目录

```
app.py        入口：蓝图注册、限流中间件、错误页
auth.py       注册、登录、登出、验证码
browse.py     网页抓取、清洗、分页
aichat.py     AI 对话
aikeys.py     内置模型加载与分层
novels.py     云小说
ratelimit.py  IP 与用户名限流
db.py         SQLite
helpers.py    登录态、提示页等公共函数
templates/    纯 HTML 模板
tests/        端到端测试与 mock 服务
```

## 许可

Copyright (C) 2026 陈子涵

本项目以 GNU General Public License v3.0 发布，全文见 LICENSE。
