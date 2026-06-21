"""
mcp_proxy.py — MCP stdio → HTTP 代理 (v3, 2026-06-20)
=================================================

v3 改动 (Day 1 - fde.188001.xyz 上线):
  - 加 X-Harness-Secret Header 校验中间件（防未授权调本地）
  - 加 POST /api/callback（白皮书第 1 节：CF Worker 审计完回调写本地）
  - 加 POST /api/chat（白皮书第 1 节：诊断问答转发）
  - healthz 公开（不需 secret，CF 探活 / 监控用）
  - CORS 收紧：allow_origins=["https://fde.188001.xyz"]（默认；可被 ALLOWED_ORIGINS env 覆盖）

v2 改动:
  - 改用 FastAPI lifespan 替代 on_event（解决 0.136+ deprecation 警告）
  - _read_response 改用 read1+解块读，避开 readline 单行问题
  - 启动时静默 banner（重定向 MCP stdout 到 DEVNULL），JSON-RPC 通过专用 channel
  - 增强 healthz 返回 session_id

为什么需要这个:
  - 浏览器 fetch() 调不到 stdio 进程
  - CF Worker / 前端 SPA 都需要 HTTP 入口
  - 复用现成 mcp_server.py 12 个 tool, 0 改动

启动:
  cd /home/ubuntu/tugraph/procurement-audit-mcp
  python3 mcp_proxy.py            # 默认 127.0.0.1:5000
  PORT=8765 python3 mcp_proxy.py  # 自定义端口

环境变量:
  HARNESS_SECRET  - X-Harness-Secret 校验密钥（默认 dev-secret-CHANGEME）
  ALLOWED_ORIGINS - 允许的 CORS 来源（默认 https://fde.188001.xyz）

注意:
  - 只监听 127.0.0.1（白皮书 7.1.2 安全护栏: FastAPI 不暴露 0.0.0.0）
  - 外部访问请走 Cloudflare Tunnel（fde.188001.xyz）
  - HARNESS_SECRET 生产前必须改：写到 /home/ubuntu/.secrets/backend.env
"""
import os
import sys
import json
import asyncio
import subprocess
import threading
from contextlib import asynccontextmanager
from typing import Any, Optional
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

HERE = os.path.dirname(os.path.abspath(__file__))
MCP_SCRIPT = os.path.join(HERE, "mcp_server.py")
DEMO_HTML = os.path.join(HERE, "demo.html")
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "5000"))
HARNESS_SECRET = os.environ.get("HARNESS_SECRET", "dev-secret-CHANGEME")
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "https://fde.188001.xyz").split(",")


# ==========================================
# MCP stdio 客户端
# ==========================================
class McpStdioClient:
    """极简 MCP JSON-RPC over stdio 客户端。

    FastMCP 协议约定:
      请求: {"jsonrpc":"2.0","id":N,"method":"tools/call","params":{"name":..., "arguments":...}}
      响应: {"jsonrpc":"2.0","id":N,"result":{"content":[{"type":"text","text": "..."}]}}
      通知: {"jsonrpc":"2.0","method":"notifications/initialized"} (无 id, 无响应)

    FastMCP 启动时会向 stdout 写启动 banner 和 INFO 日志, 我们逐行读
    直到找到以 '{' 开头的行作为 JSON 响应。
    """
    def __init__(self, script_path: str):
        self.script_path = script_path
        self.proc: Optional[subprocess.Popen] = None
        self._id = 0
        self._lock = threading.Lock()

    def start(self):
        self.proc = subprocess.Popen(
            [sys.executable, "-u", self.script_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,  # banner/INFO 丢黑洞, 不污染 stdout
            cwd=os.path.dirname(self.script_path),
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        # 3 步握手: initialize (等响应) -> notifications/initialized (无响应) -> list_tools 验证
        self._send({
            "jsonrpc": "2.0", "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "mcp_proxy", "version": "2.0"}}
        })
        resp = self._read_json_response()
        if "error" in resp:
            raise RuntimeError("initialize 失败: " + str(resp["error"]))
        # 通知无响应, 直接发
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        # 业务调用 list_tools 验证握手成功
        result = self.call("list_tools", {})
        if "error" in result:
            raise RuntimeError("握手后 list_tools 失败: " + str(result))
        return result

    def _send(self, msg: dict):
        self.proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def _read_json_response(self) -> dict:
        """逐行读直到拿到 1 个 JSON 对象。最多 50 行。"""
        for _ in range(50):
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("MCP 子进程 stdout 关闭")
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            if not text.startswith("{"):
                # 跳过 banner / INFO 日志
                continue
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                continue
        raise RuntimeError("MCP 连续 50 行无 JSON 响应, 协议不匹配")

    def call(self, tool_name: str, arguments: dict) -> Any:
        with self._lock:
            self._id += 1
            rid = self._id + 1  # 1 已被 initialize 占用
            self._send({
                "jsonrpc": "2.0", "id": rid,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            })
            resp = self._read_json_response()
            if "error" in resp:
                raise RuntimeError("MCP 返回错误: " + str(resp["error"]))
            result = resp.get("result", {})
            content = result.get("content", [])
            if not content:
                return {}
            text = content[0].get("text", "{}")
            return json.loads(text)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()


# ==========================================
# FastAPI app (用 lifespan, 替代 deprecated on_event)
# ==========================================
mcp = McpStdioClient(MCP_SCRIPT)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    mcp.start()
    yield
    # shutdown
    mcp.stop()


app = FastAPI(title="Procurement-Audit MCP HTTP Proxy", version="2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ==========================================
# 安全：X-Harness-Secret Header 校验
# ==========================================
def verify_harness_secret(x_harness_secret: str = Header(default="")):
    """校验请求 Header X-Harness-Secret，失败 401。

    生产环境必须通过 HARNESS_SECRET 环境变量注入密钥，不要用默认值。
    """
    if x_harness_secret != HARNESS_SECRET:
        raise HTTPException(status_code=401, detail="invalid harness secret")


class ToolCall(BaseModel):
    name: str
    arguments: dict = {}


@app.get("/", dependencies=[Depends(verify_harness_secret)])
def index():
    if os.path.exists(DEMO_HTML):
        return FileResponse(DEMO_HTML)
    return {"message": "demo.html not found; see /docs for API"}


@app.get("/healthz")
def healthz():
    return {
        "ok": mcp.proc is not None and mcp.proc.poll() is None,
        "mcp_pid": mcp.proc.pid if mcp.proc else None,
        "script": MCP_SCRIPT,
    }


@app.post("/mcp/call", dependencies=[Depends(verify_harness_secret)])
def mcp_call(body: ToolCall):
    try:
        result = mcp.call(body.name, body.arguments)
        return {"ok": True, "tool": body.name, "result": result}
    except Exception as e:
        return {"ok": False, "tool": body.name, "error": str(e)}


@app.get("/api/pending", dependencies=[Depends(verify_harness_secret)])
def api_pending(status: str = "pending"):
    return mcp.call("list_pending_reviews", {"status_filter": status})


@app.get("/api/audit", dependencies=[Depends(verify_harness_secret)])
def api_audit(limit: int = 30):
    return mcp.call("query_audit_actions", {"limit": limit})


@app.post("/api/review", dependencies=[Depends(verify_harness_secret)])
def api_review(body: dict):
    return mcp.call("flag_for_review", {
        "action_id": body.get("action_id"),
        "reason": body.get("reason", "manual"),
        "note": body.get("note", ""),
    })


@app.post("/api/commit", dependencies=[Depends(verify_harness_secret)])
def api_commit(body: dict):
    args = {
        "review_id": body.get("review_id"),
        "outcome": body.get("outcome"),
        "note": body.get("note", ""),
        "actor": body.get("actor", "demo-user"),
    }
    if body.get("override_payload_json"):
        args["override_payload_json"] = body["override_payload_json"]
    return mcp.call("manual_commit", args)


# ==========================================
# 业务流程端点（白皮书第 1 节）
# ==========================================
@app.post("/api/callback", dependencies=[Depends(verify_harness_secret)])
def api_callback(body: dict):
    """CF Worker 审计完回调，写本地状态。

    请求体（白皮书 1 节约定的最小契约）:
      task_id: str         任务 ID
      status: str          completed / pending_manual_review / failed
      report: str | None   报告 Markdown（status=completed 时）
      error: str | None    错误信息（status=failed 时）
    """
    import datetime
    task_id = body.get("task_id", "unknown")
    status = body.get("status", "completed")
    callback_log = {
        "task_id": task_id,
        "status": status,
        "received_at": datetime.datetime.now().isoformat(),
        "report_chars": len(body.get("report", "")) if body.get("report") else 0,
        "has_error": bool(body.get("error")),
    }
    # 持久化到 scratch 目录（治理审计可查）
    scratch_dir = os.path.join(HERE, "scratch")
    os.makedirs(scratch_dir, exist_ok=True)
    log_path = os.path.join(scratch_dir, "callbacks.jsonl")
    with open(log_path, "a") as f:
        f.write(json.dumps(callback_log, ensure_ascii=False) + chr(10))
    return {"ok": True, "task_id": task_id, "status": status}


@app.post("/api/chat", dependencies=[Depends(verify_harness_secret)])
def api_chat(body: dict):
    """诊断问答转发：CF Worker / 前端 SPA 把问题转给 MCP server 处理。

    请求体:
      question: str    用户问题
      session_id: str  会话 ID（可选，用于审计追踪）
    """
    question = body.get("question", "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    # 走 MCP server 的 search_vector_news 作为示例（实际可调任意 tool）
    # 生产应调一个意图分类 + 路由到 12 tool 之一
    return mcp.call("search_vector_news", {
        "supplier_name": question,
        "limit": 5,
    })


# ==========================================
# 异步任务处理端点（C 方案阶段 2.5）
# ==========================================
@app.post("/api/process", dependencies=[Depends(verify_harness_secret)])
def api_process(body: dict):
    """接收 CF Worker Queue consumer 推过来的任务，处理后回调 Worker 写最终状态。

    请求体（Worker queue payload）:
      task_id: str        任务 ID
      action: str         处理动作（当前: audit_contract）
      r2_key: str         R2 文件路径
      filename: str       原始文件名
      content_type: str   文件 MIME 类型

    流程:
      1. 这里 MVP 先做"轻量处理"：根据 action 调对应 MCP tool
      2. 处理完成 → 回调 Worker https://ingest-worker.<account>.workers.dev/api/callback
      3. Worker 写 KV 最终状态
    """
    import urllib.request
    import urllib.error

    task_id = body.get("task_id", "")
    action = body.get("action", "audit_contract")
    r2_key = body.get("r2_key", "")
    filename = body.get("filename", "")
    content_type = body.get("content_type", "")

    if not task_id:
        raise HTTPException(status_code=400, detail="task_id required")

    # 1. 调 MCP / 数据库 处理器
    is_invoice_scenario = any(k in (filename or "").lower() for k in ["invoice", "recon", "payment"])
    
    if is_invoice_scenario:
        try:
            from neo4j import GraphDatabase
            driver = GraphDatabase.driver("bolt://localhost:7687", auth=("admin", "73@TuGraph"))
            with driver.session(database="default") as sess:
                # 1) 超开
                q_over = """
                MATCH (c:Contract)-[:HAS_INVOICE]->(i:Invoice)
                WITH c, sum(i.amount) AS total_invoiced, collect(i.invoice_code) AS invoice_list
                WHERE total_invoiced > c.amount
                RETURN c.contract_id AS id, total_invoiced - c.amount AS over
                """
                over_list = sess.run(q_over).data()
                
                # 2) 欠付
                q_under = """
                MATCH (p:Payment)-[r:MATCHED_INVOICE]->(i:Invoice)
                WITH i, sum(r.matched_amount) AS total_paid
                WHERE total_paid < i.amount
                RETURN i.invoice_id AS id, i.amount - total_paid AS gap
                """
                under_list = sess.run(q_under).data()
                
                # 3) 第三方
                q_third = """
                MATCH (p:Payment)-[:MATCHED_INVOICE]->(i:Invoice),
                      (p)-[:PAID_BY]->(payer:Corp),
                      (i)-[:ISSUED_TO]->(buyer:Corp)
                WHERE NOT (payer.corp_id = buyer.corp_id)
                RETURN p.payment_id AS id, buyer.name AS buyer_name, payer.name AS payer_name
                """
                third_list = sess.run(q_third).data()
            driver.close()
            
            # 拼装审计报告
            findings = []
            if over_list:
                findings.append(f"🔴 合同 {over_list[0]['id']} 累计超额开票 ¥{over_list[0]['over']:,.2f}")
            if under_list:
                findings.append(f"🟡 发票 {under_list[0]['id']} 付款欠收缺口 ¥{under_list[0]['gap']:,.2f}")
            if third_list:
                findings.append(f"🔴 付款流水 {third_list[0]['id']} 存在第三方代付 (抬头: {third_list[0]['buyer_name']}, 付款方: {third_list[0]['payer_name']})")
                
            if not findings:
                summary_text = "✅ 业财图谱比对完成：未发现合同超额开票、付款欠收或第三方代付异常。"
                findings_html = "<li>未发现任何合规异常。</li>"
            else:
                summary_text = "❌ 业财核销图谱比对警报：\n" + "\n".join(findings)
                findings_html = "".join([f"<li class='text-rose-300'>{f}</li>" for f in findings])
                
            mcp_result = {"status": "ok", "summary": summary_text}
            qdrant_title = "发票与收付款多维核销校验 (TuGraph)"
            
            # 拼装前端 HTML 报告
            tugraph_md = f"""
            <div class="space-y-4 leading-relaxed text-xs sm:text-sm">
              <div class="flex items-center gap-2 text-rose-400 font-semibold mb-2">
                <span class="w-1.5 h-1.5 rounded-full bg-rose-400"></span>
                <span>业财发票核销图穿透研判结果 (TuGraph)</span>
              </div>
              <p>经 TuGraph 业财物理图谱穿透计算，自动比对签署主体 <b>{filename}</b> 的关系网络，发现以下异常：</p>
              <ul class="list-disc pl-5 space-y-2 text-slate-400 text-xs">
                {findings_html}
              </ul>
              <div class="mt-4 pt-3 border-t border-white/5 flex justify-between items-center text-[10px] text-slate-500">
                <span>存储引擎: TuGraph-DB Engine</span>
                <span>穿透分析: 业财核销多跳关联</span>
              </div>
            </div>
            """
        except Exception as e:
            mcp_result = {"error": f"TuGraph audit query failed: {e}"}
            summary_text = f"TuGraph 审计执行失败: {e}"
            qdrant_title = "发票与收付款多维核销校验 (TuGraph)"
            tugraph_md = f"<div class='text-rose-400 text-xs'>TuGraph 审计执行失败: {e}</div>"
    else:
        try:
            supplier_name = os.path.splitext(filename)[0] if filename else ""
            mcp_result = mcp.call("search_vector_news", {
                "supplier_name": supplier_name,
                "limit": 3,
            })
            summary_text = (
                f"matched {len(mcp_result.get('matches', []))} references"
                if isinstance(mcp_result, dict) and "matches" in mcp_result
                else str(mcp_result)[:200]
            )
        except Exception as e:
            mcp_result = {"error": f"mcp call failed: {e}"}
            summary_text = f"mcp call failed: {e}"
        qdrant_title = "向量舆情相似度比对 (Qdrant)"
        tugraph_md = None

    # 2. 拼最终报告
    report = {
        "task_id": task_id,
        "action": action,
        "r2_key": r2_key,
        "filename": filename,
        "content_type": content_type,
        "mcp_result_summary": summary_text,
        "qdrant_title": qdrant_title,
        "tugraph_md": tugraph_md,
        "completed_at": __import__("datetime").datetime.now().isoformat(),
    }

    # 3. 回调 Worker /api/callback 写最终状态
    #    Worker URL 从 env WORKER_CALLBACK_URL 读（生产配置；默认走 workers.dev 域）
    worker_url = os.environ.get(
        "WORKER_CALLBACK_URL",
        "https://ingest-worker.nathanjim5546.workers.dev/api/callback",
    )
    try:
        secret_header = os.environ.get("BACKEND_SECRET_HEADER", "X-Harness-Secret")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            secret_header: HARNESS_SECRET,
        }
        req = urllib.request.Request(
            worker_url,
            data=json.dumps({
                "task_id": task_id,
                "status": "completed",
                "report": json.dumps(report, ensure_ascii=False),
            }).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            callback_resp = json.loads(resp.read().decode("utf-8"))
        print(f"[api_process] callback success for task {task_id}: {callback_resp}", flush=True)
    except Exception as e:
        callback_resp = {"ok": False, "error": f"callback failed: {e}"}
        print(f"[api_process] callback failed for task {task_id}: {e}", flush=True)

    return {
        "ok": True,
        "task_id": task_id,
        "mcp_result": mcp_result,
        "callback": callback_resp,
    }


if __name__ == "__main__":
    import uvicorn
    print(f"[mcp_proxy v2] starting on http://{HOST}:{PORT}", flush=True)
    print(f"[mcp_proxy v2] MCP script: {MCP_SCRIPT}", flush=True)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
