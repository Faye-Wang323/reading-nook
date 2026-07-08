# -*- coding: utf-8 -*-
"""
共读小屋 · MCP 包装层
同一进程、同一端口：
  - /mcp/<MCP_TOKEN>  → MCP 接口（Streamable HTTP, JSON-RPC），给 Claude.ai 连接器用
  - 其他路径          → 原封不动交给 app.py 的阅读界面

启动：python3 main.py   （替代原来的 python3 app.py）
环境变量（Zeabur 里配）：
  PORT       监听端口（Zeabur 自动注入）
  MCP_TOKEN  MCP 路径密钥，务必设成长随机串
"""
import json
import os
import re
import time
import uuid

# ---- 先用环境变量生成 config.json（必须在 import app 之前）----
# 公开仓库里不放 config.json，所有私密配置走 Zeabur 环境变量
_ROOT = os.path.dirname(os.path.abspath(__file__))
_cfg_path = os.path.join(_ROOT, "config.json")
try:
    with open(_cfg_path, encoding="utf-8") as _f:
        _cfg = json.load(_f)
except (OSError, json.JSONDecodeError):
    _cfg = {}
_env_map = {
    "PASSCODE": "passcode",
    "DEEPSEEK_API_KEY": "deepseek_api_key",
    "SUBTITLE": "subtitle",
    "LOGIN_HINT": "login_hint",
    "USER_NAME": "user_name",
    "AI_NAME": "ai_name",
}
for _env, _key in _env_map.items():
    if os.environ.get(_env):
        _cfg[_key] = os.environ[_env]
with open(_cfg_path, "w", encoding="utf-8") as _f:
    json.dump(_cfg, _f, ensure_ascii=False, indent=1)

import app  # 复用共读小屋全部逻辑

MCP_TOKEN = os.environ.get("MCP_TOKEN", "").strip()
PORT = int(os.environ.get("PORT", app.PORT))

PROTOCOL_VERSION = "2025-03-26"
SERVER_INFO = {"name": "reading-nook", "version": "1.0"}

# ---------------- 工具实现 ----------------

def t_list_books(args):
    books = app.list_books()
    out = []
    for b in books:
        meta = app.load_json(os.path.join(app.BOOKS_DIR, b["slug"], "meta.json"), {})
        out.append({
            "slug": b["slug"],
            "title": meta.get("title", b["slug"]),
            "chapters": len(meta.get("chapters", [])),
        })
    return out


def t_read_chapter(args):
    slug, idx = args["book"], int(args["chapter"])
    ch = app.get_chapter(slug, idx)
    if ch is None:
        raise ValueError(f"没有这一章：{slug} #{idx}")
    return ch


def t_read_note(args):
    slug, idx = args["book"], int(args["chapter"])
    note = app.load_json(app.note_path(slug, idx), None)
    return note if note else "（这一章没有剧情笔记）"


def t_list_pending(args):
    """列出所有还没有 AI 回应的批注"""
    pending = []
    for b in app.list_books():
        adir = os.path.join(app.BOOKS_DIR, b["slug"], "annotations")
        if not os.path.isdir(adir):
            continue
        for fn in sorted(os.listdir(adir)):
            m = re.match(r"^(\d+)\.json$", fn)
            if not m:
                continue
            idx = int(m.group(1))
            for a in app.load_json(os.path.join(adir, fn), []):
                if a.get("who") == "user" and not a.get("replies"):
                    pending.append({
                        "book": b["slug"], "chapter": idx,
                        "id": a["id"], "anchor": a.get("anchor", ""),
                        "note": a.get("note", ""), "ts": a.get("ts", ""),
                    })
    return pending


def t_read_annotations(args):
    slug, idx = args["book"], int(args["chapter"])
    return app.load_json(app.anno_path(slug, idx), [])


def t_reply(args):
    slug, idx = args["book"], int(args["chapter"])
    aid, text = args["annotation_id"], args["text"]
    path = app.anno_path(slug, idx)
    annos = app.load_json(path, [])
    for a in annos:
        if a["id"] == aid:
            a.setdefault("replies", []).append({
                "who": "ai", "text": text,
                "ts": time.strftime("%Y-%m-%d %H:%M"),
            })
            app.save_json(path, annos)
            return {"ok": True}
    raise ValueError(f"找不到批注 id={aid}（{slug} 第{idx}章）")


def t_add_annotation(args):
    """AI 主动划线写想法（也是蓝色一方的气泡）"""
    slug, idx = args["book"], int(args["chapter"])
    path = app.anno_path(path_slug := slug, idx)
    annos = app.load_json(path, [])
    annos.append({
        "id": uuid.uuid4().hex[:8],
        "anchor": args.get("anchor", ""),
        "note": args["note"],
        "who": "ai",
        "ts": time.strftime("%Y-%m-%d %H:%M"),
        "replies": [],
    })
    app.save_json(path, annos)
    return {"ok": True}


TOOLS = {
    "list_books": {
        "fn": t_list_books,
        "description": "列出书架上所有书（slug、书名、章节数）",
        "schema": {"type": "object", "properties": {}},
    },
    "read_chapter": {
        "fn": t_read_chapter,
        "description": "读某本书的某一章原文（chapter 从 0 开始）",
        "schema": {"type": "object", "properties": {
            "book": {"type": "string", "description": "书的 slug"},
            "chapter": {"type": "integer"}},
            "required": ["book", "chapter"]},
    },
    "read_note": {
        "fn": t_read_note,
        "description": "读某章的 DeepSeek 剧情笔记（150-250字），快速恢复上下文",
        "schema": {"type": "object", "properties": {
            "book": {"type": "string"}, "chapter": {"type": "integer"}},
            "required": ["book", "chapter"]},
    },
    "list_pending": {
        "fn": t_list_pending,
        "description": "列出所有还没有回应的用户批注（划线+想法）",
        "schema": {"type": "object", "properties": {}},
    },
    "read_annotations": {
        "fn": t_read_annotations,
        "description": "读某章的全部批注（含双方往来）",
        "schema": {"type": "object", "properties": {
            "book": {"type": "string"}, "chapter": {"type": "integer"}},
            "required": ["book", "chapter"]},
    },
    "reply_annotation": {
        "fn": t_reply,
        "description": "回应一条用户批注，页面上显示为蓝色气泡",
        "schema": {"type": "object", "properties": {
            "book": {"type": "string"}, "chapter": {"type": "integer"},
            "annotation_id": {"type": "string"}, "text": {"type": "string"}},
            "required": ["book", "chapter", "annotation_id", "text"]},
    },
    "add_annotation": {
        "fn": t_add_annotation,
        "description": "AI 主动在某章划线/写想法（anchor 填被划的原文，可留空）",
        "schema": {"type": "object", "properties": {
            "book": {"type": "string"}, "chapter": {"type": "integer"},
            "anchor": {"type": "string"}, "note": {"type": "string"}},
            "required": ["book", "chapter", "note"]},
    },
}

# ---------------- MCP JSON-RPC ----------------

def mcp_dispatch(req):
    rid = req.get("id")
    method = req.get("method", "")
    print(f"[MCP] <- {method} id={rid}", flush=True)
    if method == "initialize":
        client_ver = (req.get("params") or {}).get("protocolVersion") or PROTOCOL_VERSION
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": client_ver,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO}}
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None  # 通知无需响应
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        tools = [{"name": k, "description": v["description"],
                  "inputSchema": v["schema"]} for k, v in TOOLS.items()]
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}}
    if method == "tools/call":
        name = req["params"]["name"]
        args = req["params"].get("arguments", {}) or {}
        if name not in TOOLS:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601, "message": f"unknown tool {name}"}}
        try:
            result = TOOLS[name]["fn"](args)
            text = result if isinstance(result, str) else json.dumps(
                result, ensure_ascii=False, indent=1)
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": text}]}}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": f"工具执行出错：{e}"}],
                "isError": True}}
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"unknown method {method}"}}


class Handler(app.Handler):
    def _is_mcp(self):
        return MCP_TOKEN and self.path.rstrip("/") == f"/mcp/{MCP_TOKEN}"

    def do_POST(self):
        if not self._is_mcp():
            return super().do_POST()
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) or b"{}"
            print(f"[MCP] POST {len(body)}B UA={self.headers.get('User-Agent','?')[:40]}", flush=True)
            req = json.loads(body)
        except (ValueError, json.JSONDecodeError) as e:
            print(f"[MCP] parse error: {e}", flush=True)
            return self.send_json({"jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": "parse error"}}, 400)
        try:
            if isinstance(req, list):  # JSON-RPC 批量
                resp = [r for r in (mcp_dispatch(x) for x in req) if r is not None]
                resp = resp or None
            else:
                resp = mcp_dispatch(req)
        except Exception as e:
            print(f"[MCP] dispatch crash: {e!r}", flush=True)
            return self.send_json({"jsonrpc": "2.0", "id": None,
                "error": {"code": -32603, "message": f"internal: {e}"}}, 500)
        if resp is None:
            self.send_response(202)
            self.send_header("Mcp-Session-Id", "reading-nook-1")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        data = json.dumps(resp, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Mcp-Session-Id", "reading-nook-1")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self._is_mcp():
            # Streamable HTTP 的服务端推送流：打开 SSE 长连接并保持
            accept = self.headers.get("Accept", "")
            print(f"[MCP] GET stream Accept={accept[:60]} UA={self.headers.get('User-Agent','?')[:40]}", flush=True)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Mcp-Session-Id", "reading-nook-1")
            self.end_headers()
            try:
                while True:
                    # 周期性注释行作为心跳，维持连接；无服务端主动消息
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    time.sleep(15)
            except (BrokenPipeError, ConnectionResetError, OSError):
                print("[MCP] GET stream closed", flush=True)
            return
        return super().do_GET()


if __name__ == "__main__":
    if not MCP_TOKEN:
        print("警告：未设置 MCP_TOKEN 环境变量，MCP 接口关闭，仅阅读界面可用")
    server = app.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"共读小屋+MCP running on :{PORT}")
    server.serve_forever()
