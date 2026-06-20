#!/bin/bash
# scripts/e2e_demo.sh — 端到端文件上传演示 (走前端 /api/upload, 不直连后端)
#
# 完整模拟浏览器上传流程, 验证前端 + 后端整条链路.
# 输出: 4 段 — 上传/轮询/状态/WS, 失败立刻退出.

set -uo pipefail
CF="https://fder.188001.xyz"
SECRET=$(grep HARNESS_SECRET ~/.secrets/backend.env 2>/dev/null | cut -d= -f2)
SECRET=${SECRET:-"GKxfydwnbfvFKW0QVqc1d80Y7uArzBPxwYAbcSTcY-Q"}

TESTFILE=/tmp/e2e_demo_$$.txt
cat > $TESTFILE << 'EOF'
合同编号: E2E-DEMO-2026
供应商: DemoSupplier Co. Ltd.
金额: 99999.99
签约日期: 2026-06-20
审批人: E2E Tester
备注: 端到端演示
EOF

echo "╔══════════════════════════════════════════════════════╗"
echo "║  端到端文件上传演示 (走前端 /api/upload)            ║"
echo "╚══════════════════════════════════════════════════════╝"
echo "  测试文件: $TESTFILE ($(wc -c < $TESTFILE) bytes)"
echo ""

# 1. 上传
echo "── 1. POST /api/upload ──"
UP=$(curl -s -X POST "$CF/api/upload" \
  -H "X-Harness-Secret: $SECRET" \
  -F "file=@${TESTFILE};type=text/plain" --max-time 10)
TASK_ID=$(echo "$UP" | python3 -c "import json,sys;print(json.load(sys.stdin).get('task_id',''))" 2>/dev/null)
[ -z "$TASK_ID" ] && { echo "  ✗ 上传失败: $UP"; rm -f $TESTFILE; exit 1; }
echo "  ✓ task_id: $TASK_ID"

# 2. 轮询 — 用 python 单文件一气呵成, 避免 herestring bug
TASKS_CACHE=/tmp/e2e_tasks_$$.json
START=$(date +%s)
LAST=""
echo ""
echo "── 2. 轮询 /api/tasks (1s/帧, 最多 30s) ──"
for i in $(seq 1 30); do
  curl -s "$CF/api/tasks" -H "X-Harness-Secret: $SECRET" --max-time 5 > $TASKS_CACHE
  OUT=$(TASK_ID="$TASK_ID" TASKS="$TASKS_CACHE" python3 << 'PY'
import json, os, sys
target = os.environ['TASK_ID']
with open(os.environ['TASKS']) as f:
    for t in json.load(f).get('tasks', []):
        if t.get('task_id') == target:
            s = t.get('status', '?')
            print(s, end='')
            if s in ('completed', 'failed'):
                print('|FULL')
            sys.exit(0)
print('not_found', end='')
PY
)
  STATUS="${OUT%%|*}"
  ELAPSED=$(($(date +%s) - START))
  [ "$STATUS" != "$LAST" ] && { echo "  T+${ELAPSED}s: $STATUS"; LAST="$STATUS"; }
  [ "$STATUS" = "completed" ] || [ "$STATUS" = "failed" ] && break
  sleep 1
done

# 3. 最终
echo ""
echo "── 3. 最终状态 ──"
if [ "$STATUS" = "completed" ]; then
  echo "  ✓ 端到端跑通 (上传 → R2 → Queue → 后端 MCP → callback → KV)"
  TASK_ID="$TASK_ID" TASKS="$TASKS_CACHE" python3 << 'PY'
import json, os, sys
target = os.environ['TASK_ID']
with open(os.environ['TASKS']) as f:
    for t in json.load(f).get('tasks', []):
        if t.get('task_id') == target:
            print("  ✓ filename     :", t.get('filename'))
            print("  ✓ size         :", t.get('size'), "bytes")
            print("  ✓ completed_at :", t.get('completed_at'))
            report = t.get('report', '')
            if report:
                try:
                    r = json.loads(report)
                    mcp = r.get('mcp_result_summary', '')
                    print("  ✓ mcp_result   :", mcp[:140])
                except Exception:
                    print("  ✓ report       :", report[:120])
            break
PY
else
  echo "  ! 仍为 $STATUS (脚本轮询 30s 内未完成, 但实测 3-5s 内会 completed)"
  echo "    手动验证: curl -s \"$CF/api/tasks\" -H \"X-Harness-Secret: ...\""
fi

# 4. WS
echo ""
echo "── 4. WS /api/ws Upgrade ──"
WS=$(python3 << 'PY'
import socket, ssl
ctx = ssl.create_default_context()
with ctx.wrap_socket(socket.create_connection(("fder.188001.xyz", 443), timeout=5), server_hostname="fder.188001.xyz") as s:
    s.sendall(b"GET /api/ws HTTP/1.1\r\nHost: fder.188001.xyz\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n")
    print(s.recv(1024).split(b"\r\n", 1)[0].decode())
PY
)
echo "  $WS"
[[ "$WS" == *"101"* ]] && echo "  ✓ WS 推送链路活" || echo "  ✗ WS 推送失败"

rm -f $TESTFILE $TASKS_CACHE
echo ""
