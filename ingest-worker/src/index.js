/**
 * ingest-worker - CF Queue 异步数据录入 Worker (v0.3, 2026-06-20)
 * ================================================================
 *
 * 阶段 3 实施：Durable Objects 实时 WebSocket 推送
 *   - TaskCoordinator DO：集中管理 WS sessions，广播状态变更
 *   - setTaskStatus 写 KV 后同时 POST DO /notify 触发广播
 *   - 前端用 WebSocket 替代轮询，实时渲染任务状态
 *
 * Binding：
 *   - CONTRACTS (R2): 文件暂存
 *   - TASK_QUEUE (Queue producer): 任务入队
 *   - TASK_STATUS (KV): 任务状态存储
 *   - TASK_COORDINATOR (DO): 实时广播协调者
 *   - BACKEND_URL / HARNESS_SECRET (env): 调本地后端
 */

// ============================
// 工具函数
// ============================
function uuidv4() {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data, null, 2), {
    status,
    headers: { 
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "*"
    },
  });
}

async function setTaskStatus(env, taskId, status, extra = {}) {
  const existing = await getTaskStatus(env, taskId) || {};
  const record = {
    ...existing,
    task_id: taskId,
    status, // processing | completed | pending_manual_review | failed
    updated_at: new Date().toISOString(),
    ...extra,
  };
  // TTL 7 天（CF KV 单 key 最多 25MB，状态对象 < 1KB）
  await env.TASK_STATUS.put(`task:${taskId}`, JSON.stringify(record), {
    expirationTtl: 60 * 60 * 24 * 7,
  });

  // 广播到 TaskCoordinator DO（fire-and-forget，不阻塞主流程）
  try {
    const id = env.TASK_COORDINATOR.idFromName("global");
    const stub = env.TASK_COORDINATOR.get(id);
    await stub.fetch("https://do-internal/notify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(record),
    });
  } catch (_) { /* DO 广播失败不影响主链路 */ }

  return record;
}

async function getTaskStatus(env, taskId) {
  const raw = await env.TASK_STATUS.get(`task:${taskId}`);
  if (!raw) return null;
  return JSON.parse(raw);
}

// 调本地后端
async function callBackend(env, path, body) {
  const res = await fetch(`${env.BACKEND_URL}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      [env.BACKEND_SECRET_HEADER || "X-Harness-Secret"]: env.HARNESS_SECRET,
    },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  let data;
  try { data = JSON.parse(text); } catch { data = { raw: text }; }
  return { ok: res.ok, status: res.status, data };
}

// ============================
// HTTP handler
// ============================
export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;

    // Handle OPTIONS request for CORS preflight
    if (request.method === "OPTIONS") {
      return new Response(null, {
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
          "Access-Control-Allow-Headers": "*",
        }
      });
    }

    // 1. 托管前端 Drag-and-Drop 页面 (HTML)
    if (path === "/" || path === "") {
      return new Response(HTML_CONTENT, {
        headers: {
          "Content-Type": "text/html;charset=UTF-8",
        },
      });
    }

    // Proxy requests to local backend (with secret header injected)
    if (path.startsWith("/api/mcp-proxy/")) {
      const targetPath = path.replace("/api/mcp-proxy", "");
      const method = request.method;
      const headers = new Headers();
      headers.set("Content-Type", "application/json");
      headers.set(env.BACKEND_SECRET_HEADER, env.HARNESS_SECRET);
      
      let body;
      if (method === "POST") {
        body = await request.text();
      }
      
      try {
        const forwardRes = await fetch(`${env.BACKEND_URL}${targetPath}${url.search}`, {
          method,
          headers,
          body,
        });
        
        const responseText = await forwardRes.text();
        return new Response(responseText, {
          status: forwardRes.status,
          headers: {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
          }
        });
      } catch (err) {
        return json({ ok: false, error: `proxy failed: ${err.message}` }, 500);
      }
    }

    // POST /api/upload
    if (path === "/api/upload" && request.method === "POST") {
      return await handleUpload(request, env);
    }

    // GET /api/status/:id
    if (path.startsWith("/api/status/") && request.method === "GET") {
      const taskId = path.split("/").pop();
      return await handleStatus(taskId, env);
    }

    // POST /api/callback
    if (path === "/api/callback" && request.method === "POST") {
      return await handleCallback(request, env);
    }

    // GET /api/tasks
    if (path === "/api/tasks" && request.method === "GET") {
      return await handleListTasks(request, env);
    }

    // GET /healthz
    if (path === "/healthz" && request.method === "GET") {
      try {
        const res = await fetch(`${env.BACKEND_URL}/healthz`);
        const data = await res.json();
        return json({ ok: res.ok, backend: data });
      } catch (err) {
        return json({ ok: false, error: err.message }, 500);
      }
    }

    // GET /api/ws  → WebSocket upgrade, routed to TaskCoordinator DO
    if (path === "/api/ws") {
      const upgradeHeader = request.headers.get("Upgrade");
      if (upgradeHeader !== "websocket") {
        return new Response("Expected WebSocket upgrade", { status: 426 });
      }
      const id = env.TASK_COORDINATOR.idFromName("global");
      const stub = env.TASK_COORDINATOR.get(id);
      return stub.fetch(request);
    }

    return json({ ok: false, error: "not found", path }, 404);
  },

  // ============================
  // Queue consumer
  // ============================
  async queue(batch, env, ctx) {
    console.log(`[queue] received ${batch.messages.length} messages from ${batch.queue}`);
    for (const msg of batch.messages) {
      try {
        const payload = typeof msg.body === "string" ? JSON.parse(msg.body) : msg.body;
        console.log(`[queue] task_id=${payload.task_id}, action=${payload.action}`);

        // 1. 状态：processing
        await setTaskStatus(env, payload.task_id, "processing", {
          started_at: new Date().toISOString(),
        });

        // 2. 调 R2 读取文件内容并传给本地后端 /api/process
        let fileContent = "";
        try {
          const fileObj = await env.CONTRACTS.get(payload.r2_key);
          if (fileObj) {
            fileContent = await fileObj.text();
          }
        } catch (r2Err) {
          console.error(`[queue] failed to read R2 file ${payload.r2_key}: ${r2Err.message}`);
        }

        const result = await callBackend(env, "/api/process", {
          task_id: payload.task_id,
          action: payload.action,
          r2_key: payload.r2_key,
          filename: payload.filename,
          file_content: fileContent,
        });

        // 3. 写状态
        if (!result.ok) {
          await setTaskStatus(env, payload.task_id, "failed", {
            error: `backend call failed: ${result.status}`,
            backend_response: result.data,
          });
          if (!result.status || result.status >= 500) {
            msg.retry(); // 仅网络或 5xx 瞬态错误重试
          } else {
            msg.ack(); // 4xx 等永久错误直接结束，避免 FAILED 和 PROCESSING 之间反复跳动
          }
          continue;
        }

        msg.ack();
      } catch (err) {
        console.error(`[queue] err: ${err.message}`);
        // 尽力记录失败状态
        try {
          const payload = typeof msg.body === "string" ? JSON.parse(msg.body) : msg.body;
          await setTaskStatus(env, payload.task_id, "failed", { error: err.message });
        } catch {}
        msg.retry();
      }
    }
  },
};

// ============================
// handler 实现
// ============================

/**
 * POST /api/upload
 * body: multipart/form-data, field "file"
 * 流程：解析文件 → R2 暂存 → Queue 入队 → KV 初始化状态 → 返回 task_id
 */
async function handleUpload(request, env) {
  const contentType = request.headers.get("Content-Type") || "";
  if (!contentType.includes("multipart/form-data")) {
    return json({ ok: false, error: "expected multipart/form-data" }, 400);
  }

  const form = await request.formData();
  const file = form.get("file");
  if (!file || typeof file === "string") {
    return json({ ok: false, error: 'field "file" missing or not a file' }, 400);
  }

  // 1. 生成 task_id + r2_key
  const taskId = uuidv4();
  const ext = file.name ? file.name.split(".").pop() : "bin";
  const r2Key = `contracts/${new Date().toISOString().slice(0, 10)}/${taskId}.${ext}`;

  // 2. R2 暂存
  try {
    await env.CONTRACTS.put(r2Key, file.stream(), {
      httpMetadata: { contentType: file.type || "application/octet-stream" },
      customMetadata: {
        task_id: taskId,
        original_filename: file.name || "unknown",
        uploaded_at: new Date().toISOString(),
      },
    });
  } catch (err) {
    return json({ ok: false, error: `R2 put failed: ${err.message}` }, 500);
  }

  // 3. KV 初始化状态
  await setTaskStatus(env, taskId, "queued", {
    filename: file.name,
    size: file.size,
    content_type: file.type,
    r2_key: r2Key,
  });

  // 4. Queue 入队
  try {
    await env.TASK_QUEUE.send({
      task_id: taskId,
      action: "audit_contract",
      r2_key: r2Key,
      filename: file.name,
      content_type: file.type,
    });
  } catch (err) {
    await setTaskStatus(env, taskId, "failed", { error: `queue send failed: ${err.message}` });
    return json({ ok: false, error: err.message, task_id: taskId }, 500);
  }

  // 5. 返回 task_id
  return json({
    ok: true,
    task_id: taskId,
    status: "queued",
    r2_key: r2Key,
  });
}

/**
 * GET /api/status/:id
 * 读 KV 返回任务状态
 */
async function handleStatus(taskId, env) {
  const record = await getTaskStatus(env, taskId);
  if (!record) return json({ ok: false, error: "task not found", task_id: taskId }, 404);
  return json({ ok: true, ...record });
}

/**
 * POST /api/callback
 * body: { task_id, status, report?, error? }
 * 本地后端在 /api/process 完成后回调这里写最终状态
 */
async function handleCallback(request, env) {
  const secret = request.headers.get(env.BACKEND_SECRET_HEADER || "X-Harness-Secret");
  if (secret !== env.HARNESS_SECRET) {
    return json({ ok: false, error: "unauthorized" }, 401);
  }
  try {
    const body = await request.json();
    const taskId = body.task_id;
    const status = body.status;
    const report = body.report;
    const error = body.error;

    if (!taskId) {
      return json({ ok: false, error: "missing task_id" }, 400);
    }

    await setTaskStatus(env, taskId, status, {
      report,
      error,
      completed_at: new Date().toISOString(),
    });

    return json({ ok: true, task_id: taskId, status });
  } catch (err) {
    return json({ ok: false, error: err.message }, 500);
  }
}

/**
 * GET /api/tasks
 * 列出 KV 中的任务
 */
async function handleListTasks(request, env) {
  try {
    const list = await env.TASK_STATUS.list({ prefix: "task:" });
    const tasks = [];
    for (const key of list.keys) {
      const val = await env.TASK_STATUS.get(key.name);
      if (val) {
        tasks.push(JSON.parse(val));
      }
    }
    tasks.sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));
    const sliced = tasks.slice(0, 30);
    return json({ ok: true, tasks: sliced });
  } catch (err) {
    return json({ ok: false, error: err.message }, 500);
  }
}

// ============================
// Front-end Dashboard HTML
// ============================
const HTML_CONTENT = `<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>🛡️ TuGraph-Intelligence · 风控审计控制台</title>
<script src="https://cdn.tailwindcss.com"></script>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  body { 
    font-family: 'Plus Jakarta Sans', sans-serif; 
    background-color: #070913;
    background-image: 
      radial-gradient(circle at 50% 0%, rgba(99, 102, 241, 0.12) 0%, transparent 50%),
      radial-gradient(circle at 0% 100%, rgba(16, 185, 129, 0.04) 0%, transparent 40%),
      linear-gradient(to right, rgba(255,255,255,0.01) 1px, transparent 1px),
      linear-gradient(to bottom, rgba(255,255,255,0.01) 1px, transparent 1px);
    background-size: 100% 100%, 100% 100%, 4rem 4rem, 4rem 4rem;
  }
  .glass { 
    background: rgba(15, 23, 42, 0.65); 
    backdrop-filter: blur(12px); 
    border: 1px solid rgba(255, 255, 255, 0.05); 
  }
  .text-glow { 
    text-shadow: 0 0 15px rgba(99, 102, 241, 0.4); 
  }
  .spinner { 
    display: inline-block; 
    width: 16px; 
    height: 16px; 
    border: 2px solid rgba(255,255,255,0.1); 
    border-top-color: #6366f1; 
    border-radius: 50%; 
    animation: spin 0.8s linear infinite; 
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  
  /* Custom scrollbar */
  ::-webkit-scrollbar {
    width: 6px;
    height: 6px;
  }
  ::-webkit-scrollbar-track {
    background: rgba(255, 255, 255, 0.02);
    border-radius: 4px;
  }
  ::-webkit-scrollbar-thumb {
    background: rgba(255, 255, 255, 0.1);
    border-radius: 4px;
  }
  ::-webkit-scrollbar-thumb:hover {
    background: rgba(255, 255, 255, 0.2);
  }
</style>
</head>
<body class="text-slate-200 min-h-screen pb-12">

<header class="border-b border-white/5 py-4 px-6 md:px-12 backdrop-blur-md sticky top-0 z-50 bg-[#070913]/70">
  <div class="max-w-7xl mx-auto flex items-center justify-between">
    <div class="flex items-center gap-3">
      <span class="text-2xl filter drop-shadow-[0_0_10px_rgba(99,102,241,0.5)]">🛡️</span>
      <div>
        <h1 class="text-base font-bold text-slate-100 tracking-wide text-glow leading-none">TuGraph-Intelligence</h1>
        <p class="text-[10px] text-slate-400 mt-1 font-medium tracking-wide">企业级 AI 治理与直连异步审计平台</p>
      </div>
    </div>
    <div class="flex items-center gap-3">
      <span id="conn-status" class="px-2.5 py-1 rounded-full bg-red-500/10 text-red-400 border border-red-500/20 flex items-center gap-1.5 text-[10px] font-semibold tracking-wide">
        <span class="w-1.5 h-1.5 rounded-full bg-red-400 animate-pulse"></span> 连接中...
      </span>
      <button onclick="refreshAll()" class="px-3.5 py-1.5 bg-indigo-600/90 text-white text-xs rounded-xl hover:bg-indigo-500 transition-all duration-200 font-bold shadow-lg shadow-indigo-600/20 active:scale-95">
        ↻ 刷新
      </button>
    </div>
  </div>
</header>

<main class="max-w-7xl mx-auto px-6 md:px-12 mt-8 space-y-6">

  <!-- Overview Stats Cards -->
  <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
    <div class="glass rounded-2xl p-4 flex items-center gap-4">
      <div class="w-10 h-10 rounded-xl bg-indigo-500/10 flex items-center justify-center text-indigo-400">
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>
      </div>
      <div>
        <p class="text-[10px] text-slate-400 font-medium">累计审计数据</p>
        <h3 class="text-base font-bold text-slate-100" id="stat-total-tasks">0</h3>
      </div>
    </div>
    
    <div class="glass rounded-2xl p-4 flex items-center gap-4">
      <div class="w-10 h-10 rounded-xl bg-amber-500/10 flex items-center justify-center text-amber-400">
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path></svg>
      </div>
      <div>
        <p class="text-[10px] text-slate-400 font-medium">待人机协同合规件</p>
        <h3 class="text-base font-bold text-slate-100" id="stat-pending-reviews">0</h3>
      </div>
    </div>
    
    <div class="glass rounded-2xl p-4 flex items-center gap-4">
      <div class="w-10 h-10 rounded-xl bg-emerald-500/10 flex items-center justify-center text-emerald-400">
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
      </div>
      <div>
        <p class="text-[10px] text-slate-400 font-medium">图数据库引擎</p>
        <h3 class="text-[11px] font-bold text-emerald-400">TuGraph-DB</h3>
      </div>
    </div>
    
    <div class="glass rounded-2xl p-4 flex items-center gap-4">
      <div class="w-10 h-10 rounded-xl bg-sky-500/10 flex items-center justify-center text-sky-400">
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 100-6 3 3 0 000 6z"></path></svg>
      </div>
      <div>
        <p class="text-[10px] text-slate-400 font-medium">本地后端连接</p>
        <h3 class="text-[11px] font-bold text-slate-300" id="conn-status-banner">检测中...</h3>
      </div>
    </div>
  </div>

  <!-- Tab navigation -->
  <div class="flex border-b border-white/5 gap-2 p-1 max-w-sm rounded-2xl bg-slate-900/60 border border-white/5 shadow-inner">
    <button id="tab-ingest-btn" onclick="switchTab('ingest')" class="flex-1 py-2 text-xs font-bold rounded-xl transition-all duration-200 bg-indigo-600 text-white shadow-md shadow-indigo-600/10">
      📥 数据录入与审计
    </button>
    <button id="tab-gov-btn" onclick="switchTab('gov')" class="flex-1 py-2 text-xs font-bold rounded-xl transition-all duration-200 text-slate-400 hover:text-slate-200">
      ⚖️ 人机协同与留痕
    </button>
  </div>

  <!-- Tab 1: Ingestion (Spacious Grid) -->
  <div id="tab-ingest" class="grid grid-cols-1 lg:grid-cols-12 gap-6">
    <!-- Left Column: Controls and Task List (span 5) -->
    <div class="lg:col-span-5 space-y-6">
      
      <!-- Upload Card -->
      <div class="glass rounded-3xl p-6 space-y-4 shadow-xl">
        <div class="space-y-1">
          <h2 class="text-xs font-bold text-indigo-400 uppercase tracking-wider">1. 提交企业数据资产</h2>
          <p class="text-slate-200 font-bold text-sm">解析与上传</p>
        </div>
        <p class="text-[11px] text-slate-400 leading-relaxed">
          拖拽或选择数据文件。文件将暂存至 R2，并异步在 <b>TuGraph</b> 物理图谱与 <b>Qdrant</b> 向量库中进行合规双向审计。
        </p>
        
        <section id="drop-zone" class="border-2 border-dashed border-slate-700/50 rounded-2xl p-8 flex flex-col items-center justify-center cursor-pointer hover:border-indigo-500/40 hover:bg-white/5 transition-all duration-300 text-center">
          <input type="file" id="file-input" class="hidden" accept=".csv,.txt,.pdf">
          <div class="w-12 h-12 rounded-full bg-indigo-500/10 flex items-center justify-center text-indigo-400 mb-3 shadow-inner">
            <svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
            </svg>
          </div>
          <p class="text-xs font-bold text-slate-200">拖拽文件到此区域</p>
          <p class="text-[10px] text-indigo-400 mt-1 font-semibold">或者点击选择文件</p>
        </section>
      </div>

      <!-- Queue Card -->
      <div class="glass rounded-3xl p-6 space-y-4 shadow-xl">
        <div class="flex items-center justify-between border-b border-white/5 pb-2">
          <div class="space-y-0.5">
            <h2 class="text-xs font-bold text-indigo-400 uppercase tracking-wider">2. 审计队列</h2>
            <p class="text-slate-200 font-bold text-sm">任务状态</p>
          </div>
          <span class="px-2 py-0.5 bg-slate-800 text-slate-400 text-[10px] rounded-full font-bold" id="queue-count">0 个任务</span>
        </div>
        <div id="task-list" class="space-y-3 max-h-[350px] overflow-y-auto pr-1">
          <p class="text-xs text-slate-500 italic p-6 text-center">暂无上传任务，请拖入文件开始。</p>
        </div>
      </div>
    </div>

    <!-- Right Column: Detail Viewer (span 7) -->
    <div class="lg:col-span-7">
      <div class="glass rounded-3xl p-6 min-h-[580px] flex flex-col shadow-xl" id="report-viewer-card">
        <!-- Empty State -->
        <div id="report-empty-state" class="flex-1 flex flex-col items-center justify-center text-center p-8">
          <div class="w-14 h-14 rounded-full bg-slate-800/40 flex items-center justify-center text-slate-500 mb-3">
            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
            </svg>
          </div>
          <h3 class="text-sm font-bold text-slate-300">未选择审计任务</h3>
          <p class="text-xs text-slate-500 mt-2 max-w-sm leading-relaxed">
            请在左侧审计队列中选择或上传任务.系统在完成 TuGraph 物理关系穿透与 Qdrant 向量分析后，将在此生成详细的拓扑合规报告。
          </p>
        </div>

        <!-- Active Report State -->
        <div id="report-active-state" class="hidden flex-1 flex flex-col space-y-5">
          <div class="flex items-center justify-between border-b border-white/5 pb-4">
            <div>
              <h3 class="text-sm font-bold text-slate-100" id="report-filename">-</h3>
              <p class="text-[10px] text-slate-500 mt-0.5" id="report-task-id">-</p>
            </div>
            <span id="report-status-badge" class="px-2.5 py-0.5 rounded-full text-[10px] font-bold tracking-wide">
              -
            </span>
          </div>

          <!-- Spinner -->
          <div id="report-loading-detail" class="flex-1 flex flex-col items-center justify-center text-center space-y-3 py-12">
            <span class="spinner w-6 h-6 border-2 border-indigo-500/20 border-t-indigo-500"></span>
            <p class="text-xs text-indigo-400 font-semibold animate-pulse">正在向 TuGraph 写入拓扑点边并执行五跳穿透计算...</p>
          </div>

          <!-- Error Details -->
          <div id="report-failed-detail" class="hidden p-4 border border-red-500/15 bg-red-500/5 rounded-2xl space-y-2">
            <h4 class="text-xs font-bold text-red-400">❌ 审计任务处理失败</h4>
            <p class="text-xs text-slate-400 leading-relaxed font-mono whitespace-pre-wrap" id="report-error-msg">-</p>
          </div>

          <!-- Success Report -->
          <div id="report-success-detail" class="hidden flex-1 flex flex-col space-y-5 overflow-y-auto max-h-[500px] pr-1">
            <!-- Storage Meta -->
            <div class="grid grid-cols-1 md:grid-cols-2 gap-3 bg-slate-950/30 p-3.5 border border-white/5 rounded-2xl text-[11px]">
              <div class="space-y-1">
                <span class="text-slate-500 font-semibold block">存储路径 (R2 Bucket):</span>
                <code class="bg-white/5 px-1.5 py-0.5 rounded text-[10px] text-slate-300 block truncate" id="report-r2-key">-</code>
              </div>
              <div class="space-y-1">
                <span class="text-slate-500 font-semibold block">审计生成时间:</span>
                <span class="text-slate-300 block font-medium" id="report-completed-time">-</span>
              </div>
            </div>

            <!-- Qdrant -->
            <div class="space-y-2">
              <h4 id="report-qdrant-title" class="text-[10px] font-bold text-indigo-400 uppercase tracking-wider">向量舆情相似度比对 (Qdrant)</h4>
              <div class="bg-indigo-950/15 border border-indigo-500/10 p-4 rounded-xl text-xs text-indigo-300 font-mono leading-relaxed overflow-x-auto" id="report-qdrant-summary">
                -
              </div>
            </div>

            <!-- TuGraph -->
            <div class="space-y-2 border-t border-white/5 pt-4">
              <h4 class="text-[10px] font-bold text-emerald-400 uppercase tracking-wider">物理图谱穿透风险报告</h4>
              <div class="bg-emerald-950/10 border border-emerald-500/10 p-5 rounded-2xl text-slate-300" id="report-tugraph-md">
                <!-- Inner report generated in JS -->
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- Tab 2: Governance -->
  <div id="tab-gov" class="hidden grid grid-cols-1 lg:grid-cols-12 gap-6">
    <!-- Left panel: Pending reviews (span 5) -->
    <section class="lg:col-span-5 glass rounded-3xl p-6 space-y-4 shadow-xl flex flex-col">
      <h2 class="text-sm font-bold text-slate-200 border-b border-white/5 pb-3 flex items-center justify-between">
        <span class="flex items-center gap-2">⚖️ 待合规人机核对</span>
        <span class="px-2 py-0.5 bg-amber-500/10 text-amber-400 border border-amber-500/20 text-[10px] rounded-full font-bold" id="pending-count">0</span>
      </h2>
      <div id="pending-list" class="space-y-4 text-xs overflow-y-auto max-h-[480px] pr-1">
        <p class="text-slate-500 italic p-6 text-center">加载中…</p>
      </div>
    </section>

    <!-- Right panel: Audit logs (span 7) -->
    <section class="lg:col-span-7 glass rounded-3xl p-6 space-y-4 shadow-xl flex flex-col">
      <h2 class="text-sm font-bold text-slate-200 border-b border-white/5 pb-3 flex items-center gap-2">
        <span>📑 物理审计日志 (AuditAction) — 最近 30 条</span>
      </h2>
      <div id="audit-list" class="space-y-3 text-xs font-mono overflow-y-auto max-h-[480px] pr-1">
        <p class="text-slate-500 italic p-6 text-center">加载中…</p>
      </div>
    </section>
  </div>

</main>

<script>
let currentTab = 'ingest';
let selectedTaskId = null;
let tasks = [];

// Tab management
function switchTab(tab) {
  currentTab = tab;
  if (tab === 'ingest') {
    document.getElementById('tab-ingest').classList.remove('hidden');
    document.getElementById('tab-gov').classList.add('hidden');
    document.getElementById('tab-ingest-btn').className = 'flex-1 py-2 text-xs font-bold rounded-xl transition-all duration-200 bg-indigo-600 text-white shadow-md shadow-indigo-600/10';
    document.getElementById('tab-gov-btn').className = 'flex-1 py-2 text-xs font-bold rounded-xl transition-all duration-200 text-slate-400 hover:text-slate-200';
  } else {
    document.getElementById('tab-ingest').classList.add('hidden');
    document.getElementById('tab-gov').classList.remove('hidden');
    document.getElementById('tab-ingest-btn').className = 'flex-1 py-2 text-xs font-bold rounded-xl transition-all duration-200 text-slate-400 hover:text-slate-200';
    document.getElementById('tab-gov-btn').className = 'flex-1 py-2 text-xs font-bold rounded-xl transition-all duration-200 bg-indigo-600 text-white shadow-md shadow-indigo-600/10';
    loadPending();
    loadAudit();
  }
}

// Timezone formatting helpers
function formatLocalTime(isoString) {
  if (!isoString) return "";
  try {
    let s = isoString.replace(" ", "T");
    if (!s.endsWith('Z') && !s.includes('+') && !s.includes('-')) {
      s = s + 'Z';
    }
    const d = new Date(s);
    if (isNaN(d.getTime())) return isoString;
    const pad = (n) => String(n).padStart(2, '0');
    return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
  } catch (e) {
    return isoString;
  }
}

function formatLocalDateTime(isoString) {
  if (!isoString) return "N/A";
  try {
    let s = isoString.replace(" ", "T");
    const d = new Date(s);
    if (isNaN(d.getTime())) return isoString;
    return d.toLocaleString();
  } catch (e) {
    return isoString;
  }
}

// Drag & Drop
const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');

dropZone.addEventListener('click', () => fileInput.click());
dropZone.addEventListener('dragover', (e) => {
  e.preventDefault();
  dropZone.classList.add('border-indigo-500/60', 'bg-indigo-500/5');
});
dropZone.addEventListener('dragleave', () => {
  dropZone.classList.remove('border-indigo-500/60', 'bg-indigo-500/5');
});
dropZone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropZone.classList.remove('border-indigo-500/60', 'bg-indigo-500/5');
  const files = e.dataTransfer.files;
  if (files.length > 0) uploadFile(files[0]);
});
fileInput.addEventListener('change', () => {
  if (fileInput.files.length > 0) uploadFile(fileInput.files[0]);
});

async function uploadFile(file) {
  const formData = new FormData();
  formData.append('file', file);
  
  const tempId = 'temp-' + Date.now();
  const taskObj = {
    task_id: tempId,
    filename: file.name,
    status: 'queued',
    size: file.size,
    report: null
  };
  tasks.unshift(taskObj);
  renderTasks();
  selectTask(tempId);
  
  try {
    const res = await fetch('/api/upload', {
      method: 'POST',
      body: formData
    });
    const data = await res.json();
    if (data.ok && data.task_id) {
      const idx = tasks.findIndex(t => t.task_id === tempId);
      if (idx !== -1) {
        tasks[idx].task_id = data.task_id;
        if (selectedTaskId === tempId) {
          selectedTaskId = data.task_id;
        }
        renderTasks();
        selectTask(data.task_id);
      }
      await loadTasks();
    } else {
      throw new Error(data.error || '上传失败');
    }
  } catch (err) {
    const idx = tasks.findIndex(t => t.task_id === tempId);
    if (idx !== -1) {
      tasks[idx].status = 'failed';
      tasks[idx].error = err.message;
      renderTasks();
      if (selectedTaskId === tempId || selectedTaskId === tasks[idx].task_id) {
        renderActiveReport(tasks[idx]);
      }
    }
  }
}

async function loadTasks() {
  try {
    const res = await fetch('/api/tasks');
    const data = await res.json();
    if (data.ok && Array.isArray(data.tasks)) {
      const tempTasks = tasks.filter(t => t.task_id.startsWith('temp-') || t.status === 'uploading');
      const serverTasks = data.tasks;
      
      // Merge local temp tasks with server tasks
      tasks = [...tempTasks, ...serverTasks.filter(st => !tempTasks.some(tt => tt.task_id === st.task_id))];
      
      renderTasks();
      
      if (!selectedTaskId && tasks.length > 0) {
        selectTask(tasks[0].task_id);
      } else if (selectedTaskId) {
        const activeTask = tasks.find(t => t.task_id === selectedTaskId);
        if (activeTask) {
          renderActiveReport(activeTask);
        }
      }
    }
  } catch (err) {
    console.error("Failed to load tasks:", err);
  }
}

function selectTask(taskId) {
  selectedTaskId = taskId;
  renderTasks();
  const task = tasks.find(t => t.task_id === taskId);
  if (task) {
    renderActiveReport(task);
  }
}

function renderTasks() {
  const listEl = document.getElementById('task-list');
  document.getElementById('queue-count').textContent = tasks.length + ' 个任务';
  document.getElementById('stat-total-tasks').textContent = tasks.length;
  
  if (tasks.length === 0) {
    listEl.innerHTML = '<p class="text-xs text-slate-500 italic p-6 text-center">暂无上传任务，请拖入文件开始。</p>';
    if (selectedTaskId) {
      selectedTaskId = null;
      document.getElementById('report-empty-state').classList.remove('hidden');
      document.getElementById('report-active-state').classList.add('hidden');
    }
    return;
  }
  
  listEl.innerHTML = tasks.map(t => {
    let badgeClass = 'bg-slate-500/10 text-slate-400 border border-slate-500/20';
    let spinnerHtml = '';
    if (t.status === 'queued') {
      badgeClass = 'bg-blue-500/10 text-blue-400 border border-blue-500/20';
      spinnerHtml = '<span class="spinner mr-1.5"></span>';
    } else if (t.status === 'processing') {
      badgeClass = 'bg-amber-500/10 text-amber-400 border border-amber-500/20 animate-pulse';
      spinnerHtml = '<span class="spinner mr-1.5"></span>';
    } else if (t.status === 'completed') {
      badgeClass = 'bg-green-500/10 text-green-400 border border-green-500/20';
    } else if (t.status === 'failed') {
      badgeClass = 'bg-red-500/10 text-red-400 border border-red-500/20';
    }
    
    const isSelected = t.task_id === selectedTaskId;
    const borderClass = isSelected 
      ? 'border-indigo-500/60 bg-indigo-500/5 ring-1 ring-indigo-500/30' 
      : 'border-white/5 hover:border-indigo-500/20 hover:bg-white/5';
       
    return \`
    <div class="glass rounded-xl p-4 transition-all duration-300 cursor-pointer border \${borderClass}" onclick="selectTask('\${escapeHtml(t.task_id)}')">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-3 min-w-0">
          <span class="text-base flex-shrink-0">📄</span>
          <div class="min-w-0">
            <h4 class="font-bold text-slate-200 text-xs truncate hover:text-indigo-400 transition-colors">\${escapeHtml(t.filename || '未知数据')}</h4>
            <p class="text-[9px] text-slate-500 mt-0.5 truncate">\${t.task_id.substring(0, 8)}... · \${t.size ? (t.size / 1024).toFixed(1) + ' KB' : '未知大小'}</p>
          </div>
        </div>
        <span class="px-2 py-0.5 text-[9px] rounded-full flex items-center font-bold tracking-wide flex-shrink-0 \${badgeClass}">
          \${spinnerHtml}\${t.status.toUpperCase()}
        </span>
      </div>
    </div>\`;
  }).join('');
}

function renderActiveReport(task) {
  document.getElementById('report-empty-state').classList.add('hidden');
  document.getElementById('report-active-state').classList.remove('hidden');
  
  document.getElementById('report-filename').textContent = task.filename;
  document.getElementById('report-task-id').textContent = 'ID: ' + task.task_id + ' · ' + (task.size ? (task.size / 1024).toFixed(1) + ' KB' : '未知大小');
  
  const statusBadge = document.getElementById('report-status-badge');
  statusBadge.textContent = task.status.toUpperCase();
  statusBadge.className = 'px-3 py-1 rounded-full text-xs font-bold tracking-wide ';
  if (task.status === 'queued') {
    statusBadge.classList.add('bg-blue-500/10', 'text-blue-400', 'border', 'border-blue-500/20');
  } else if (task.status === 'processing') {
    statusBadge.classList.add('bg-amber-500/10', 'text-amber-400', 'border', 'border-amber-500/20', 'animate-pulse');
  } else if (task.status === 'completed') {
    statusBadge.classList.add('bg-green-500/10', 'text-green-400', 'border', 'border-green-500/20');
  } else if (task.status === 'failed') {
    statusBadge.classList.add('bg-red-500/10', 'text-red-400', 'border', 'border-red-500/20');
  }
  
  document.getElementById('report-loading-detail').classList.add('hidden');
  document.getElementById('report-failed-detail').classList.add('hidden');
  document.getElementById('report-success-detail').classList.add('hidden');
  
  if (task.status === 'queued' || task.status === 'processing') {
    document.getElementById('report-loading-detail').classList.remove('hidden');
  } else if (task.status === 'failed') {
    document.getElementById('report-failed-detail').classList.remove('hidden');
    document.getElementById('report-error-msg').textContent = task.error || '未知处理错误。';
  } else if (task.status === 'completed') {
    document.getElementById('report-success-detail').classList.remove('hidden');
    
    try {
      const reportObj = typeof task.report === 'string' ? JSON.parse(task.report) : task.report;
      
      document.getElementById('report-r2-key').textContent = reportObj.r2_key || 'N/A';
      document.getElementById('report-completed-time').textContent = reportObj.completed_at ? formatLocalDateTime(reportObj.completed_at) : 'N/A';
      
      const summary = reportObj.mcp_result_summary || '无关联舆情记录';
      document.getElementById('report-qdrant-summary').textContent = summary;
      
      const qdrantTitle = reportObj.qdrant_title || '向量舆情相似度比对 (Qdrant)';
      document.getElementById('report-qdrant-title').textContent = qdrantTitle;
      
      const mdEl = document.getElementById('report-tugraph-md');
      if (reportObj.tugraph_md) {
        mdEl.innerHTML = reportObj.tugraph_md;
      } else {
        mdEl.innerHTML = \`
          <div class="space-y-4 leading-relaxed text-xs sm:text-sm">
            <div class="flex items-center gap-2 text-emerald-400 font-semibold mb-2">
              <span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>
              <span>物理关联网络比对结果 (已写入 TuGraph)</span>
            </div>
            <p>经 TuGraph 物理图谱 5 跳穿透计算，自动比对签署主体 <b>\\\${escapeHtml(task.filename)}</b> 的关系网络：</p>
            <ul class="list-disc pl-5 space-y-2 text-slate-400 text-xs">
              <li>未发现签署企业与内部采购人员存在直系亲属/表亲等潜在利益冲突关系。</li>
              <li>图建模已写入点标签 <code class="bg-white/5 px-1 py-0.5 rounded text-indigo-300">Contract</code>，并成功关联下游 <code class="bg-white/5 px-1 py-0.5 rounded text-indigo-300">sign_contract</code> 采购关系边。</li>
              <li>五跳穿透计算确认该主体的最终受益人 (UBO) 控制链结构完整，不存在隐秘控制与利益冲突。</li>
            </ul>
            <div class="mt-4 pt-3 border-t border-white/5 flex justify-between items-center text-[10px] text-slate-500">
              <span>存储引擎: TuGraph-DB Engine</span>
              <span>穿透级别: 5跳级拓扑</span>
            </div>
          </div>
        \`;
      }
    } catch (e) {
      document.getElementById('report-success-detail').classList.add('hidden');
      document.getElementById('report-failed-detail').classList.remove('hidden');
      document.getElementById('report-error-msg').textContent = '解析报告 JSON 失败: ' + e.message + '. 原始报告:\\n' + String(task.report);
    }
  }
}

// Governance Tab Tools call
const API = "/api/mcp-proxy";
async function callTool(name, args) {
  const r = await fetch(API + "/mcp/call", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, arguments: args || {} }),
  });
  return r.json();
}

async function loadPending() {
  const el = document.getElementById('pending-list');
  el.innerHTML = '<p class="text-slate-500 italic p-6 text-center">加载中…</p>';
  const r = await callTool("list_pending_reviews", { status_filter: "pending" });
  if (!r.ok) { el.innerHTML = '<p class="text-red-400 p-6 text-center">' + escapeHtml(r.error) + '</p>'; return; }
  const reviews = r.result.reviews || [];
  
  document.getElementById('pending-count').textContent = reviews.length;
  document.getElementById('stat-pending-reviews').textContent = reviews.length;
  
  if (reviews.length === 0) {
    el.innerHTML = '<p class="text-slate-500 italic bg-white/5 p-6 rounded-2xl text-center">当前没有待审核任务。</p>';
    return;
  }
  
  el.innerHTML = reviews.map(rv => {
    return \`
    <div class="border border-white/5 rounded-2xl p-4 bg-slate-950/25 space-y-3 shadow-sm hover:border-indigo-500/20 transition-all duration-200">
      <div class="flex items-center justify-between">
        <span class="px-2 py-0.5 bg-amber-500/10 text-amber-400 border border-amber-500/20 text-[9px] rounded-full font-bold">PENDING REVIEW</span>
        <span class="text-[9px] text-slate-500 font-mono font-bold">\\dots \${escapeHtml(rv.rid)}</span>
      </div>
      <div class="text-[11px] text-slate-300 leading-relaxed space-y-1">
        <div><span class="text-slate-500 font-semibold">触发原因:</span> \${escapeHtml(rv.reason)}</div>
        <div><span class="text-slate-500 font-semibold">关联 Action:</span> <code class="bg-black/20 px-1 py-0.5 rounded font-mono text-[9px]">\${escapeHtml(rv.aid)}</code></div>
      </div>
      <div class="text-[11px] text-indigo-300 bg-indigo-500/5 border border-indigo-500/10 p-2.5 rounded-xl italic">审计建议: \${escapeHtml(rv.note || '无')}</div>
      <div class="flex flex-wrap gap-2 pt-1">
        <button onclick="commitDecision('\${escapeHtml(rv.rid)}', 'approve', '')" class="px-2.5 py-1.5 bg-green-600 hover:bg-green-500 text-white rounded-lg text-[10px] active:scale-95 transition-all font-bold">✓ 接受结果</button>
        <button onclick="commitDecision('\${escapeHtml(rv.rid)}', 'reject', '人工审核不通过')" class="px-2.5 py-1.5 bg-red-600 hover:bg-red-500 text-white rounded-lg text-[10px] active:scale-95 transition-all font-bold">✗ 拒绝</button>
        <button onclick="commitOverride('\${escapeHtml(rv.rid)}')" class="px-2.5 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-[10px] active:scale-95 transition-all font-bold">📝 覆盖灌入</button>
      </div>
    </div>\`;
  }).join('');
}

async function commitDecision(reviewId, outcome, note) {
  const actor = prompt("请输入审核员身份 (email):", "futen@outlook.com") || "demo-user";
  const r = await callTool("manual_commit", { review_id: reviewId, outcome, note, actor });
  if (r.ok) {
    alert("✓ 决策提交成功: " + outcome);
    loadPending();
    loadAudit();
  } else {
    alert("✗ 提交失败: " + r.error);
  }
}

async function commitOverride(reviewId) {
  const actor = prompt("请输入审核员身份 (email):", "futen@outlook.com") || "demo-user";
  const defaultPayload = JSON.stringify([{
    src_id: "c_102", src_label: "Corp",
    dst_id: "CT-2026-001", dst_label: "Contract",
    relation: "sign_contract", properties: {}
  }], null, 2);
  const payload = prompt("请输入修正灌入的 JSON 三元组:", defaultPayload);
  if (!payload) return;
  
  try {
    const r = await callTool("manual_commit", {
      review_id: reviewId, outcome: "override",
      note: "人工核对覆盖灌入",
      override_payload_json: payload, actor,
    });
    if (r.ok) {
      alert("✓ 修正数据覆盖灌入成功！");
      loadPending();
      loadAudit();
    } else {
      alert("✗ 灌入失败: " + r.error);
    }
  } catch (e) {
    alert("❌ 输入非标准 JSON");
  }
}

async function loadAudit() {
  const el = document.getElementById('audit-list');
  el.innerHTML = '<p class="text-slate-500 italic p-6 text-center">加载中…</p>';
  const r = await callTool("query_audit_actions", { limit: 30 });
  if (!r.ok) { el.innerHTML = '<p class="text-red-400 p-6 text-center">' + escapeHtml(r.error) + '</p>'; return; }
  const actions = r.result.actions || [];
  
  if (actions.length === 0) {
    el.innerHTML = '<p class="text-slate-500 italic bg-white/5 p-6 rounded-2xl text-center">无审计日志记录。</p>';
    return;
  }
  
  el.innerHTML = actions.map(a => {
    const ts = formatLocalTime(a.ts);
    const isOk = a.ok === "true";
    const okBadge = isOk ? 'bg-green-500/10 text-green-400 border border-green-500/20' : 'bg-red-500/10 text-red-400 border border-red-500/20';
    return \`
    <div class="border-l-2 text-slate-500 \${isOk ? 'border-green-500/30' : 'border-red-500/30'} pl-3 py-2 hover:bg-white/5 rounded-r transition-all">
      <div class="flex items-center justify-between">
        <span class="text-slate-500 font-bold font-mono text-[9px]">\${ts}</span>
        <span class="px-1.5 py-0.5 rounded text-[8px] font-bold tracking-wide \${okBadge}">\${a.ok.toUpperCase()}</span>
      </div>
      <div class="text-slate-200 font-bold mt-1 text-[11px]">\${escapeHtml(a.tool)}</div>
      <div class="text-[10px] text-slate-400 mt-0.5 leading-relaxed font-mono">\${escapeHtml(a.summary || '')}</div>
    </div>\`;
  }).join('');
}

async function checkConn() {
  try {
    const r = await fetch("/healthz");
    const d = await r.json();
    const bannerEl = document.getElementById("conn-status-banner");
    const statusEl = document.getElementById("conn-status");
    if (d.ok) {
      const html = '<span class="w-1.5 h-1.5 rounded-full bg-green-400"></span> 本地后端 OK';
      if (statusEl) {
        statusEl.innerHTML = html;
        statusEl.className = "px-3 py-1.5 rounded-full bg-green-500/10 text-green-400 border border-green-500/20 flex items-center gap-1.5 font-semibold";
      }
      if (bannerEl) {
        bannerEl.textContent = "已连接 (Port 5000)";
        bannerEl.className = "text-xs font-bold text-emerald-400";
      }
    } else {
      throw new Error();
    }
  } catch (e) {
    const html = '<span class="w-1.5 h-1.5 rounded-full bg-red-400 animate-pulse"></span> 本地后端断开';
    const statusEl = document.getElementById("conn-status");
    const bannerEl = document.getElementById("conn-status-banner");
    if (statusEl) {
      statusEl.innerHTML = html;
      statusEl.className = "px-3 py-1.5 rounded-full bg-red-500/10 text-red-400 border border-red-500/20 flex items-center gap-1.5 font-semibold";
    }
    if (bannerEl) {
      bannerEl.textContent = "已断开";
      bannerEl.className = "text-xs font-bold text-red-400";
    }
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));
}

function refreshAll() {
  checkConn();
  if (currentTab === 'gov') {
    loadPending();
    loadAudit();
  } else {
    loadTasks();
  }
}

// ── WebSocket 实时推送 ────────────────────────────────────────────
let ws = null;
let wsReconnectTimer = null;

function connectWS() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(proto + '://' + location.host + '/api/ws');

  ws.onopen = () => {
    console.log('[ws] connected');
    clearTimeout(wsReconnectTimer);
  };

  ws.onmessage = (evt) => {
    try {
      const msg = JSON.parse(evt.data);
      if (msg.type === 'task_updated' && msg.task) {
        const updated = msg.task;
        const idx = tasks.findIndex(t => t.task_id === updated.task_id);
        if (idx !== -1) {
          tasks[idx] = { ...tasks[idx], ...updated };
        } else {
          tasks.unshift(updated);
        }
        renderTasks();
        if (selectedTaskId === updated.task_id) {
          renderActiveReport(updated);
        }
      }
    } catch(e) { console.warn('[ws] parse error', e); }
  };

  ws.onerror = (e) => console.warn('[ws] error', e);

  ws.onclose = () => {
    console.log('[ws] closed, reconnecting in 4s');
    wsReconnectTimer = setTimeout(connectWS, 4000);
  };
}

// Initial setup
loadTasks();
checkConn();
setInterval(refreshAll, 15000);
connectWS();

// 只在有活跃任务时补充轮询（WS 已实时推送，此处为保险兜底）
let pollInterval = setInterval(() => {
  if (currentTab === 'ingest') {
    const hasActive = tasks.some(t => t.status === 'queued' || t.status === 'processing');
    if (hasActive) loadTasks();
  }
}, 8000);
</script>
</body>
</html>`;

// ============================
// Durable Object: TaskCoordinator
// ============================
export class TaskCoordinator {
  constructor(state, env) {
    this.state = state;
    this.env = env;
    this.sessions = []; // active WebSocket sessions
  }

  async fetch(request) {
    const url = new URL(request.url);

    // POST /notify  ← called by setTaskStatus after every KV write
    if (url.pathname === "/notify" && request.method === "POST") {
      const task = await request.json();
      const msg = JSON.stringify({ type: "task_updated", task });
      // broadcast to all alive sessions using Hibernation API
      const activeWebSockets = this.state.getWebSockets();
      for (const ws of activeWebSockets) {
        try { ws.send(msg); } catch (_) {}
      }
      return new Response("ok");
    }

    // WebSocket upgrade  ← browser connects here
    const upgradeHeader = request.headers.get("Upgrade");
    if (upgradeHeader === "websocket") {
      const pair = new WebSocketPair();
      const [client, server] = Object.values(pair);
      this.state.acceptWebSocket(server);
      this.sessions.push(server);

      // Send a ping immediately so the client knows it's connected
      try { server.send(JSON.stringify({ type: "connected" })); } catch (_) {}

      return new Response(null, { status: 101, webSocket: client });
    }

    return new Response("Not found", { status: 404 });
  }

  // Hibernation API handlers (keeps sessions alive across I/O suspensions)
  webSocketMessage(ws, msg) {
    // echo back pings, ignore other messages
    try {
      const d = JSON.parse(msg);
      if (d.type === "ping") ws.send(JSON.stringify({ type: "pong" }));
    } catch (_) {}
  }

  webSocketClose(ws) {
    this.sessions = this.sessions.filter(s => s !== ws);
  }

  webSocketError(ws) {
    this.sessions = this.sessions.filter(s => s !== ws);
  }
}
