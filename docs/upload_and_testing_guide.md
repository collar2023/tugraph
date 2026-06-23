# Ingestion & Testing Guide (数据录入与端到端测试指南)

本文档旨在说明如何使用 `curl` 在端侧模拟文件上传，并跟踪 Cloudflare Workers ➔ Queue ➔ 本地 FastAPI Proxy ➔ TuGraph ➔ DO WebSocket 广播的完整异步处理链路。

---

## 1. 核心链路诊断指令

在开始测试前，可以通过以下命令检查本地服务状态及日志：

### 1.1 检查本地 Proxy 进程
```bash
# 查看本地运行的 mcp_proxy.py 与 mcp_server.py 进程
ps -ef | grep mcp_proxy.py
```

### 1.2 查看 systemd 服务状态
```bash
# 本地 Proxy 作为 systemd 服务运行，名称为 mcp-proxy
sudo systemctl status mcp-proxy
```

### 1.3 实时追踪运行日志 (最重要)
```bash
# 实时滚动追踪本地对账及智能体执行日志，可直接观察 Cypher 查询与 HITL 回调
sudo journalctl -f -n 50 -u mcp-proxy
```

---

## 2. 场景触发规则 (Triggering Rules)

后端 Proxy 根据**上传文件的文件名**自动分发审计处理器：
1.  **业财发票核销场景**：文件名中必须包含 `invoice`、`recon` 或 `payment` 中的任意一个关键字（如 `complex_payment_reconciliation.csv`）。
    *   *逻辑*：直连 TuGraph 运行超开、欠付、第三方代付三项异常穿透。
2.  **向量舆情场景**：文件名不包含上述关键字。
    *   *逻辑*：根据文件名作为供应商名，直接在 Qdrant 向量库中进行 n-gram 相似度检索。

---

## 3. 对账测试数据 CSV 规范

为了触发全项业财核销异常，可构建包含多维度字段的测试 CSV，例如 `/home/ubuntu/tugraph/procurement-audit-mcp/complex_payment_reconciliation.csv`：

```csv
transaction_id,contract_ref,invoice_number,invoice_date,vendor_code,vendor_name,buyer_name,invoice_amount,paid_amount,payment_method,payor_entity,bank_reference,audit_flag
TX-2026-9081,cont_001,inv_001,2026-06-01,V-SIGMA,SigmaCorp (供应商/销售商),AlphaCorp (采购商A),30000.0,30000.0,BANK_WIRE,AlphaCorp (采购商A),REF-89021832019,MATCHED
TX-2026-9082,cont_001,inv_002,2026-06-02,V-SIGMA,SigmaCorp (供应商/销售商),AlphaCorp (采购商A),20000.0,15000.0,CASH,AlphaCorp (采购商A),REF-89021832020,UNDER_PAID
TX-2026-9083,cont_002,inv_003,2026-06-03,V-SIGMA,SigmaCorp (供应商/销售商),BetaCorp (采购商B),110000.0,110000.0,BANK_WIRE,GammaCorp (神秘第三方付款方),REF-89021832021,THIRD_PARTY_MISMATCH
TX-2026-9084,cont_002,inv_003,2026-06-03,V-SIGMA,SigmaCorp (供应商/销售商),BetaCorp (采购商B),110000.0,0.0,UNPAID,BetaCorp (采购商B),,OVER_BILLED
```

---

## 4. 端到端测试步骤

### 第一步：检查全链路联通性
```bash
# 检查 CF Worker ➔ Local Tunnel ➔ FastAPI Healthz 是否正常
curl -s https://fder.188001.xyz/healthz
```

### 第二步：端侧模拟上传文件
```bash
# 上传 CSV 文件至边缘端 R2 并送入队列，需注意文件名需匹配触发规则
curl -X POST -F "file=@/home/ubuntu/tugraph/procurement-audit-mcp/complex_payment_reconciliation.csv" https://fder.188001.xyz/api/upload
```
*响应示例*：
```json
{
  "ok": true,
  "task_id": "eb9f2261-8c02-4ec3-a4fd-f4d1c4b916e9",
  "status": "queued",
  "r2_key": "contracts/2026-06-22/eb9f2261-8c02-4ec3-a4fd-f4d1c4b916e9.csv"
}
```

### 第三步：查询任务最终状态与报告
```bash
# 根据返回的 task_id 查询最终处理报告（通常在 10s 内从 queued ➔ completed）
curl -s https://fder.188001.xyz/api/status/<YOUR_TASK_ID>
```

### 第四步：检查人机协同（HITL）流转列表
由于测试 CSV 触发了合规异常，后台会调用 `flag_for_review` 生成审核单。可通过代理路由直接查询待审列表：
```bash
curl -H "X-Harness-Secret: GKxfydwnbfvFKW0QVqc1d80Y7uArzBPxwYAbcSTcY-Q" \
     https://fder.188001.xyz/api/mcp-proxy/api/pending
```
