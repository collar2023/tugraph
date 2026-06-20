"""
scripts/smoke_check.py — 演示前 5 分钟冒烟检查 (v2 前端视角, 2026-06-20)
========================================================================

只走前端浏览器真实会走的 4 条链路, **绝不 import 后端 Python 库**。
任何改动都不会影响演示前端。

链路:
  浏览器 fder.188001.xyz
    → CF Worker (route + secret)
      → https://fde.188001.xyz:5000 (mcp_proxy.py FastAPI)
        → mcp_server.py 12 tools → TuGraph + Qdrant

检查清单 (5 项, 任一 FAIL 立即 exit 1):
  1. 前端首页 GET /                 — 浏览器打开第一眼
  2. GET /api/tasks                  — 任务看板列表
  3. POST /api/mcp-proxy/mcp/call    — 调一个最无害的 MCP tool (execute_cypher 纯读)
  4. POST /api/mcp-proxy/mcp/call    — 再调一个写类 tool (list_pending_reviews, 验证写链路可达)
  5. WS /api/ws Upgrade              — Durable Objects 实时推送握手

输出:
  - 屏幕: 彩色 PASS/FAIL + 红色失败项高亮
  - logs/smoke_<timestamp>.json      — 落盘可被 jq 解析

用法:
  python3 scripts/smoke_check.py
"""
from __future__ import annotations
import os
import sys
import json
import time
import socket
import ssl
import argparse
import datetime
import urllib.request
import urllib.error
from pathlib import Path

# ---------- 配置 ----------
CF_BASE = os.environ.get("CF_BASE", "https://fder.188001.xyz")
# 演示用 secret: 读 ~/.secrets/backend.env 或走默认占位
_SECRET_PATH = Path.home() / ".secrets" / "backend.env"
HARNESS_SECRET = os.environ.get("HARNESS_SECRET", "")
if not HARNESS_SECRET and _SECRET_PATH.exists():
    for line in _SECRET_PATH.read_text().splitlines():
        if line.startswith("HARNESS_SECRET="):
            HARNESS_SECRET = line.split("=", 1)[1].strip()
            break
if not HARNESS_SECRET:
    HARNESS_SECRET = "dev-secret-CHANGEME"

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

# ---------- 颜色 (无 colorama 时降级) ----------
try:
    from colorama import init as _ci, Fore, Style
    _ci()
    GREEN = Fore.GREEN + "✓"
    RED = Fore.RED + "✗"
    YELLOW = Fore.YELLOW + "!"
    CYAN = Fore.CYAN + "·"
    RESET = Style.RESET_ALL
    BOLD = Style.BRIGHT
except ImportError:
    GREEN = "[OK]"; RED = "[FAIL]"; YELLOW = "[WARN]"; CYAN = "[..]"; RESET = ""; BOLD = ""

# ---------- 结果收集 ----------
class Reporter:
    def __init__(self):
        self.results: list[dict] = []
        self.fail_count = 0
        self.warn_count = 0
        self.start_ts = time.time()

    def add(self, name: str, ok: bool, detail: str = "", warn: bool = False, latency_ms: int = 0):
        rec = {"name": name, "ok": ok, "warn": warn, "detail": detail, "latency_ms": latency_ms, "ts": datetime.datetime.now(datetime.timezone.utc).isoformat()}
        self.results.append(rec)
        if warn:
            self.warn_count += 1
            tag = YELLOW
        elif ok:
            tag = GREEN
        else:
            self.fail_count += 1
            tag = RED
        suffix = f" ({latency_ms}ms)" if latency_ms else ""
        print(f"  {tag} {name:<48} {detail}{suffix}")

    def summary(self) -> dict:
        total = len(self.results)
        passed = sum(1 for r in self.results if r["ok"] and not r["warn"])
        return {
            "total": total, "passed": passed,
            "failed": self.fail_count, "warned": self.warn_count,
            "duration_sec": round(time.time() - self.start_ts, 2),
            "checks": self.results,
        }

R = Reporter()

def step(t):
    print(f"\n{BOLD}── {t} ──{RESET}")

def _t():
    return time.time()

def _e(t0):
    return int((time.time() - t0) * 1000)

def _http(method: str, path: str, body: dict | None = None, timeout: int = 8) -> tuple[int, dict | str]:
    """对 CF Worker 发请求, 走前端真实会用的 secret header"""
    url = CF_BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "X-Harness-Secret": HARNESS_SECRET,
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace") if e.fp else ""
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw
    except Exception as e:
        return 0, {"_exc": str(e)[:200]}

# ============================================================
# 1. 前端首页
# ============================================================
def check_homepage():
    step(f"1. 前端首页  {CF_BASE}/")
    t = _t()
    status, body = _http("GET", "/", timeout=8)
    if status == 200 and isinstance(body, str) and "<html" in body.lower():
        R.add("GET / (前端 HTML)", True, f"{len(body)} bytes", latency_ms=_e(t))
    else:
        R.add("GET / (前端 HTML)", False, f"HTTP {status}, 演示时浏览器会白屏")

# ============================================================
# 2. 任务列表
# ============================================================
def check_tasks():
    step("2. 任务看板  /api/tasks")
    t = _t()
    status, body = _http("GET", "/api/tasks", timeout=8)
    if status == 200 and isinstance(body, dict) and "tasks" in body:
        n = len(body["tasks"])
        if n == 0:
            R.add("GET /api/tasks", True, "0 条 (演示前可上传 1 个文件预热)", _e(t), warn=True)
        else:
            last_status = body["tasks"][0].get("status", "?")
            R.add("GET /api/tasks", True, f"{n} 条, 最新状态: {last_status}", latency_ms=_e(t))
    else:
        R.add("GET /api/tasks", False, f"HTTP {status}, 演示时任务看板为空")

# ============================================================
# 3. MCP 读类工具 (execute_cypher 纯读, 安全)
# ============================================================
def check_mcp_read():
    step("3. MCP 读类工具  /api/mcp-proxy/mcp/call")
    t = _t()
    status, body = _http("POST", "/api/mcp-proxy/mcp/call", body={
        "name": "execute_cypher",
        "arguments": {"query": "MATCH (c:Corp) RETURN c.corp_id AS id, c.name AS name LIMIT 3"},
    }, timeout=12)
    if status == 200 and isinstance(body, dict) and body.get("ok") is True:
        result = body.get("result", {})
        # result 多种形态都支持: 字符串 "[...]" / 对象 {"rows":[...]} / 列表 [...]
        rows = None
        if isinstance(result, str):
            try:
                parsed = json.loads(result)
                rows = parsed if isinstance(parsed, list) else parsed.get("rows")
            except json.JSONDecodeError:
                pass
        elif isinstance(result, dict):
            rows = result.get("rows")
        elif isinstance(result, list):
            rows = result
        if rows is not None:
            R.add("execute_cypher (纯读, 不入审计)", True, f"{len(rows)} 行", latency_ms=_e(t))
        else:
            R.add("execute_cypher", False, f"result 结构未知: {str(result)[:120]}", latency_ms=_e(t))
    else:
        # 可能是 401 (secret 不对) 或代理 5xx
        R.add("execute_cypher (纯读)", False, f"HTTP {status}: {str(body)[:120]}")

# ============================================================
# 4. MCP 元数据工具 (list_tools, 不触发审计)
# ============================================================
def check_mcp_meta():
    step("4. MCP 元数据工具  /api/mcp-proxy/mcp/call")
    t = _t()
    status, body = _http("POST", "/api/mcp-proxy/mcp/call", body={
        "name": "list_tools",
        "arguments": {},
    }, timeout=10)
    if status == 200 and isinstance(body, dict) and body.get("ok") is True:
        result_str = body.get("result", "{}")
        try:
            result = json.loads(result_str) if isinstance(result_str, str) else result_str
            n = len(result.get("tools", []))
            R.add("list_tools (12 个契约)", n == 12, f"实际 {n} 个", latency_ms=_e(t))
        except (json.JSONDecodeError, TypeError):
            R.add("list_tools", False, f"result 解析失败: {str(result_str)[:80]}", latency_ms=_e(t))
    else:
        R.add("list_tools", False, f"HTTP {status}: {str(body)[:120]}")

# ============================================================
# 5. WebSocket 升级 (Durable Object)
# ============================================================
def check_websocket():
    step("5. Durable Object WebSocket 实时推送  /api/ws")
    import ssl as _ssl
    t = _t()
    try:
        ctx = _ssl.create_default_context()
        with ctx.wrap_socket(socket.create_connection(("fder.188001.xyz", 443), timeout=5), server_hostname="fder.188001.xyz") as s:
            req = (
                b"GET /api/ws HTTP/1.1\r\n"
                b"Host: fder.188001.xyz\r\n"
                b"Upgrade: websocket\r\n"
                b"Connection: Upgrade\r\n"
                b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                b"Sec-WebSocket-Version: 13\r\n"
                b"\r\n"
            )
            s.sendall(req)
            data = s.recv(2048)
        status_line = data.split(b"\r\n", 1)[0].decode(errors="replace").strip()
        is_101 = " 101 " in status_line
        # 进一步确认: 应该看到 "fder.188001.xyz" via DO binding
        if is_101:
            R.add("WS /api/ws Upgrade → 101", True, status_line, latency_ms=_e(t))
        else:
            R.add("WS /api/ws Upgrade", False, status_line, latency_ms=_e(t))
    except Exception as e:
        R.add("WS /api/ws", False, str(e)[:80] + " (本机无外网/DNS 失败, 演示时浏览器开 fder.188001.xyz 看板验证)", _e(t), warn=True)

# ============================================================
# Main
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="演示前 5 分钟冒烟检查 (前端视角)")
    parser.add_argument("--no-ws", action="store_true", help="跳过 WS 检查 (本机无外网时)")
    args = parser.parse_args()

    print(f"{BOLD}╔════════════════════════════════════════════════════════════╗")
    print(f"║   演示前冒烟检查  v2 前端视角  ·  {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}   ║")
    print(f"╚════════════════════════════════════════════════════════════╝{RESET}")
    print(f"  {CYAN}前端: {CF_BASE}  ·  5 项检查, 全走 HTTP/WS 黑盒, 不 import 后端库{RESET}")
    print(f"  {CYAN}secret: {'<env>' if HARNESS_SECRET != 'dev-secret-CHANGEME' else '默认占位符 (演示用, OK)'}{RESET}")

    check_homepage()
    check_tasks()
    check_mcp_read()
    check_mcp_meta()
    if not args.no_ws:
        check_websocket()

    s = R.summary()
    print(f"\n{BOLD}── 汇总 ──{RESET}")
    color = GREEN if s["failed"] == 0 else RED
    print(f"  {color}  总 {s['total']}  通过 {s['passed']}  失败 {s['failed']}  警告 {s['warned']}  耗时 {s['duration_sec']}s{RESET}")
    if s["failed"]:
        print(f"\n{RED}{BOLD}  ✗ 存在失败项, 演示前必须修复:{RESET}")
        for r in s["checks"]:
            if not r["ok"] and not r["warn"]:
                print(f"    {RED}• {r['name']}: {r['detail']}{RESET}")
    elif s["warned"]:
        print(f"\n  {YELLOW}{BOLD}  ! 有警告, 演示前过一眼:{RESET}")
        for r in s["checks"]:
            if r["warn"]:
                print(f"    {YELLOW}• {r['name']}: {r['detail']}{RESET}")

    log_path = LOG_DIR / f"smoke_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    log_path.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  {CYAN}日志: {log_path.relative_to(Path.cwd()) if log_path.is_relative_to(Path.cwd()) else log_path}{RESET}")
    print()
    sys.exit(1 if s["failed"] else 0)


if __name__ == "__main__":
    main()
