"""本地模拟 OpenAI 兼容接口，用于测试 AI 对话功能。端口 5801。"""

import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/models"):
            auth = self.headers.get("Authorization", "")
            if auth != "Bearer test-key-123":
                return self._send({"error": "bad key"}, 401)
            return self._send({"data": [{"id": "mock-a"}, {"id": "mock-b"}]})
        return self._send({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n))
        if not self.path.endswith("/chat/completions"):
            return self._send({"error": "not found"}, 404)
        last_user = next(m["content"] for m in reversed(req["messages"]) if m["role"] == "user")
        reply = "(模型=%s) 收到你的消息: 「%s」。这是一条由模拟接口返回的非流式回复，共两行。\n第二行用于测试换行渲染。" % (
            req.get("model", "?"), last_user[:50])
        if req.get("stream"):
            # 模拟 SSE：分片推送，最后以 [DONE] 结束
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            # 先发一个空角色帧（真实接口常见），测试「等待开始输出」
            self.wfile.write(b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n')
            self.wfile.flush()
            time.sleep(1)
            for ch in reply:
                chunk = {"choices": [{"delta": {"content": ch}}]}
                line = "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"
                self.wfile.write(line.encode("utf-8"))
                self.wfile.flush()
                time.sleep(0.005)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            self.close_connection = True
            return
        time.sleep(2)
        return self._send({
            "choices": [{"message": {"role": "assistant", "content": reply}}]
        })


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 5801), H).serve_forever()
