# 诺基亚助手（nokia_assistant）

给功能机浏览器用的 Flask 网站。目标设备屏幕小、内存少、只认基本 HTML。
仓库：`https://github.com/aura-deak/nokia-assistant.git`，默认分支 `master`。

密钥、接入信息和部署细节都写在本目录的 `other.md`（已被 git 忽略，不提交）。

## 硬约束

- 页面无 CSS、无 JavaScript、无图片、无注释，全部服务端拼纯 HTML。
- 单页 HTML 必须小于 8KB（测试会逐页断言）。
- 响应头统一 `Cache-Control: no-store`。
- 主导航用 `accesskey` 数字键，功能机可按数字跳转。
- 运行环境拦截永久删除的 `rm`，删除一律走回收站。

## 目录

```
app.py        入口：蓝图注册、限流中间件、错误页
auth.py       注册、登录、登出、验证码
browse.py     网页抓取、清洗、分页
aichat.py     AI 对话（流式）
novels.py     云小说（含快照下载与 TXT 转换）
snapshot.py   诺基亚浏览器快照格式
tools.py      词典（中英）+ 翻译 + 维基搜索
game.py       52 周奋斗（FightOrRest 重实现）
aikeys.py     内置模型加载与分层
ratelimit.py  IP 与用户名限流
db.py         SQLite
helpers.py    登录态、提示页等公共函数
templates/    纯 HTML 模板
tests/        端到端测试与 mock 服务
```

## 数据

- SQLite：`data/nokia.db`；小说正文：`data/novels/`。
- `data/` 整个目录在 `.gitignore` 里，不进仓库。
- `config.json`：`privileged_users`、`rate_limit`、`captcha_fixed`（测试用）。
- 密钥文件位置和结构见 `other.md` 和 `api_keys.example.json`。

## 重要设计决定

- 网页代理抓取前读 `robots.txt`，禁止路径直接拦；**没有 robots.txt 视为不允许**（按站点缓存 1 小时，UA `NokiaAssistantProxy`）。
- 抓取结果编码自动识别并统一转 UTF-8（HTTP 头 → HTML meta → 字符集探测）。
- 代理不留 cookie：目标站 `Set-Cookie` 全部丢弃，不退传。
- 禁止代理浏览本站自身（套娃）：目标 netloc 与当前 Host 相等，或本机地址同端口，直接拦。
- 内置搜索用 wiby.me（主流引擎 robots 都禁搜索路径）。
- 浏览页内置 10 个资讯站快捷入口（写死在 `browse.py` 的 `_DEFAULT_LINKS`）；用户自定义入口存 `quick_links` 表。
- AI 生成流式：`requests.stream=True` 解析 SSE delta，每 0.6s 写库；状态 `pending/streaming/done`，页面自动刷新。
- 小说快照：诺基亚浏览器快照格式，内置 `nokia_read_novel` 的逆向成果。

## 运行与测试

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./run.sh                     # 默认 0.0.0.0:8000，PORT 可覆盖

.venv/bin/python tests/mock_openai.py &   # 模拟 OpenAI，监听 5801
.venv/bin/python tests/test_flow.py       # 端到端，当前 144 项断言
```

测试会临时改写 `config.json`，跑完还原。改动后必须跑通全部断言再提交。

## 已知限制

- 代理只把表单转成 GET，需要 POST 加登录态的表单提交不了。
- 免费模型密钥写在服务器本地，所有访客共用。
- 经 cloudflare quick tunnel 上传几 MB 以上文件容易 502。
- ssrfguard 只放行 80/443/8080；Clash fake-ip 网段 `198.18.0.0/15` 额外放行。
