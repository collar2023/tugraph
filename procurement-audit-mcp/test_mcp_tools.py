import sys
import os
import json
from neo4j import GraphDatabase

# 兼容同级目录下 import
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import mcp_server

def generate_mock_csv():
    """生成一个高难度的车辆物流原始数据样本 CSV，用于自适应建模测试"""
    csv_content = """logistics_id,carrier_name,vehicle_license,freight_cost,destination,dispatch_date
L-2026-901,德邦物流,粤B-12345,15000.0,上海张江,2026-06-21
L-2026-902,顺丰速运,沪A-88888,28000.0,北京中关村,2026-06-22
L-2026-903,京东物流,京C-99999,12000.0,广州黄埔,2026-06-23
"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vehicle_logistics_sample.csv")
    with open(path, "w", encoding="utf-8") as f:
        f.write(csv_content)
    return path

def test_adaptive_modeling():
    print("=============================================================")
    print("🚀 动态自适应建模高难度端侧测试 (Schema Self-Evolution)")
    print("=============================================================")

    # Step 0: 清理以往测试生成的旧标签和数据，实现从干净状态测试
    print("🧹 Step 0: 清理以往测试生成的数据与 Label (Carrier, Vehicle, DISPATCHED_TO)...")
    try:
        mcp_server.execute_cypher("MATCH ()-[r:DISPATCHED_TO]->() DELETE r")
        mcp_server.execute_cypher("MATCH (c:Carrier) DELETE c")
        mcp_server.execute_cypher("MATCH (v:Vehicle) DELETE v")
        
        driver = mcp_server.get_tugraph_driver()
        with driver.session(database=mcp_server.TUGRAPH_DB) as s:
            try:
                s.run("CALL db.deleteLabel('EDGE', 'DISPATCHED_TO')")
                print("  -> 清理边 [DISPATCHED_TO] 成功")
            except Exception: pass
            try:
                s.run("CALL db.deleteLabel('VERTEX', 'Carrier')")
                print("  -> 清理点 [Carrier] 成功")
            except Exception: pass
            try:
                s.run("CALL db.deleteLabel('VERTEX', 'Vehicle')")
                print("  -> 清理点 [Vehicle] 成功")
            except Exception: pass
        driver.close()
        
        # 清理幂等缓存文件
        cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scratch", "mcp_writes.jsonl")
        if os.path.exists(cache_path):
            os.remove(cache_path)
            print("  -> 清理幂等缓存文件成功")
    except Exception as e:
        print(f"  -> 清理准备工作警告: {e}")

    # Step 1: 生成并探索原始数据
    print("\n📂 Step 1: 扫描并分析新上传的车辆物流原始数据...")
    csv_path = generate_mock_csv()
    sample_res = mcp_server.get_raw_data_sample(csv_path, limit=2)
    print(f"  -> 原始数据特征：\n{sample_res}")
    
    # Step 2: 获取当前已有的 Schema 地图
    print("\n🗺️ Step 2: 获取当前 TuGraph 图谱已有 Schema，检测是否存在冲突...")
    old_schema = json.loads(mcp_server.get_graph_schema())
    schema_list = old_schema.get("schema", [])
    old_vertex_labels = [item["label"] for item in schema_list if item.get("type") == "VERTEX"]
    old_edge_labels = [item["label"] for item in schema_list if item.get("type") == "EDGE"]
    print(f"  -> 已有点标签: {old_vertex_labels}")
    print(f"  -> 已有边标签: {old_edge_labels}")

    # Step 3: 自适应建模决策与动态注入 DDL
    print("\n🛠️ Step 3: 大模型自适应推导，通过 create_graph_label 动态注入新 Schema...")
    
    # 动态注入新顶点：Carrier (承运商)
    if "Carrier" not in old_vertex_labels:
        print("  -> 注入新点标签 [Carrier]...")
        carrier_props = '[{"name":"carrier_id","type":"STRING"},{"name":"name","type":"STRING"}]'
        res_carrier = mcp_server.create_graph_label(
            label_type="VERTEX",
            label_name="Carrier",
            primary_key="carrier_id",
            properties_json=carrier_props
        )
        print(f"     结果: {res_carrier}")
    else:
        print("  -> 点 [Carrier] 已存在，跳过注入。")

    # 动态注入新顶点：Vehicle (车辆)
    if "Vehicle" not in old_vertex_labels:
        print("  -> 注入新点标签 [Vehicle]...")
        vehicle_props = '[{"name":"vehicle_id","type":"STRING"},{"name":"license_plate","type":"STRING"}]'
        res_vehicle = mcp_server.create_graph_label(
            label_type="VERTEX",
            label_name="Vehicle",
            primary_key="vehicle_id",
            properties_json=vehicle_props
        )
        print(f"     结果: {res_vehicle}")
    else:
        print("  -> 点 [Vehicle] 已存在，跳过注入。")

    # 动态注入新关系边：DISPATCHED_TO (派车去往，约束：Carrier -> Vehicle)
    if "DISPATCHED_TO" not in old_edge_labels:
        print("  -> 注入新边标签 [DISPATCHED_TO] (Carrier -> Vehicle)...")
        edge_props = '[{"name":"freight","type":"DOUBLE"},{"name":"date","type":"STRING"}]'
        edge_constraints = '[["Carrier","Vehicle"]]'
        res_edge = mcp_server.create_graph_label(
            label_type="EDGE",
            label_name="DISPATCHED_TO",
            primary_key="",
            properties_json=edge_props,
            constraints_json=edge_constraints
        )
        print(f"     结果: {res_edge}")
    else:
        print("  -> 边 [DISPATCHED_TO] 已存在，跳过注入。")

    # Step 4: 重新拉取 Schema 验证自适应建模结果
    print("\n🔍 Step 4: 重新读取图谱 Schema，校验新本体是否成功持久化...")
    new_schema = json.loads(mcp_server.get_graph_schema())
    new_schema_list = new_schema.get("schema", [])
    new_vertex_labels = [item["label"] for item in new_schema_list if item.get("type") == "VERTEX"]
    new_edge_labels = [item["label"] for item in new_schema_list if item.get("type") == "EDGE"]
    print(f"  -> 最新点标签: {new_vertex_labels}")
    print(f"  -> 最新边标签: {new_edge_labels}")
    
    assert "Carrier" in new_vertex_labels, "测试失败：Carrier 标签未建立"
    assert "Vehicle" in new_vertex_labels, "测试失败：Vehicle 标签未建立"
    assert "DISPATCHED_TO" in new_edge_labels, "测试失败：DISPATCHED_TO 边未建立"
    print("  -> 校验成功！第一板块自适应动态建模端侧测试通过！")

    # Step 5: 测试关系批量灌入 (Category 2) 是否能平滑识别新 Schema
    print("\n📦 Step 5: 测试第二板块，向新 Schema 中批量灌入事实关系数据...")
    
    # 模拟数据加载，前置创建 Carrier 和 Vehicle 点（直接用 Neo4j driver，不通过带防爆拦截的 execute_cypher）
    driver = mcp_server.get_tugraph_driver()
    with driver.session(database=mcp_server.TUGRAPH_DB) as s:
        s.run("CREATE (c:Carrier {carrier_id: 'C-DEBANG', name: '德邦物流'})")
        s.run("CREATE (v:Vehicle {vehicle_id: 'V-B12345', license_plate: '粤B-12345'})")
    driver.close()
    
    relationships = [
        {
            "src_id": "C-DEBANG",
            "src_label": "Carrier",
            "dst_id": "V-B12345",
            "dst_label": "Vehicle",
            "relation": "DISPATCHED_TO",
            "properties": {"freight": 15000.0, "date": "2026-06-21"}
        }
    ]
    res_insert = mcp_server.bulk_insert_relationships(json.dumps(relationships))
    print(f"  -> 批量写入返回：\n{res_insert}")

    # Step 6: 验证写入路径
    print("\n📈 Step 6: 运行 Cypher 图路径查询，验证新模型下的数据关系图谱...")
    verify_cypher = (
        "MATCH (c:Carrier)-[r:DISPATCHED_TO]->(v:Vehicle) "
        "RETURN c.name AS carrier, r.freight AS cost, v.license_plate AS license"
    )
    res_query = mcp_server.execute_cypher(verify_cypher)
    print(f"  -> 查询结果：\n{res_query}")
    print("=============================================================")

if __name__ == "__main__":
    test_adaptive_modeling()
