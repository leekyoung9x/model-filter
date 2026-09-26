#!/usr/bin/env python3
"""BRIEF M137 — proxy-filter HTTP dung truoc 9router (port 20127).

Client -> 127.0.0.1:20130 (proxy nay) -> 127.0.0.1:20127 (9router).
Chi stdlib (http.server + http.client). Chay:
  python3 /poki/tools/model-filter/proxy.py [--host 127.0.0.1] [--port 20130] [--upstream http://127.0.0.1:20127]
Docker (tren VPS 157): --host 0.0.0.0 --upstream http://9router:20128
Stdout moi request 1 dong log: ts, path, model, blocked, matched_keyword.
KHONG log Authorization, KHONG log toan bo prompt.
"""
import argparse
import datetime
import http.client
import json
import sys
import time
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

CANNED = ("Xin lỗi, tôi không có thông tin về vấn đề này. "
          "Tôi chỉ có thể hỗ trợ trả lời các câu hỏi trong phạm vi công việc được giao.")

# Model thuoc combo thtung-muse (SELECT combos WHERE name='thtung-muse', 2026-09-25)
COMBO_MODELS = frozenset({
    "thtung-muse",
    "muse/muse-spark-1.3-contributor",
    "oc/muse-spark-1.3-contributor-free",
    "oc/muse-spark-1.2-contributor-free",
})

KEYWORDS_VN = [
    "nguồn gốc", "nguon goc",
    "nhà cung cấp", "nha cung cap", "nhà cung cap", "cung cấp bởi",
    "mô hình nào", "mô hình gì", "model nào", "model gì",
    "mô hình ai", "ai nào đang",
    "bạn là ai", "ban la ai", "bạn là model",
    "bạn thuộc", "thuộc công ty nào", "của công ty nào",
    "do ai tạo", "ai tạo ra bạn", "ai phát triển bạn", "phát triển bởi",
]
KEYWORDS_EN = [
    "which model", "what model", "model provider",
    "who made you", "who created you", "who developed you",
    "what company", "which company",
    "powered by", "built by", "created by", "developed by",
    "your provider", "provider name", "model name",
    "model identity", "underlying model",
]
KEYWORDS_VENDOR = [
    "Muse", "gpt", "gemini", "deepseek", "qwen", "llama",
    "mistral", "mimo", "glm", "kimi", "grok",
    "openai", "anthropic", "meta ai", "google deepmind",
    "moonshot", "zhipu", "alibaba", "provider", "upstream",
]
# Lower + NFC san de so khop sau normalize.
KEYWORDS = [unicodedata.normalize("NFC", k.lower()) for k in
            (KEYWORDS_VN + KEYWORDS_EN + KEYWORDS_VENDOR)]


def norm(text):
    return unicodedata.normalize("NFC", text.lower())


def match_keyword(text):
    """Tra ve keyword dau tien khop, hoac None."""
    t = norm(text)
    for kw in KEYWORDS:
        if kw in t:
            return kw
    return None


def extract_user_text(body):
    """Chi gom text cua role 'user' + prompt legacy. KHONG quet system prompt:
    system la cua operator (bot Hermes...) chua tu nhu 'openai-compatible'
    gay chan nham moi cau (bug 2026-09-26: 'hi'/'alo' bi matched=openai)."""
    parts = []
    try:
        msgs = body.get("messages") or []
        for m in msgs:
            if not isinstance(m, dict):
                continue
            if m.get("role") != "user":
                continue
            c = m.get("content")
            if isinstance(c, str):
                parts.append(c)
            elif isinstance(c, list):
                for p in c:
                    if isinstance(p, str):
                        parts.append(p)
                    elif isinstance(p, dict) and isinstance(p.get("text"), str):
                        parts.append(p["text"])
        pr = body.get("prompt")
        if isinstance(pr, str):
            parts.append(pr)
        elif isinstance(pr, list):
            for p in pr:
                if isinstance(p, str):
                    parts.append(p)
    except Exception:
        pass
    return "\n".join(parts)


def model_in_scope(model):
    if not isinstance(model, str) or not model:
        return False
    return model in COMBO_MODELS or model.startswith("thtung-muse")


UPSTREAM = "http://127.0.0.1:20127"
FORWARD_TIMEOUT = 120
HOP_HEADERS = {"host", "connection", "keep-alive", "transfer-encoding",
               "upgrade", "proxy-authenticate", "proxy-authorization", "te", "trailer"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ModelFilter/1.0"

    def log_message(self, *args):
        pass  # chi dung log 1-dong cua M137 ra stdout

    def log(self, path, model, blocked, matched):
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        print(f"{ts} path={path} model={model} blocked={str(blocked).lower()} "
              f"matched={matched or '-'}", flush=True)

    def _send_json(self, status, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _blocked_body(self, model, stream):
        cid = f"chatcmpl-filter-{int(time.time() * 1000)}"
        now = int(time.time())
        if not stream:
            return {"id": cid, "object": "chat.completion", "created": now,
                    "model": model,
                    "choices": [{"index": 0,
                                 "message": {"role": "assistant", "content": CANNED},
                                 "finish_reason": "stop"}]}, None
        chunk = {"id": cid, "object": "chat.completion.chunk", "created": now,
                 "model": model,
                 "choices": [{"index": 0,
                              "delta": {"role": "assistant", "content": CANNED},
                              "finish_reason": None}]}
        sse = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\ndata: [DONE]\n\n"
        return None, sse.encode("utf-8")

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/health":
            self.log("/health", "-", False, None)
            self._send_json(200, {"ok": True})
            return
        self._forward(None)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length > 0 else b""
        self._forward(raw)

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_GET

    def _forward(self, raw):
        up = urlsplit(UPSTREAM)
        path_qs = self.path
        path = urlsplit(path_qs).path
        # new-api goi path nhan doi /v1/v1/chat/completions, hoac /responses —
        # nhan moi path ket thuc bang chat/completions hay responses.
        is_chat = path.endswith(("/chat/completions", "/responses"))
        model, stream, matched, in_scope = "-", False, None, False

        if is_chat and raw:
            try:
                body = json.loads(raw.decode("utf-8"))
            except Exception:
                body = None
            if isinstance(body, dict):
                model = body.get("model") or "-"
                stream = body.get("stream") is True
                if model_in_scope(model):
                    in_scope = True
                    matched = match_keyword(extract_user_text(body))
                if in_scope and matched:
                    self.log(path, model, True, matched)
                    if stream:
                        _, sse = self._blocked_body(model, True)
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.send_header("Cache-Control", "no-cache")
                        self.send_header("Content-Length", str(len(sse)))
                        self.send_header("Connection", "close")
                        self.end_headers()
                        self.wfile.write(sse)
                    else:
                        obj, _ = self._blocked_body(model, False)
                        self.send_response(200)
                        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(data)))
                        self.send_header("Connection", "close")
                        self.end_headers()
                        self.wfile.write(data)
                    try:
                        self.close_connection = True
                    except Exception:
                        pass
                    return

        # Forward nguyen ven sang upstream, pipe ve.
        # API chat (is_chat): strip encoding de doc body (9router khong nen gzip
        # JSON chat; giu Accept-Encoding goc chi gay double-gzip o dashboard).
        # Non-chat (dashboard/assets): pass-through TUYET DOI — giu nguyen
        # moi header + bytes, khong strip content-encoding/content-length.
        fwd_headers = {k: v for k, v in self.headers.items()
                       if k.lower() not in HOP_HEADERS}
        pass_through = not is_chat
        if not pass_through:
            fwd_headers = {k: v for k, v in fwd_headers.items()
                           if k.lower() not in ("accept-encoding", "content-encoding",
                                                "content-length", "transfer-encoding")}
        try:
            conn = http.client.HTTPConnection(up.hostname, up.port or 80,
                                              timeout=FORWARD_TIMEOUT)
            conn.request(self.command, path_qs, body=raw, headers=fwd_headers)
            resp = conn.getresponse()
            if pass_through:
                # Dashboard/static: pipe status + headers + bytes y nguyen.
                data = resp.read()
                self.send_response(resp.status, resp.reason)
                for k, v in resp.getheaders():
                    if k.lower() not in ("connection", "transfer-encoding",
                                         "keep-alive", "upgrade"):
                        self.send_header(k, v)
                self.send_header("Connection", "close")
                self.end_headers()
                if data:
                    self.wfile.write(data)
            elif stream and is_chat:
                # Relay chunk real-time (chunked).
                self.send_response(resp.status, resp.reason)
                for k, v in resp.getheaders():
                    if k.lower() not in ("content-length", "transfer-encoding",
                                         "connection", "content-encoding"):
                        self.send_header(k, v)
                if not resp.getheader("Content-Type"):
                    self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.send_header("Connection", "close")
                self.end_headers()
                while True:
                    piece = resp.read(8192)
                    if not piece:
                        break
                    self.wfile.write(b"%X\r\n%s\r\n" % (len(piece), piece))
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            else:
                data = resp.read()
                self.send_response(resp.status, resp.reason)
                for k, v in resp.getheaders():
                    if k.lower() not in ("content-length", "transfer-encoding",
                                         "connection", "content-encoding"):
                        self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                if data:
                    self.wfile.write(data)
            self.log(path, model, False, None)
            try:
                self.close_connection = True
            except Exception:
                pass
            conn.close()
        except Exception as e:
            self.log(path, model, False, None)
            try:
                self._send_json(502, {"error": {"message": f"upstream error: {e}",
                                                "type": "upstream_error"}})
            except Exception:
                pass


def main():
    global UPSTREAM
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=20130)
    ap.add_argument("--upstream", default="http://127.0.0.1:20127")
    args = ap.parse_args()
    UPSTREAM = args.upstream.rstrip("/")
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    print(f"[model-filter] listening {args.host}:{args.port} -> {UPSTREAM}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
