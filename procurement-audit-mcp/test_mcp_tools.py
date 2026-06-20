import sys
import os
import json

# 兼容同级目录下 import
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import mcp_server

def test_mcp():
    print("=============================================================")
    print("🚀 采购合同审计场景 MCP 工具直连测试 (TuGraph & Qdrant)")
    print("=============================================================")
    
    # 1. 测试 数据探索工具: get_raw_data_sample
    print("🔎 1. 测试数据探索工具 [get_raw_data_sample]")
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "procurement_contracts_sample.csv")
    res1 = mcp_server.get_raw_data_sample(csv_path, limit=2)
    print(res1)
    print("-" * 60)

    # 2. 测试 本体建模工具: get_graph_schema
    print("🕸️ 2. 测试本体建模工具 [get_graph_schema]")
    res2 = mcp_server.get_graph_schema()
    try:
        schema_json = json.loads(res2)
        print(json.dumps(schema_json, ensure_ascii=False, indent=2))
    except Exception:
        print(res2)
    print("-" * 60)

    # 3. 测试 图谱查询工具: execute_cypher
    print("⚙️ 3. 测试图谱查询工具 [execute_cypher]")
    query = (
        "MATCH (c:Corp)-[:sign_contract]->(t:Contract)-[:approve_by]->(p:Person) "
        "RETURN c.name AS corp_name, t.title AS contract_title, t.amount AS amount, p.name AS approver_name"
    )
    res3 = mcp_server.execute_cypher(query)
    try:
        rows_json = json.loads(res3)
        print(json.dumps(rows_json, ensure_ascii=False, indent=2))
    except Exception:
        print(res3)
    print("-" * 60)

    # 4. 测试 向量比对工具: search_vector_news
    print("📄 4. 测试向量检索工具 [search_vector_news]")
    res4 = mcp_server.search_vector_news("Hicks PLC", limit=2)
    try:
        vector_json = json.loads(res4)
        print(json.dumps(vector_json, ensure_ascii=False, indent=2))
    except Exception:
        print(res4)
    print("=============================================================")

if __name__ == "__main__":
    test_mcp()
