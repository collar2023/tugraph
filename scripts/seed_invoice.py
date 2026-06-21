"""
scripts/seed_invoice.py - 财务发票核销场景骨干网构建脚本 (Ontology Seeding)
========================================================================
用于在 TuGraph 中强制注入财务审计（发票核销）的核心点边 Schema。
"""
import json
from neo4j import GraphDatabase

URI = "bolt://localhost:7687"
USER = "admin"
PASSWORD = "73@TuGraph"
DB = "default"

def ensure_label(session, label_type, label_name, schema_dict):
    """幂等创建 Label，如果已存在则忽略"""
    # TuGraph 获取已存在的 Schema
    try:
        if label_type.upper() == "VERTEX":
            cmd = f"CALL db.createVertexLabelByJson('{json.dumps(schema_dict)}')"
        else:
            cmd = f"CALL db.createEdgeLabelByJson('{json.dumps(schema_dict)}')"
        session.run(cmd)
        print(f"  -> {label_type} Label '{label_name}' 建立成功！")
    except Exception as e:
        # 如果是因为 Label 已存在报错，则跳过
        err_msg = str(e)
        if "already exists" in err_msg.lower() or "exist" in err_msg.lower():
            print(f"  -> {label_type} Label '{label_name}' 已存在，跳过。")
        else:
            print(f"  -> {label_type} Label '{label_name}' 建立失败: {e}")

def main():
    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    print("=== 开始执行 财务发票核销场景 核心骨干表(Ontology Seeding)注入 ===")
    
    with driver.session(database=DB) as session:
        # 1. 建立 Corp (企业) 骨干表
        corp_schema = {
            "label": "Corp",
            "primary": "corp_id",
            "type": "VERTEX",
            "properties": [
                {"name": "corp_id", "type": "STRING", "is_primary": True, "is_unique": True, "is_notnull": True, "max_length": 100},
                {"name": "name", "type": "STRING", "is_notnull": False}
            ]
        }
        ensure_label(session, "VERTEX", "Corp", corp_schema)

        # 2. 建立 Contract (合同) 骨干表
        contract_schema = {
            "label": "Contract",
            "primary": "contract_id",
            "type": "VERTEX",
            "properties": [
                {"name": "contract_id", "type": "STRING", "is_primary": True, "is_unique": True, "is_notnull": True, "max_length": 100},
                {"name": "name", "type": "STRING", "is_notnull": False},
                {"name": "amount", "type": "DOUBLE", "is_notnull": False}
            ]
        }
        ensure_label(session, "VERTEX", "Contract", contract_schema)

        # 3. 建立 Invoice (发票) 骨干表
        invoice_schema = {
            "label": "Invoice",
            "primary": "invoice_id",
            "type": "VERTEX",
            "properties": [
                {"name": "invoice_id", "type": "STRING", "is_primary": True, "is_unique": True, "is_notnull": True, "max_length": 100},
                {"name": "invoice_code", "type": "STRING", "is_notnull": False},
                {"name": "amount", "type": "DOUBLE", "is_notnull": False},
                {"name": "tax", "type": "DOUBLE", "is_notnull": False},
                {"name": "date", "type": "STRING", "is_notnull": False}
            ]
        }
        ensure_label(session, "VERTEX", "Invoice", invoice_schema)

        # 4. 建立 Payment (付款单/流水) 骨干表
        payment_schema = {
            "label": "Payment",
            "primary": "payment_id",
            "type": "VERTEX",
            "properties": [
                {"name": "payment_id", "type": "STRING", "is_primary": True, "is_unique": True, "is_notnull": True, "max_length": 100},
                {"name": "bank_flow_no", "type": "STRING", "is_notnull": False},
                {"name": "amount", "type": "DOUBLE", "is_notnull": False},
                {"name": "date", "type": "STRING", "is_notnull": False}
            ]
        }
        ensure_label(session, "VERTEX", "Payment", payment_schema)

        # 5. 建立边关系
        # ISSUED_BY: Invoice -> Corp (销售方开具发票)
        issued_by_schema = {
            "label": "ISSUED_BY",
            "type": "EDGE",
            "constraints": [["Invoice", "Corp"]],
            "properties": []
        }
        ensure_label(session, "EDGE", "ISSUED_BY", issued_by_schema)

        # ISSUED_TO: Invoice -> Corp (采购方收受发票)
        issued_to_schema = {
            "label": "ISSUED_TO",
            "type": "EDGE",
            "constraints": [["Invoice", "Corp"]],
            "properties": []
        }
        ensure_label(session, "EDGE", "ISSUED_TO", issued_to_schema)

        # PAID_BY: Payment -> Corp (买方付款)
        paid_by_schema = {
            "label": "PAID_BY",
            "type": "EDGE",
            "constraints": [["Payment", "Corp"]],
            "properties": []
        }
        ensure_label(session, "EDGE", "PAID_BY", paid_by_schema)

        # PAID_TO: Payment -> Corp (卖方收款)
        paid_to_schema = {
            "label": "PAID_TO",
            "type": "EDGE",
            "constraints": [["Payment", "Corp"]],
            "properties": []
        }
        ensure_label(session, "EDGE", "PAID_TO", paid_to_schema)

        # HAS_INVOICE: Contract -> Invoice (合同开具的发票)
        has_invoice_schema = {
            "label": "HAS_INVOICE",
            "type": "EDGE",
            "constraints": [["Contract", "Invoice"]],
            "properties": []
        }
        ensure_label(session, "EDGE", "HAS_INVOICE", has_invoice_schema)

        # ASSOCIATED_WITH: Payment -> Contract (付款单对应的合同)
        associated_with_schema = {
            "label": "ASSOCIATED_WITH",
            "type": "EDGE",
            "constraints": [["Payment", "Contract"]],
            "properties": []
        }
        ensure_label(session, "EDGE", "ASSOCIATED_WITH", associated_with_schema)

        # MATCHED_INVOICE: Payment -> Invoice (付款流水核销发票)
        matched_invoice_schema = {
            "label": "MATCHED_INVOICE",
            "type": "EDGE",
            "constraints": [["Payment", "Invoice"]],
            "properties": [
                {"name": "matched_amount", "type": "DOUBLE", "is_notnull": False}
            ]
        }
        ensure_label(session, "EDGE", "MATCHED_INVOICE", matched_invoice_schema)

    driver.close()
    print("=== 财务发票核销场景 骨干表 Schema 初始化完毕！ ===")

if __name__ == "__main__":
    main()
