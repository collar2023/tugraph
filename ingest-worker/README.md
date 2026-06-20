# ingest-worker (v0.3 · 已上线)

CF Worker：实现白皮书双向异步闭环 + **Durable Objects 实时 WebSocket 推送**。

---

## 当前状态 (2026-06-20 · v0.3 Production)

| 能力 | 状态 |
|---|---|
| R2 文件暂存 + Queue 入队 | ✅ 已验证 |
| KV 任务状态 (含 filename/size 完整元数据) | ✅ 已修复 |
| Queue Consumer → 本地后端 `/api/process` | ✅ 已验证 |
| POST `/api/callback` 回写状态 | ✅ 已验证 |
| GET `/api/tasks` 任务列表 | ✅ 已验证 |
| **Durable Object `TaskCoordinator`** | ✅ 已部署 |
| **WebSocket `/api/ws` 实时推送** | ✅ 已上线 |
| 前端 Dashboard（TuGraph-Intelligence） | ✅ 已上线 |
| 人机协同 HITL 面板（approve / reject / override） | ✅ 已上线 |

**线上地址**: `https://fder.188001.xyz`  
**Worker 版本**: `eaadeac6-bfca-483d-bc9d-84e97e649f11`

---

## Binding 清单（5 个）

| Binding | 类型 | 资源 | 用途 |
|---|---|---|---|
| `CONTRACTS` | R2 | `procurement-contracts` | 文件暂存 |
| `TASK_QUEUE` | Queue | `ingest-tasks` | 任务入队 / 消费 |
| `TASK_STATUS` | KV | `e5b060080e21...` | 任务状态持久化（TTL 7天）|
| **`TASK_COORDINATOR`** | **Durable Object** | `TaskCoordinator` | **WebSocket 实时广播** |
| `BACKEND_URL` / `HARNESS_SECRET` | env vars | — | 本地后端调用 |

---

## 架构（v0.3 Durable Object 实时推送）

```
[Browser] ──WebSocket wss://fder.188001.xyz/api/ws──► [TaskCoordinator DO]
                                                              ▲
[KV write] ◄── setTaskStatus() ──► POST /notify ───────────┘
                                         (fire-and-forget)
```

- **生产**：Browser 上传 → Worker → R2 暂存 → Queue 入队 → KV `queued`
- **消费**：Queue Consumer → 本地后端 `/api/process` → Callback → KV `completed`
- **推送**：每次 `setTaskStatus()` 写 KV 后同时 POST DO `/notify`，DO 广播给所有在线 WebSocket 客户端
- **兜底**：前端保留 8s 补充轮询（WS 断线时自动降级）

---

## API 端点

| Method | Path | 说明 |
|---|---|---|
| `GET` | `/` | 前端 Dashboard HTML |
| `POST` | `/api/upload` | 上传文件 → R2 + Queue + KV |
| `GET` | `/api/status/:id` | 查询单任务状态 |
| `GET` | `/api/tasks` | 列出最近 30 个任务 |
| `POST` | `/api/callback` | 本地后端回写完成状态（带 secret 验证）|
| `GET/WS` | `/api/ws` | **WebSocket 升级入口**（转发到 DO）|
| `GET/POST` | `/api/mcp-proxy/*` | 透传到本地后端 MCP 接口 |
| `GET` | `/healthz` | 健康检查 |

---

## 部署

```bash
cd /home/ubuntu/tugraph/ingest-worker
npx wrangler deploy        # 部署
npx wrangler tail          # 实时日志
```

## 验证

```bash
# 上传测试文件
curl -X POST https://fder.188001.xyz/api/upload \
  -F "file=@scratch/Hicks PLC.txt"

# 查看任务列表
curl https://fder.188001.xyz/api/tasks | python3 -m json.tool

# WebSocket 验证（浏览器 DevTools → Network → WS）
# 连接后应收到 {"type":"connected"}
# 上传文件后应收到 {"type":"task_updated", "task": {...}}
```
