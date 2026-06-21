"""
seed_procurement_data.py — 采购合同审计场景的测试数据灌入与 DDL 初始化脚本
====================================================================
"""
import os
import sys
import pandas as pd
from neo4j import GraphDatabase

# ---------- 配置 ----------
URI = "bolt://localhost:7687"
USER = "admin"
PASSWORD = "73@TuGraph"
DB = "default"

def generate_csv_sample():
    print("==> 1. 生成原始采购合同 CSV 样本文件")
    data = {
        "contract_id": ["CT-2026-001", "CT-2026-002", "CT-2026-003", "CT-2026-004", "CT-2026-005"],
        "title": ["原材料钢材采购合同", "行政办公用品采购协议", "服务器托管运维合同", "物流仓储外包协议", "芯片测试设备购买合同"],
        "supplier_name": ["Moore-Kim", "Bean, Jones and Benton", "Ortega-Hoffman", "Smith, Dawson and Williams", "Hicks PLC"],
        "amount": [5000000.0, 150000.0, 1200000.0, 850000.0, 12000000.0],
        "payment_terms": ["首付30%，尾款验收后支付", "验收合格后一次性付清", "按季度预付", "月度结算", "首付80%（违规比例），发货前支付20%"],
        "approver_name": ["Jasmine Bell", "Laura Clark", "Kathryn Chavez", "Miguel Turner", "Christine Lee"]
    }
    df = pd.DataFrame(data)
    target_path = "/home/ubuntu/tugraph/procurement-audit-mcp/procurement_contracts_sample.csv"
    df.to_csv(target_path, index=False, encoding="utf-8")
    print(f"    生成成功: {target_path}")

def cleanup_schema(driver):
    print("==> 2. 幂等清理旧采购场景数据及 Label")
    # 关键 (2026-06-20 修)：hold_share 属于股权场景，不能在这里删！
    # 2026-06-19 复测发现的 bug：procurement seed 删了股权 hold_share 9 条边
    # 正确做法：只删本场景（procurement）的 label，不碰其他场景
    labels = ["sign_contract", "approve_by"]
    vertex_labels = ["Contract"]
    
    with driver.session(database=DB) as session:
        try:
            session.run("MATCH ()-[e:sign_contract]->() DELETE e")
            session.run("MATCH ()-[e:approve_by]->() DELETE e")
            session.run("MATCH (c:Contract) DELETE c")
        except Exception as e:
            print(f"    清理警告: {e}")
            
    for lbl in labels:
        try:
            with driver.session(database=DB) as session:
                session.run(f"CALL db.deleteLabel('EDGE', '{lbl}')")
        except Exception: pass
        
    for lbl in ["Contract"]:
        try:
            with driver.session(database=DB) as session:
                session.run(f"CALL db.deleteLabel('VERTEX', '{lbl}')")
        except Exception: pass

def create_schema(driver):
    print("==> 3. 创建采购场景本体 Schema (Vertex: Contract, Edges: sign_contract, approve_by)")
    
    # 我们复用已经灌好的 Person 和 Corp，仅为 Contract 实体及两条关系线建立 Schema
    cmds = [
        # Contract vertex: contract_id (PRIMARY), amount, title, payment_terms
        '''CALL db.createVertexLabelByJson(\'{"label":"Contract","primary":"contract_id","type":"VERTEX","properties":[{"name":"contract_id","type":"STRING","is_primary":true,"is_unique":true,"is_notnull":true,"max_length":100},{"name":"amount","type":"DOUBLE","is_notnull":false},{"name":"title","type":"STRING","is_notnull":false},{"name":"payment_terms","type":"STRING","is_notnull":false}]}\')''',
        # sign_contract edge: Corp -> Contract
        '''CALL db.createEdgeLabelByJson(\'{"label":"sign_contract","type":"EDGE","constraints":[["Corp","Contract"]]}\')''',
        # approve_by edge: Contract -> Person
        '''CALL db.createEdgeLabelByJson(\'{"label":"approve_by","type":"EDGE","constraints":[["Contract","Person"]]}\')'''
    ]
    names = ["Contract", "sign_contract", "approve_by"]
    for name, cmd in zip(names, cmds):
        try:
            with driver.session(database=DB) as session:
                session.run(cmd)
                print(f"    ok: {name}")
        except Exception as e:
            print(f"    FAIL: {name} -> {e}")

def insert_data(session):
    print("==> 4. 灌入采购合同关系网数据")
    
    # 建立合同节点
    contracts = [
        ("CT-2026-001", 5000000.0, "原材料钢材采购合同", "首付30%，尾款验收后支付"),
        ("CT-2026-005", 12000000.0, "芯片测试设备购买合同", "首付80%（违规比例），发货前支付20%"),
    ]
    for cid, amount, title, terms in contracts:
        session.run(
            "CREATE (n:Contract {contract_id:$cid, amount:$amount, title:$title, payment_terms:$terms})",
            cid=cid, amount=amount, title=title, terms=terms
        )
        
    # 建立签署关系 (Corp -> Contract)
    # c_465 (Moore-Kim) 签署了 CT-2026-001
    # c_102 (Hicks PLC) 签署了 CT-2026-005
    session.run("MATCH (c:Corp {corp_id:'c_465'}), (t:Contract {contract_id:'CT-2026-001'}) CREATE (c)-[:sign_contract]->(t)")
    session.run("MATCH (c:Corp {corp_id:'c_102'}), (t:Contract {contract_id:'CT-2026-005'}) CREATE (c)-[:sign_contract]->(t)")
    
    # 建立审批人关系 (Contract -> Person)
    # CT-2026-001 由 p_1031 (Jasmine Bell) 审批
    # CT-2026-005 由 p_1130 (Christine Lee) 审批
    session.run("MATCH (t:Contract {contract_id:'CT-2026-001'}), (p:Person {person_id:'p_1031'}) CREATE (t)-[:approve_by]->(p)")
    session.run("MATCH (t:Contract {contract_id:'CT-2026-005'}), (p:Person {person_id:'p_1130'}) CREATE (t)-[:approve_by]->(p)")
    
    print("    ok: 2 contracts + 2 sign_contract + 2 approve_by relations")

def verify(session):
    print("==> 5. 验证采购场景数据")
    print(f"    Contract      : {session.run('MATCH (c:Contract) RETURN count(c) AS n').single()['n']}")
    print(f"    sign_contract : {session.run('MATCH ()-[e:sign_contract]->() RETURN count(e) AS n').single()['n']}")
    print(f"    approve_by    : {session.run('MATCH ()-[e:approve_by]->() RETURN count(e) AS n').single()['n']}")

def main():
    print(">>> 启动采购合同场景测试数据初始化...")
    generate_csv_sample()
    
    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    cleanup_schema(driver)
    create_schema(driver)
    
    with driver.session(database=DB) as session:
        insert_data(session)
        verify(session)
        
    driver.close()
    print(">>> 采购合同数据灌入完成")
    return 0

if __name__ == "__main__":
    sys.exit(main())
