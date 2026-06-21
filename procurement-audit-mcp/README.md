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

在企业级 AI Agent 与混合 GraphRAG 系统的落地工程中，数据录入与三元组转化（三元化）往往占据了 80% 以上的时间与工作量。本 MCP 服务端通过以下两个最核心的工具板块（板块一：数据探索与自适应建模；板块二：图谱演算与语义写入），实现了安全图谱读写、零注入风险以及数据自演变：

### 5.1 第一板块：数据探索与自适应建模工具的运行机制
这一板块旨在赋予大模型“视觉”和“本体自进化”能力，让 AI 能够针对异构的原始表格完成动态适配：
1. **`get_raw_data_sample` (数据勘探探针)**：
   * **原理**：安全地只读取传入 CSV/Excel 的前几行样本数据，返回给大模型。
   * **价值**：大模型的“眼睛”，防止在没有见过原始数据列名、数值格式和空值情况时盲目猜测 Schema。
2. **`get_graph_schema` (拉取物理地图)**：
   * **原理**：直接向 TuGraph 查询当前数据库已建立的所有顶点、边的 Label 和属性（JSON 格式）。
   * **价值**：向大模型提供已存在的实体和关系骨干表地图，指导其如何把抽取出的数据嫁接到已有底座上。
3. **`create_graph_label` (动态本体注入与演进化)**：
   * **原理**：大模型根据数据特征，推导出新 Label DDL 的 JSON payload（如新增 `Vehicle` 实体），通过 `CALL db.createVertexLabelByJson`（或 Edge 对应的过程）动态建立结构。
   * **安全护栏**：
     * **正则拦截**：对 `label_name` 强制执行 `re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", label_name)` 校验，防止恶意的 procedure 注入。
     * **约束绑定**：自动把主键的 `is_primary`、`is_unique`、`is_notnull` 强制设为 `True`，保证新建实体符合底线规则。
     * **白名单与工程师审核（可选）**：通过结合 HITL 人工协作网页，可实现由大模型提出 Schema 扩建方案、人类一键确认后动态写入图谱的卓越交付管线。

### 5.2 第二板块：图谱演算与语义写入的防爆与防重设计
该板块是数据写入与查询的核心网关，为高并发、复杂查询下的物理图数据库拉起了一道防火墙：
1. **`execute_cypher` (安全图谱演算通道)**：
   * **黑名单拦截**：通过 `_enforce_safety(query)` 解析查询语句，一旦包含 `DELETE`、`DROP`、`CREATE`、`MERGE`、`SET` 等修改或删除关键字，直接拒绝执行并记入审计。大模型绝对无法通过此工具修改或删除数据库任何数据。
   * **自动注入 LIMIT 1000（防爆盾）**：如果大模型生成的 Cypher 没有指定 LIMIT，代码在底层会自动拼接 `LIMIT 1000`，有效防止全表扫描导致系统发生 OOM 崩溃。
   * **8秒闹钟信号硬超时**：图数据库的多表关联（Join）极耗 CPU。如果查询发生复杂笛卡尔积或超过 8 秒，直接强行中止，保护数据库的系统可用性。
2. **`bulk_insert_relationships` (批量三元组解耦灌入)**：
   * **大模型与物理细节解耦**：大模型不需要编写复杂的物理 Cypher 写入语句，它只需提取语义级别的标准 JSON（包含 src_id, src_label, dst_id, dst_label, relation）。MCP 服务端接管主键查找（如自动映射 `Person -> person_id`, `Corp -> corp_id`），由后端 Python 脚本自动装载，**将大模型因为写错 SQL/Cypher 方言导致的写入崩溃率降为 0%**。
   * **MERGE 模式幂等写入**：生成的 Cypher 全部基于属性绑定的 `ON CREATE SET` / `ON MATCH SET` 的 `MERGE` 语法，确保节点和边均是幂等写入，节点不存在则自动跳过建边，保证数据库整洁度。
   * **文件级去重缓存 (Idempotency Engine)**：每次写入结果会在 `scratch/mcp_writes.jsonl` 中持久化一份 idempotency key（关系数据的 SHA256 哈希值）。当大模型或队列重试重放时，**直接在 0 毫秒内命中缓存并返回，不再冲击底层数据库**，杜绝了重复连边与物理锁表风险。
3. **`search_vector_news` (向量舆情研判)**：
   * **双驱对齐**：直连 Qdrant 检索供应商近期的诉讼与舆情，与图谱内部的财务对账链路合并分析，形成多维度的风险合规风控审计结果。

### 5.3 屏蔽物理数据库差异，实现“一次编写，到处运行” (架构解耦与商业价值)
* **统一语义接口**：大模型 Agent 仅通过标准的 MCP Tools（如 `execute_cypher`、`search_vector_news`）与外部交互，物理驱动细节完全被隔离在 `mcp_server.py` 内部。
* **信创国产化极速热切换**：
  * **向量库从 Qdrant 换成国产 Milvus**：只需在 `mcp_server.py` 的 `search_vector_news` 里把 `QdrantClient` 替换为 `pymilvus`，重写这几行查询。**大模型、云端队列和前端不需要修改任何代码**，即可平滑切换。
  * **图数据库从 TuGraph 换成商业 Neo4j**：只需更改 `mcp_server.py` 的 Bolt 连接凭证。因为 TuGraph 的 Cypher 兼容 Neo4j，切换过程对大模型无感知，瞬间完成迁移。
* **知识产权保护**：可将核心大模型 Agent 和业务状态机封装为闭源加密镜像（保护企业代码知识产权），仅公开暴露 `mcp_server.py` 作为适配器交由部署人员现场配置，极大降低了项目实施的摩擦风险。


---

## 6. TuGraph-Gate 商业化独立部署与变现前景 💎

这 12 个 Tools 组成的 MCP 服务端并不局限于某一个项目，其底座设计具有极强的通用性，可作为独立的 AI 数据库安全网关产品（**TuGraph-Gate**）进行变现：

### 6.1 核心商业卖点 (Value Proposition)
1. **数据库物理防火墙**：在大模型与物理数据库（SQL / Cypher）之间拉起安全阻断网，拦截所有 DDL/DML 越权写入，防范大模型失控或 Prompt 注入。
2. **防暴与查询限流 (OOM Prevention)**：自动限制大结果集返回，强制注入 `LIMIT`，绑定 UNIX 信号闹钟进行软超时中止，保障数据库可用性。
3. **完全合规的审计追踪**：每次修改数据库的行为均转化为审计图谱（AuditAction -> AuditRef）写入图数据库本身，满足企业合规审计。
4. **人机协同（HITL）阀门**：将异常或低置信度的操作自动挂起为 PendingReview，提供管理决策覆写机制，保证系统具备 100% 可控性。

### 6.2 商业销售模式 (Monetization Models)
* **独立中间件许可 (On-premise Gateway)**：作为企业私有化部署的安全网关 Docker 容器出售，为企业的自建大模型应用 proxy 数据库端口。
* **快速开发 SDK (Developer License)**：作为连接 TuGraph + Qdrant 的生产级脚手架模板，售卖给其他 AI 开发者，缩短其治理与安全模块的研发周期。
* **POC 项目强力催化剂**：在竞标金融/国企项目时，作为“安全合规与人机协作底座”打包输出，能够强力击败没有合规审计机制的竞争产品。

