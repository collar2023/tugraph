"""
seed_data.py — D 路线 TuGraph 反欺诈 demo 数据初始化
======================================================

TuGraph 4.5.1, bolt://localhost:7687, default graph, user/pass: admin/YOUR_TUGRAPH_PASSWORD

Schema (简化版, 只用核心 3 个 tag + 2 个 edge 演示反欺诈核心场景):
  Vertex:  Applicant { applicant_id PRIMARY, name, age }
  Vertex:  Device    { device_id PRIMARY }
  Vertex:  Phone     { phone_number PRIMARY }
  Edge:    USED_DEVICE (Applicant) -> (Device)
  Edge:    WITH_PHONE  (Applicant) -> (Phone)

Demo 数据: 5 申请人 / 4 设备 / 3 手机号, 模拟 1 个共享设备的可疑团伙.
"""
import sys
from neo4j import GraphDatabase

URI = "bolt://localhost:7687"
USER = "admin"
PASSWORD = "YOUR_TUGRAPH_PASSWORD"
DB = "default"


def create_schema(session):
    print("==> 创建 schema (3 vertex label + 2 edge label)")
    cmds = [
        # Applicant: applicant_id (PRIMARY), name, age
        '''CALL db.createVertexLabelByJson('{"label":"Applicant","primary":"applicant_id","type":"VERTEX","properties":[{"name":"applicant_id","type":"STRING","is_primary":true,"is_unique":true,"is_notnull":true,"max_length":100},{"name":"name","type":"STRING","is_notnull":false},{"name":"age","type":"INT32","is_notnull":false}]}')''',
        # Device: device_id PRIMARY
        '''CALL db.createVertexLabelByJson('{"label":"Device","primary":"device_id","type":"VERTEX","properties":[{"name":"device_id","type":"STRING","is_primary":true,"is_unique":true,"is_notnull":true,"max_length":100}]}')''',
        # Phone: phone_number PRIMARY
        '''CALL db.createVertexLabelByJson('{"label":"Phone","primary":"phone_number","type":"VERTEX","properties":[{"name":"phone_number","type":"STRING","is_primary":true,"is_unique":true,"is_notnull":true,"max_length":100}]}')''',
        # USED_DEVICE edge
        '''CALL db.createEdgeLabelByJson('{"label":"USED_DEVICE","type":"EDGE","constraints":[["Applicant","Device"]],"properties":[]}')''',
        # WITH_PHONE edge
        '''CALL db.createEdgeLabelByJson('{"label":"WITH_PHONE","type":"EDGE","constraints":[["Applicant","Phone"]],"properties":[]}')''',
    ]
    names = ["Applicant", "Device", "Phone", "USED_DEVICE", "WITH_PHONE"]
    for name, cmd in zip(names, cmds):
        try:
            session.run(cmd)
            print(f"    ok: {name}")
        except Exception as e:
            msg = str(e)[:100]
            if "already exists" in msg.lower() or "exist" in msg.lower():
                print(f"    skip (exists): {name}")
            else:
                print(f"    FAIL: {name} -> {msg}")


def insert_data(session):
    print("==> 灌入 demo 数据")
    # 5 申请人
    for aid, name, age in [
        ("A001", "张三", 35),
        ("A002", "李四", 42),
        ("A003", "王五", 28),
        ("A004", "赵六", 51),
        ("A005", "钱七", 33),
    ]:
        session.run(
            "CREATE (n:Applicant {applicant_id:$aid, name:$name, age:$age})",
            aid=aid, name=name, age=age,
        )
    # 4 设备
    for did in ["D100", "D101", "D102", "D103"]:
        session.run("CREATE (n:Device {device_id:$did})", did=did)
    # 3 手机号
    for pn in ["13800000001", "13800000002", "13800000003"]:
        session.run("CREATE (n:Phone {phone_number:$pn})", pn=pn)
    # USED_DEVICE 关系 (含共享设备: D100/D102)
    for aid, did in [
        ("A001", "D100"), ("A001", "D101"),
        ("A002", "D100"), ("A002", "D102"),
        ("A003", "D102"),
        ("A004", "D103"),
    ]:
        session.run(
            "MATCH (a:Applicant {applicant_id:$aid}), (d:Device {device_id:$did}) "
            "CREATE (a)-[:USED_DEVICE]->(d)",
            aid=aid, did=did,
        )
    # WITH_PHONE 关系 (含共享手机号: 13800000001/13800000002)
    for aid, pn in [
        ("A001", "13800000001"),
        ("A002", "13800000001"),
        ("A003", "13800000002"),
        ("A004", "13800000003"),
        ("A005", "13800000002"),
    ]:
        session.run(
            "MATCH (a:Applicant {applicant_id:$aid}), (p:Phone {phone_number:$pn}) "
            "CREATE (a)-[:WITH_PHONE]->(p)",
            aid=aid, pn=pn,
        )
    print("    ok: 5 applicant + 4 device + 3 phone + 6 used_device + 5 with_phone")


def verify(session):
    print("==> 验证数据")
    print(f"    Applicant: {session.run('MATCH (a:Applicant) RETURN count(a) AS n').single()['n']}")
    print(f"    Device   : {session.run('MATCH (d:Device) RETURN count(d) AS n').single()['n']}")
    print(f"    Phone    : {session.run('MATCH (p:Phone) RETURN count(p) AS n').single()['n']}")
    print(f"    USED_DEVICE edges: {session.run('MATCH ()-[e:USED_DEVICE]->() RETURN count(e) AS n').single()['n']}")
    print(f"    WITH_PHONE edges : {session.run('MATCH ()-[e:WITH_PHONE]->() RETURN count(e) AS n').single()['n']}")


def main():
    print(">>> D 路线 TuGraph 反欺诈 demo 数据初始化")
    print(f">>> 连接: {URI}  user={USER}  graph={DB}")
    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    with driver.session(database=DB) as session:
        # 先清空 + 删 label (幂等)
        try:
            session.run("MATCH (n) DETACH DELETE n")
            print("==> 清空旧数据")
        except Exception as e:
            print(f"==> 清空数据失败 (忽略): {e}")
        for lbl in ["USED_DEVICE", "WITH_PHONE"]:
            try:
                session.run(f"CALL db.deleteLabel('EDGE', '{lbl}')")
            except Exception:
                pass
        for lbl in ["Applicant", "Device", "Phone"]:
            try:
                session.run(f"CALL db.deleteLabel('VERTEX', '{lbl}')")
            except Exception:
                pass
        create_schema(session)
        insert_data(session)
        verify(session)
    driver.close()
    print(">>> 完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
