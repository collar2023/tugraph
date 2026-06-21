"""
seed_shareholding_tugraph.py — 将股权穿透/最终受益人(UBO)场景测试数据灌入 TuGraph
========================================================================

连接: bolt://localhost:7687, DB: default, admin / 73@TuGraph
"""
import sys
from neo4j import GraphDatabase

URI = "bolt://localhost:7687"
USER = "admin"
PASSWORD = "73@TuGraph"
DB = "default"

def create_schema(driver):
    print("==> 创建 TuGraph 股权场景 Schema (2 vertex labels + 1 edge label)")
    cmds = [
        # Person: person_id (PRIMARY), name
        '''CALL db.createVertexLabelByJson(\'{"label":"Person","primary":"person_id","type":"VERTEX","properties":[{"name":"person_id","type":"STRING","is_primary":true,"is_unique":true,"is_notnull":true,"max_length":100},{"name":"name","type":"STRING","is_notnull":false}]}\')''',
        # Corp: corp_id (PRIMARY), name
        '''CALL db.createVertexLabelByJson(\'{"label":"Corp","primary":"corp_id","type":"VERTEX","properties":[{"name":"corp_id","type":"STRING","is_primary":true,"is_unique":true,"is_notnull":true,"max_length":100},{"name":"name","type":"STRING","is_notnull":false}]}\')''',
        # hold_share edge (支持 Person->Corp 和 Corp->Corp)
        '''CALL db.createEdgeLabelByJson(\'{"label":"hold_share","type":"EDGE","constraints":[["Person","Corp"],["Corp","Corp"]],"properties":[{"name":"share","type":"DOUBLE","is_notnull":false}]}\')'''
    ]
    names = ["Person", "Corp", "hold_share"]
    for name, cmd in zip(names, cmds):
        try:
            with driver.session(database=DB) as session:
                session.run(cmd)
                print(f"    ok: {name}")
        except Exception as e:
            print(f"    FAIL: {name} -> {e}")

def insert_data(session):
    print("==> 灌入 Hicks PLC 股权穿透数据 (包含 1 级直接持股与 2 级间接持股)")
    # 1. 灌入公司 (Corp)
    corps = [
        ("c_102", "Hicks PLC"),
        ("c_108", "Ortega-Hoffman"),
        ("c_109", "Hall-Wilson"),
        ("c_113", "Smith, Dawson and Williams"),
        ("c_114", "Bean, Jones and Benton"),
        ("c_465", "Moore-Kim"),
    ]
    for cid, name in corps:
        session.run("CREATE (n:Corp {corp_id:$cid, name:$name})", cid=cid, name=name)
        
    # 2. 灌入自然人 (Person)
    persons = [
        ("p_1031", "Jasmine Bell"),
        ("p_1130", "Christine Lee"),
        ("p_1725", "Lori Richardson"),
        ("p_2309", "Miguel Turner"),
        ("p_3681", "Laura Clark"),
        ("p_4100", "Marissa Miller"),
        ("p_538", "Kathryn Chavez"),
        ("p_999", "Alyssa Allen"),
    ]
    for pid, name in persons:
        session.run("CREATE (n:Person {person_id:$pid, name:$name})", pid=pid, name=name)
        
    # 3. 建立持股关系 (hold_share)
    # 自然人直接持股
    direct_shares = [
        ("p_1031", "c_102", 3.0),
        ("p_1130", "c_102", 13.0),
        ("p_1725", "c_102", 5.0),
        ("p_2309", "c_102", 3.0),
        ("p_3681", "c_102", 14.0),
        ("p_4100", "c_102", 10.0),
        ("p_538", "c_102", 9.0),
        ("p_999", "c_465", 20.0), # 间接大股东：Alyssa Allen 持股 Moore-Kim 20%
    ]
    for pid, cid, share in direct_shares:
        session.run(
            "MATCH (p:Person {person_id:$pid}), (c:Corp {corp_id:$cid}) "
            "CREATE (p)-[:hold_share {share:$share}]->(c)",
            pid=pid, cid=cid, share=share
        )
        
    # 公司持股公司 (间接持股)
    corp_shares = [
        ("c_465", "c_102", 15.0), # Moore-Kim 持股 Hicks PLC 15%
    ]
    for c1_id, c2_id, share in corp_shares:
        session.run(
            "MATCH (c1:Corp {corp_id:$c1_id}), (c2:Corp {corp_id:$c2_id}) "
            "CREATE (c1)-[:hold_share {share:$share}]->(c2)",
            c1_id=c1_id, c2_id=c2_id, share=share
        )
        
    print("    ok: 6 corp + 8 person + 9 hold_share relationships")

def verify(session):
    print("==> 验证 TuGraph 股权数据")
    print(f"    Person       : {session.run('MATCH (p:Person) RETURN count(p) AS n').single()['n']}")
    print(f"    Corp         : {session.run('MATCH (c:Corp) RETURN count(c) AS n').single()['n']}")
    print(f"    hold_share   : {session.run('MATCH ()-[e:hold_share]->() RETURN count(e) AS n').single()['n']}")

def main():
    print(">>> TuGraph 股权数据初始化")
    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    
    # 1. 幂等清理旧股权数据
    print("==> 幂等清理旧股权数据")
    with driver.session(database=DB) as session:
        try:
            session.run("MATCH ()-[e:hold_share]->() DELETE e")
            session.run("MATCH (p:Person) DELETE p")
            session.run("MATCH (c:Corp) DELETE c")
        except Exception as e:
            print(f"    清理警告: {e}")
            
    for lbl in ["hold_share"]:
        try:
            with driver.session(database=DB) as session:
                session.run(f"CALL db.deleteLabel(\'EDGE\', \'{lbl}\')")
        except Exception:
            pass
            
    for lbl in ["Person", "Corp"]:
        try:
            with driver.session(database=DB) as session:
                session.run(f"CALL db.deleteLabel(\'VERTEX\', \'{lbl}\')")
        except Exception:
            pass
            
    create_schema(driver)
    
    with driver.session(database=DB) as session:
        insert_data(session)
        verify(session)
        
    driver.close()
    print(">>> 股权数据灌入完成")
    return 0

if __name__ == "__main__":
    sys.exit(main())
