# 核心骨干表构建指南 (Ontology Seeding Guide)

在 TuGraph-Gate 大模型图谱网关架构中，“本体无序膨胀”与“大模型造词幻觉”是导致下游图计算算法（如股权穿透、UBO计算）崩溃的致命元凶。

为了彻底解决这一问题，本架构引入了 **“第 0 步：人工预建骨干表 (Ontology Seeding)”** 机制。该机制要求数据架构师在系统上线前，通过 Python 脚本将核心业务的“底线思维”以强 Schema 的形式硬编码固化到 TuGraph 中。

本文档将详细说明数据架构师如何使用 Python 语言编写核心骨干表的初始化脚本。

---

## 1. 原理与选型

* **工具语言**：`Python 3`
* **驱动包**：`neo4j` (官方 Bolt 协议客户端)
* **核心语法**：放弃传统的 Cypher `CREATE`，强制调用 TuGraph 底层的原生存储过程（`CALL db.createVertexLabelByJson` 和 `CALL db.createEdgeLabelByJson`），以实现毫秒级的强约束注入。

---

## 2. 脚本编写核心拆解

### 第一步：建立安全的高速连接
TuGraph 完美兼容国际标准的 Bolt 协议。架构师可以直接使用 Neo4j 驱动包建立长连接。

```python
import json
from neo4j import GraphDatabase

# 数据库连接配置 (TuGraph 默认 Bolt 端口为 7687)
URI = "bolt://localhost:7687"
USER = "admin"
PASSWORD = "YOUR_TUGRAPH_PASSWORD"

# 初始化驱动实例
driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
```

### 第二步：定义强类型的“实体”（点表 Vertex）
在 TuGraph 中建立实体骨干表时，必须严格指定**主键（Primary Key）**和**非空约束**。这是防止大模型抽取相同企业时发生“节点影分身”的物理保障。

```python
def create_core_vertex_labels(session):
    print("正在建立【Corp】企业骨干表...")
    
    # 严格定义 Schema JSON
    corp_schema = {
        "label": "Corp",
        "primary": "corp_id",  # 强约束：必须有唯一的 corp_id 主键
        "type": "VERTEX",
        "properties": [
            {
                "name": "corp_id", 
                "type": "STRING", 
                "is_primary": True, 
                "is_unique": True, 
                "is_notnull": True,
                "max_length": 100
            },
            {
                "name": "name", 
                "type": "STRING", 
                "is_notnull": False
            }
        ]
    }
    
    # 通过存储过程注入
    cmd = f"CALL db.createVertexLabelByJson('{json.dumps(corp_schema)}')"
    session.run(cmd)
    print("  -> Corp 表建立成功！")
```

### 第三步：定义带“物理边界约束”的“关系”（边表 Edge）
定义关系表是整个过程中**最关键的一环**。架构师必须通过 `constraints` 阵列死死限制住哪些实体可以连线。如果大模型企图将不符合逻辑的实体串联（例如 `[合同]-持股->[公司]`），底层引擎会直接拒绝并抛错。

```python
def create_core_edge_labels(session):
    print("正在建立【hold_share】持股关系骨干表...")
    
    hold_share_schema = {
        "label": "hold_share",
        "type": "EDGE",
        # 【核心约束】: 规定起点和终点必须是下面这两种组合
        "constraints": [
            ["Person", "Corp"],   # 自然人 持股 公司
            ["Corp", "Corp"]      # 公司 持股 公司
        ],
        "properties": [
            {
                # 【算法护栏】: 强制规定持股比例必须是 DOUBLE 浮点数
                # 逼迫大模型将 "百分之十" 转换为 0.10，确保下游 UBO 乘积运算不报错
                "name": "share", 
                "type": "DOUBLE", 
                "is_notnull": False
            }
        ]
    }
    
    cmd = f"CALL db.createEdgeLabelByJson('{json.dumps(hold_share_schema)}')"
    session.run(cmd)
    print("  -> hold_share 表建立成功！")
```

### 第四步：一键统筹与执行
在 Python 的入口点中，将上述流程串联，确保在一个独立的 Database Session 中干净利落地完成初始化。

```python
if __name__ == "__main__":
    print("=== 开始执行 TuGraph 核心骨干表(Ontology Seeding)注入 ===")
    try:
        # 使用默认的 default 图空间
        with driver.session(database="default") as session:
            create_core_vertex_labels(session)
            create_core_edge_labels(session)
        print("=== 核心骨干本体初始化完毕！地基已打好！ ===")
    except Exception as e:
        print(f"初始化失败，请检查 TuGraph 状态或 Schema 是否已存在: {e}")
    finally:
        driver.close()
```

---

## 3. 为什么不让大模型来写这一步？

1. **底线不容试错**：核心业务节点（如企业、资金流水、合同、人）是硬核图谱算法（如连通子图、深度优先搜索提取路径）的基石。如果让大模型自由发挥，它可能会因为上下游语境的变化，将 `Corp` 写成 `Company` 或者 `Enterprise`，导致下游 Python 分析脚本大面积崩溃。
2. **混合建模（Hybrid Modeling）才是未来**：通过本文档描述的 **Python 脚本**，人类架构师负责搭建“不可逾越的四面承重墙”（手写核心本体）；而后续业务中无穷无尽的长尾场景（如新增《车辆信息表》），则通过 MCP Tools 工具完全放权给大模型在墙内自由地“自主动态扩建”。

这种“**第 0 步法治兜底 + 第 1~5 步自治扩张**”的设计，正是本系统能够在商业场景中具备极高可用性的杀手锏。
