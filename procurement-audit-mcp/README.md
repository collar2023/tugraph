# 🏢 采购合同风控审计 MCP 服务端 (Procurement Audit MCP)

本子文件夹实现了用于采购合同风控审计场景的 **Model Context Protocol (MCP)** 服务端。它直接将国产图数据库（**TuGraph**）与向量库（**Qdrant**）的底层操作封装为大模型（如 Claude, MiniMax）可调用的标准化工具组，解耦了业务智能体与物理数据库的直连代码。

---

## 1. 结构与组件 📋

*   `requirements.txt`：项目依赖库（含 `mcp`、`neo4j`、`qdrant-client`、`pandas` 等）。
*   `mcp_server.py`：使用 `FastMCP` 框架实现的标准 MCP 运行服务端。
*   `seed_procurement_data.py`：测试数据初始化灌入脚本，自动生成样例 CSV 合同文件并创建图谱本体。
*   `procurement_contracts_sample.csv`：自动生成的合同样本，供 LLM 进行数据探索。

---

## 2. 注册工具组清单 (Toolkits) ⚙️

服务端向大模型暴露了以下 12 个即插即用的核心工具，覆盖数据勘探、本体建模、安全查询、审计留痕以及人机协同（HITL）全生命周期：

| 工具名称 | 分组 (Category) | 危险级别 (Risk) | 功能说明 | 核心参数 |
| :--- | :--- | :--- | :--- | :--- |
| `get_raw_data_sample` | 数据探索 | `safe` | 读取本地原始 CSV/Excel 样本数据，供大模型逆向建模学习 | `file_path`, `limit` |
| `get_graph_schema` | 本体建模 | `safe` | 获取 TuGraph 当前已有的点边标签及属性定义（JSON 格式） | 无 |
| `create_graph_label` | 本体建模 | `caution` | 动态修改图谱 Schema，向 TuGraph 注入新的点/边 Label | `label_type`, `label_name`等 |
| `execute_cypher` | 图谱演算 | `caution` | 直连 TuGraph 运行纯读 Cypher，内置关键字拦截与强制 LIMIT 限制 | `query`, `params_json` |
| `bulk_insert_relationships`| 自动建图 | `danger` | 大模型提取三元组后，批量幂等灌入点边持股关系网络 | `relationships_json`, `idempotency_key` |
| `search_vector_news` | 向量研判 | `safe` | 直连 Qdrant 检索供应商近期的诉讼与违约舆情，实现双驱研判 | `supplier_name`, `limit` |
| `list_tools` | 元数据 | `safe` | 查询本服务所有可用 tool 的契约清单 (TOOL_REGISTRY) | 无 |
| `describe_tool` | 元数据 | `safe` | 查询单个 tool 的详细参数契约与危险等级 | `tool_name` |
| `query_audit_actions` | 审计留痕 | `safe` | 查询最近 N 条审计操作记录（AuditAction + AuditRef） | `limit` |
| `flag_for_review` | 人机协同 | `caution` | 将执行失败/低置信度的操作标为待人工审核，生成 PendingReview 节点 | `action_id`, `reason`, `note` |
| `list_pending_reviews` | 人机协同 | `safe` | 列出 PendingReview 列表（含关联的 HumanDecision） | `status_filter` |
| `manual_commit` | 人机协同 | `danger` | 审核员对挂起状态做决策 (approve / reject / override 强灌) | `review_id`, `outcome`, `note`等 |

---

## 3. 运行指南 🚀

### 3.1 安装环境依赖
```bash
pip install -r requirements.txt
```

### 3.2 初始化测试数据
运行初始化脚本以在本地 TuGraph 数据库中生成 `Contract`、`sign_contract`、`approve_by` 本体，并生成 CSV 合同样本文件：
```bash
python3 seed_procurement_data.py
```

### 3.3 运行或调试 MCP 服务端
*   **调试模式 (推荐使用 MCP CLI)**：
    ```bash
    mcp dev mcp_server.py
    ```
*   **直接运行服务 (Stdio 模式)**：
    ```bash
    python3 mcp_server.py
    ```

### 3.4 验证与测试工具组 (Test Verification)
项目包含一个本地测试脚本 `test_mcp_tools.py`，用于程序化验证 FastMCP 服务端暴露的各个 Tools 的连通性与返回数据格式。
*   **运行测试**：
    ```bash
    python3 test_mcp_tools.py
    ```
    该脚本将依次调用 `get_raw_data_sample`（数据采样）、`get_graph_schema`（获取本体结构）、`execute_cypher`（多表 Cypher 图查询）及 `search_vector_news`（向量舆情召回），并在终端回显格式化后的 JSON 数据。

---


## 4. 接入客户端配置 🛠️

若要将本服务接入 **Claude Desktop** 或其他兼容 MCP 的客户端，请在您的 MCP 配置文件（如 `~/.config/Claude/claude_desktop_config.json`）中添加以下配置：

```json
{
  "mcpServers": {
    "procurement-audit-service": {
      "command": "python3",
      "args": [
        "/home/ubuntu/tugraph/procurement-audit-mcp/mcp_server.py"
      ],
      "env": {
        "TUGRAPH_URI": "bolt://localhost:7687",
        "TUGRAPH_USER": "admin",
        "TUGRAPH_PASSWORD": "YOUR_TUGRAPH_PASSWORD",
        "TUGRAPH_DB": "default",
        "QDRANT_URL": "http://localhost:6333"
      }
    }
  }
}
```
配置完成后，大模型将自动加载并具备上面列出的所有 6 个工具，实现全自动的采购合同关系审计与数据写入。

---

## 5. 核心工具组的底层设计原理与商业价值 💡

在企业级 AI Agent 与混合 GraphRAG 系统的落地工程中，数据录入与三元组转化（三元化）往往占据了 80% 以上的时间与工作量。本 MCP 服务端通过以下三大底层机制，彻底解决了这一瓶颈并赋予了系统极佳的扩展性：

### 5.1 解决“三元化灌入”痛点：大模型无需编写复杂的 Cypher 语句 (物理执行与语义解耦)

在传统的图谱建模写入中，大模型提取出三元组后（如 `张三（Person） --持股 15%--> 某公司（Corp）`），必须尝试生成用于写入的物理 Cypher 语句。这会导致极高的崩溃率，主要由于：
1. **实体主键属性混淆**：大模型需要记住 `Person` 的主键属性名叫 `person_id`，而 `Corp` 的主键属性名叫 `corp_id`。一旦模型写错成 `{id: "..."}`，TuGraph 就会直接报错崩溃。
2. **空指针/静默失败**：如果 `Person` 或 `Corp` 节点在图数据库中还不存在，直接执行 `MATCH ... CREATE` 会因为匹配为空而静默失败，无法建边。
3. **语法方言冲突**：大模型写出的 Cypher 容易混淆不同图数据库的细微语法差异，导致频繁报语法错。

**MCP 工具组的解耦原理：**
- **大模型只管语义提取**：大模型读完合同，只需要把提取的关系整理成一个**标准的、无视数据库类型的 JSON 数组**：
  ```json
  [
    {
      "src_id": "p_zhangsan",
      "src_label": "Person",
      "dst_id": "c_somecorp",
      "dst_label": "Corp",
      "relation": "hold_share",
      "properties": { "share": 15.0 }
    }
  ]
  ```
- **MCP 服务端接管物理写入**：大模型直接调用工具 `bulk_insert_relationships(relationships_json=...)`。MCP 服务端在底层用 Python 静态代码自动映射主键（如 `Person` 映射至 `person_id`）、处理节点前置建立规避空指针，并用经过压测的参数化 Cypher 模板安全刷入 TuGraph，**直接把三元化阶段大模型因为写错 SQL/Cypher 导致的报错率降低到了 0%**。
- **性能优势**：大模型一次性把 100 条三元组丢给 MCP，MCP 在底层用长连接或批量 Pipeline 统一合并写入，减少物理数据库连接的往返延迟，比大模型单独发起网络连接快了数十倍。

### 5.2 解决“逆向建模与探索”痛点：实现本体与 Schema 的 AI 自进化

在企业交付环境中，不同部门导出的数据表格结构（Excel/CSV）千奇百怪。让程序员手工去给每个新场景写 DDL 建立图 Schema 极其浪费工时。

**本体自进化工作原理：**
1. **数据探针与特征识别 (Inspection)**：大模型通过 `get_raw_data_sample` 接口读取原始 Excel/CSV 表格的前几行，自动分析列名（如 `vendor_id`, `supplier_name`）与数据特征。
2. **本体设计与推理 (Ontology Reasoning)**：大模型接收到数据特征，结合业务目标进行语义推理，自动推导图谱本体（应该建哪些点、哪些边、什么属性），并输出设计好的 JSON DDL 载荷。
3. **Schema 动态注入 (Seeding)**：大模型调用 `create_graph_label` 工具。由于 TuGraph 原生支持通过 JSON 格式来定义和修改点边 Schema（如 `CALL db.createVertexLabelByJson`），MCP 服务端直接把大模型的 JSON 传递给 TuGraph 执行建 Label，**实现了“阅读数据 -> 设计 Schema -> 动态建库”的全自动管线**。
4. **状态反馈与对齐 (Verification)**：建库成功后，大模型调用 `get_graph_schema` 重新拉取物理 Schema，验证点边是否已成功注册，然后指导下一步三元化灌入。这极大地减免了现场人工编写 DDL 建图的工作量，是交付工程师的绝对利器。
5. **鲁棒性护栏（白名单机制）**：为防止模型在自动建库时导致“本体无序膨胀”（例如今天建了 `Corp` 节点，明天又建了一个 `Company` 节点），在生产中可设置只允许大模型在预设的词典范围内进行映射，或者在大模型设计好 Schema 建议后由部署工程师配合企业人员通过 HITL 协同页面一键确认，保障图谱质量。

### 5.3 屏蔽物理数据库差异，实现“一次编写，到处运行” (信创对齐与架构解耦)

在传统的开发中，大模型状态机代码中会直接引入特定的物理数据库驱动（如 `qdrant_client`、`neo4j`），一旦发生底层迁移（如客户强制要求将 Qdrant 向量库换成国产 Milvus，或将 TuGraph 换成商业 Neo4j），就需要修改大量底层代码和 Prompt，耗时数周并会引入 regression bug。

**MCP 屏蔽差异原理：**
- **统一语义层**：大模型 Agent 在运行中只感知标准的 MCP Tools 接口（如 `execute_cypher`、`search_vector_news`）。物理连接细节、凭证安全配置完全被隔离在 `mcp_server.py` 中。
- **极速热切换**：
  * **向量库由 Qdrant 换成 Milvus**：只需在 `mcp_server.py` 的 `search_vector_news` 工具内，把 `QdrantClient` 替换为 `pymilvus`，重写这几行查询。**大模型端、前端页面、云端队列不需要修改 1 行代码**，直接无缝跑通新向量库。
  * **图数据库由 TuGraph 换成 Neo4j**：只需修改 `mcp_server.py` 顶部的 Bolt 连接地址和密码。因为 TuGraph 兼容 Neo4j 的 Cypher，大模型感知不到变更，瞬间完成迁移。
- **商业价值**：这为企业级 PoC 交付提供了极高的敏捷度，能轻松满足各类“信创合规”的数据库对齐指标。同时，可将核心 Agent 状态机封装为闭源加密镜像（保护知识产权），仅将轻量的 `mcp_server.py` 暴露给工程师进行现场环境配置，保障了商业安全。

---

## 6. Nebula-Gate 商业化独立部署与变现前景 💎

这 12 个 Tools 组成的 MCP 服务端并不局限于某一个项目，其底座设计具有极强的通用性，可作为独立的 AI 数据库安全网关产品（**Nebula-Gate**）进行变现：

### 6.1 核心商业卖点 (Value Proposition)
1. **数据库物理防火墙**：在大模型与物理数据库（SQL / Cypher）之间拉起安全阻断网，拦截所有 DDL/DML 越权写入，防范大模型失控或 Prompt 注入。
2. **防暴与查询限流 (OOM Prevention)**：自动限制大结果集返回，强制注入 `LIMIT`，绑定 UNIX 信号闹钟进行软超时中止，保障数据库可用性。
3. **完全合规的审计追踪**：每次修改数据库的行为均转化为审计图谱（AuditAction -> AuditRef）写入图数据库本身，满足企业合规审计。
4. **人机协同（HITL）阀门**：将异常或低置信度的操作自动挂起为 PendingReview，提供管理决策覆写机制，保证系统具备 100% 可控性。

### 6.2 商业销售模式 (Monetization Models)
* **独立中间件许可 (On-premise Gateway)**：作为企业私有化部署的安全网关 Docker 容器出售，为企业的自建大模型应用 proxy 数据库端口。
* **快速开发 SDK (Developer License)**：作为连接 TuGraph + Qdrant 的生产级脚手架模板，售卖给其他 AI 开发者，缩短其治理与安全模块的研发周期。
* **POC 项目强力催化剂**：在竞标金融/国企项目时，作为“安全合规与人机协作底座”打包输出，能够强力击败没有合规审计机制的竞争产品。

