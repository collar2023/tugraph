# Fraud-Agent: 基于 TuGraph 的极简直连反欺诈智能体 (v3.5)

本项目是部署于 **Oracle ARM** 环境下的轻量级反欺诈研判智能体。项目已全面升级为**直连主方案架构（Lean GraphRAG Architecture）**，彻底废除了重型 Java 语义服务（OpenSPG）与 NebulaGraph 容器集群，仅以单个 **TuGraph** 容器作为图存储，配合 Python 内存 DFS 进行深度团伙排查。

---

## 🏗️ 1. 反欺诈图谱本体 (Schema)
在 TuGraph 中建立的核心本体结构如下：
*   **点 (Vertices)**：
    *   `Applicant`（申请人）：主键 `applicant_id`，属性 `name`、`age`。
    *   `Device`（硬件设备）：主键 `device_id`。
    *   `Phone`（手机号码）：主键 `phone_number`。
*   **边 (Edges)**：
    *   `USED_DEVICE` (由 `Applicant` 指向 `Device`)：申请人使用的设备。
    *   `WITH_PHONE` (由 `Applicant` 指向 `Phone`)：申请人关联的手机号。

---

## 🔍 2. 核心反欺诈研判场景
1.  **设备共享团伙排查**：检测多名申请人共用相同硬件设备的风险拓扑，并识别出连接多个共享设备的核心枢纽节点（中介或黑产组织）。
2.  **手机号共用风险排查**：检测多名申请人共享同一手机号的异常情况。
3.  **设备指纹关联溯源**：以特定申请人或特定设备为中心，反查所有关联关系，追踪历史污染源。

---

## ⚙️ 3. 运行与验证指引 (Execution Guide)

运行前确保已设置 MiniMax 环境变量：
```bash
export MINIMAX_API_KEY="您的 MiniMax API Key"
```

### 3.1 灌入反欺诈测试数据
```bash
python3 /home/ubuntu/tugraph/fraud-agent/d_route/seed_data.py
```

### 3.2 运行端到端反欺诈状态机 Demo
```bash
python3 /home/ubuntu/tugraph/fraud-agent/d_route/demo_llm.py
```
这会依次执行 5 个核心反欺诈查询，并由大模型分析并输出详细的中文《反欺诈风险研判报告》。
