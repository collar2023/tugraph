"""
agent_llm.py — 直连主方案 MiniMax 大模型驱动的反欺诈 Agent
============================================================

通过 MiniMax API + LangGraph + TuGraph 直连：
  1. 意图分类与参数提取 (利用 MiniMax 提取)
  2. Cypher 生成 (预定义模板 + LLM 动态生成混合模式)
  3. TuGraph Bolt 7687 执行
  4. 业务化回复 (利用 MiniMax 结合数据进行深度研判)

运行前请设置环境变量:
  export MINIMAX_API_KEY="您的 MiniMax API Key"
  export MINIMAX_MODEL="MiniMax-M3"  (可选，默认 MiniMax-M3)
"""
from __future__ import annotations
import os
import json
import re
from typing import TypedDict, Any
from neo4j import GraphDatabase
from openai import OpenAI
from langgraph.graph import END, START, StateGraph

# ---------- TuGraph 连接 ----------
TUGRAPH_URI = "bolt://localhost:7687"
TUGRAPH_USER = "admin"
TUGRAPH_PASSWORD = "73@TuGraph"
TUGRAPH_DB = "default"

# ---------- MiniMax 客户端初始化 ----------
def get_minimax_client() -> tuple[OpenAI, str]:
    api_key = os.environ.get("MINIMAX_API_KEY")
    if not api_key:
        # 如果未设置，提示并使用占位符，以便报错时用户知道怎么解决
        raise ValueError(
            "请设置 MINIMAX_API_KEY 环境变量!\n"
            "例如: export MINIMAX_API_KEY='您的MiniMax API Key'"
        )
    
    base_url = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1")
    model = os.environ.get("MINIMAX_MODEL", "MiniMax-M3")
    
    client = OpenAI(base_url=base_url, api_key=api_key)
    return client, model

# ---------- State 定义 ----------
class FraudState(TypedDict):
    question: str
    intent: str
    extracted_params: dict
    cypher: str
    rows: list
    raw_count: int
    final_report: str
    confidence: int  # 0-100
    error: str

# ---------- 领域本体定义 (Ontology) ----------
DOMAIN_ONTOLOGY = {
    "entities": {
        "Applicant": {
            "description": "贷款申请人",
            "properties": {
                "applicant_id": "STRING, 唯一编号 (如 A001)",
                "name": "STRING, 申请人姓名",
                "age": "INT32, 年龄"
            }
        },
        "Device": {
            "description": "硬件设备",
            "properties": {
                "device_id": "STRING, 设备标识码 (如 D100)"
            }
        },
        "Phone": {
            "description": "手机号",
            "properties": {
                "phone_number": "STRING, 11位手机号码"
            }
        }
    },
    "relations": [
        {"source": "Applicant", "edge": "USED_DEVICE", "target": "Device", "description": "申请人使用过某设备"},
        {"source": "Applicant", "edge": "WITH_PHONE", "target": "Phone", "description": "申请人绑定某手机号"}
    ]
}

# ---------- 6 个预定义意图模板 (经典风控查询) ----------
INTENT_TEMPLATES = {
    "list_applicants": {
        "description": "列出所有申请人",
        "template": "MATCH (a:Applicant) RETURN a.applicant_id AS id, a.name AS name, a.age AS age ORDER BY a.applicant_id",
    },
    "shared_device": {
        "description": "找出共用设备的所有申请人 (核心反欺诈团伙分析)",
        "template": (
            "MATCH (a1:Applicant)-[:USED_DEVICE]->(d:Device)<-[:USED_DEVICE]-(a2:Applicant) "
            "WHERE a1.applicant_id < a2.applicant_id "
            "RETURN d.device_id AS device, a1.name AS p1, a1.applicant_id AS p1_id, "
            "       a2.name AS p2, a2.applicant_id AS p2_id "
            "ORDER BY device"
        ),
    },
    "shared_phone": {
        "description": "找出共用手机号的所有申请人 (核心反欺诈团伙分析)",
        "template": (
            "MATCH (a1:Applicant)-[:WITH_PHONE]->(p:Phone)<-[:WITH_PHONE]-(a2:Applicant) "
            "WHERE a1.applicant_id < a2.applicant_id "
            "RETURN p.phone_number AS phone, a1.name AS p1, a1.applicant_id AS p1_id, "
            "       a2.name AS p2, a2.applicant_id AS p2_id "
            "ORDER BY phone"
        ),
    },
    "applicant_devices": {
        "description": "查询某个申请人(按姓名 name)用过的所有设备",
        "template": (
            "MATCH (a:Applicant {name: $name})-[:USED_DEVICE]->(d:Device) "
            "RETURN a.applicant_id AS id, a.name AS name, collect(d.device_id) AS devices"
        ),
        "params": ["name"],
    },
    "applicant_phones": {
        "description": "查询某个申请人(按姓名 name)的所有手机号",
        "template": (
            "MATCH (a:Applicant {name: $name})-[:WITH_PHONE]->(p:Phone) "
            "RETURN a.applicant_id AS id, a.name AS name, collect(p.phone_number) AS phones"
        ),
        "params": ["name"],
    },
    "device_users": {
        "description": "查询使用某台设备(按设备ID did)的所有申请人",
        "template": (
            "MATCH (a:Applicant)-[:USED_DEVICE]->(d:Device {device_id: $did}) "
            "RETURN d.device_id AS device, collect(a.applicant_id) AS user_ids, collect(a.name) AS user_names"
        ),
        "params": ["did"],
    },
}

# ---------- Node 1: 意图分类与参数提取 ----------
def node_intent_classify(state: FraudState) -> dict:
    """利用 MiniMax 大模型进行实体提取与意图识别"""
    question = state["question"]
    
    try:
        client, model = get_minimax_client()
        
        prompt = f"""
        你是一个精通反欺诈领域的本体分类器。请阅读以下【本体结构】，分析用户问题的【查询意图】和【需要提取的参数】。
        
        【本体结构】:
        {json.dumps(DOMAIN_ONTOLOGY, ensure_ascii=False, indent=2)}
        
        【任务要求】:
        意图分类值必须是以下之一:
        - "list_applicants" (列出所有申请人列表)
        - "shared_device" (查询共享/共用设备的可疑申请人)
        - "shared_phone" (查询共享/共用手机号的申请人)
        - "applicant_devices" (查询特定申请人使用过的设备，需要参数 name)
        - "applicant_phones" (查询特定申请人绑定的所有手机号，需要参数 name)
        - "device_users" (根据设备 ID 查询使用用户，需要参数 did)
        - "unknown" (无法匹配以上意图)
        
        提取参数时：
        - 若属于个人查询，提取人名（如 "张三"），存入参数 key "name"
        - 若属于设备查询，提取设备编号（如 "D100"），存入参数 key "did"
        
        请严格以 JSON 格式输出，不要任何 Markdown 包装（不要 ```json），确保输出可以直接被 json.loads 解析。
        示例:
        {{"intent": "applicant_devices", "params": {{"name": "张三"}}, "confidence": 95}}
        
        用户提问: "{question}"
        JSON输出:"""
        
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )
        
        content = response.choices[0].message.content.strip()
        # 清洗可能存在的 reasoning think 标记
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        # 清洗可能存在的 markdown 代码块包裹
        content = re.sub(r"^```json\s*", "", content, flags=re.IGNORECASE)
        content = re.sub(r"\s*```$", "", content, flags=re.IGNORECASE)
        
        data = json.loads(content)
        print(f"  🧠 [MiniMax Intent] intent='{data.get('intent')}'  params={data.get('params')}")
        
        return {
            "intent": data.get("intent", "unknown"),
            "extracted_params": data.get("params", {}),
            "confidence": data.get("confidence", 70)
        }
    except Exception as e:
        print(f"  ❌ [MiniMax Classify Error] {e}，降级回退到规则匹配。")
        # 降级备用规则匹配
        q_lower = question.lower()
        intent = "list_applicants"
        params = {}
        if "共用" in question or "共享" in question or "share" in q_lower:
            if "设备" in question or "device" in q_lower:
                intent = "shared_device"
            else:
                intent = "shared_phone"
        elif "设备" in question and "谁" in question:
            intent = "device_users"
            m = re.search(r"D\d+", question)
            if m: params["did"] = m.group(0)
        elif "设备" in question:
            intent = "applicant_devices"
            for name in ["张三", "李四", "王五", "赵六", "钱七"]:
                if name in question: params["name"] = name
        elif "手机" in question:
            intent = "applicant_phones"
            for name in ["张三", "李四", "王五", "赵六", "钱七"]:
                if name in question: params["name"] = name
        
        return {"intent": intent, "extracted_params": params, "confidence": 50}

# ---------- Node 2: Cypher 生成 ----------
def node_cypher_gen(state: FraudState) -> dict:
    """根据意图模板 + 参数生成 Cypher 语句。对于未知意图，调用 MiniMax 动态生成。"""
    intent = state["intent"]
    params = state.get("extracted_params", {})
    
    # 1. 如果匹配到了预定义模板，直接生成参数化 Cypher（准确度 100%）
    if intent in INTENT_TEMPLATES:
        tpl = INTENT_TEMPLATES[intent]
        cypher = tpl["template"]
        print(f"  📝 [CypherGen] 使用预置本体模板: {intent}")
        return {"cypher": cypher}
    
    # 2. 如果属于未知意图，让 MiniMax 动态翻译生成 Cypher (Text-to-Cypher)
    print("  📝 [CypherGen] 未知意图，调用 MiniMax 动态生成 Cypher...")
    try:
        client, model = get_minimax_client()
        
        prompt = f"""
        你是一个精通图数据库 Cypher 语法的本体工程师。请根据【本体结构】，将用户的自然语言问题翻译成合法的 Cypher 查询。
        我们的图数据库是 TuGraph，支持标准 Neo4j 风格的 Cypher 语法。
        
        【本体结构】:
        {json.dumps(DOMAIN_ONTOLOGY, ensure_ascii=False, indent=2)}
        
        【限制条件】:
        - 仅输出一条 Cypher 语句，不要有任何 Markdown 块包裹（不要 ```cypher 或 ```）。
        - 只能使用本体中定义的 Label (Applicant, Device, Phone) 和 Edge (USED_DEVICE, WITH_PHONE)。
        - 过滤条件应直接写入查询中（例如: MATCH (a:Applicant {{name: '张三'}})...）。
        
        用户提问: "{state["question"]}"
        Cypher查询:"""
        
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )
        
        cypher = response.choices[0].message.content.strip()
        # 清洗可能存在的 reasoning think 标记
        cypher = re.sub(r"<think>.*?</think>", "", cypher, flags=re.DOTALL).strip()
        cypher = re.sub(r"^```cypher\s*", "", cypher, flags=re.IGNORECASE)
        cypher = re.sub(r"^```\s*", "", cypher, flags=re.IGNORECASE)
        cypher = re.sub(r"\s*```$", "", cypher, flags=re.IGNORECASE)
        
        print(f"  📝 [MiniMax Cypher] 动态生成: {cypher}")
        return {"cypher": cypher}
        
    except Exception as e:
        print(f"  ❌ [MiniMax Cypher Error] {e}。降级回退到全量列表查询。")
        return {"cypher": INTENT_TEMPLATES["list_applicants"]["template"]}

# ---------- Helper: TuGraph List Parser ----------
def parse_tugraph_list(val: Any) -> list[str]:
    if isinstance(val, list):
        return val
    if isinstance(val, str) and val.startswith("[") and val.endswith("]"):
        return [x.strip() for x in val[1:-1].split(",") if x.strip()]
    return [str(val)] if val is not None else []

# ---------- Node 3: TuGraph 执行 ----------
def node_cypher_exec(state: FraudState) -> dict:
    """直连 TuGraph 运行 Cypher 语句，并传入参数"""
    cypher = state["cypher"]
    params = state.get("extracted_params", {})
    
    driver = GraphDatabase.driver(TUGRAPH_URI, auth=(TUGRAPH_USER, TUGRAPH_PASSWORD))
    try:
        with driver.session(database=TUGRAPH_DB) as session:
            result = session.run(cypher, **params)
            rows = [dict(record) for record in result]
            
            # 解决 TuGraph List 序列化与 Map 嵌套不支持的 Quirks
            for row in rows:
                if "user_ids" in row and "user_names" in row:
                    ids = parse_tugraph_list(row["user_ids"])
                    names = parse_tugraph_list(row["user_names"])
                    row["users"] = [{"id": i, "name": n} for i, n in zip(ids, names)]
                if "devices" in row:
                    row["devices"] = parse_tugraph_list(row["devices"])
                if "phones" in row:
                    row["phones"] = parse_tugraph_list(row["phones"])
                    
        print(f"  ⚙️  [TuGraph Exec] 成功执行 Cypher，返回 {len(rows)} 行数据")
        return {"rows": rows, "raw_count": len(rows), "error": ""}
    except Exception as e:
        err_msg = str(e)
        print(f"  ❌ [TuGraph Exec Error] {err_msg}")
        return {"rows": [], "raw_count": 0, "error": err_msg}
    finally:
        driver.close()

# ---------- Node 4: 业务化回复 ----------
def node_answer(state: FraudState) -> dict:
    """利用 MiniMax 将图库数据融合生成中文研判报告"""
    question = state["question"]
    rows = state["rows"]
    cypher = state["cypher"]
    error = state.get("error", "")
    
    if error:
        report = f"❌ 系统在图谱执行阶段遇到错误:\n`{error}`\n\n请联系技术人员检查 Cypher 语法与 Schema 是否一致。"
        return {"final_report": report, "confidence": 0}
        
    try:
        client, model = get_minimax_client()
        
        prompt = f"""
        你是一位资深金融反欺诈审查官。请根据以下【图数据库查询到的数据】，撰写一份详细的中文【反欺诈风险研判报告】。
        
        【用户提问】: {question}
        【查询 Cypher】: {cypher}
        【查询结果数据】:
        {json.dumps(rows, ensure_ascii=False, indent=2)}
        
        【撰写要求】:
        1. 针对用户的提问，进行直接、客观的中文业务解答。
        2. 如果结果为空 ( [] )，说明“未发现相关异常关联，当前风控评级为安全”。
        3. 如果结果中含有共享设备 (USED_DEVICE) 或共享手机号 (WITH_PHONE) 的多个人员关联，必须判定为“高欺诈团伙风险”，清晰列出受波及的申请人姓名、ID 及共享媒介。
        4. 分析潜在的风控逻辑（如：共享手机号可能代表中介批量操作或身份冒用）。
        5. 给出具体的业务处置建议（如：通过、拒绝、挂起人工核查）。
        
        请直接输出风控研判报告:"""
        
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )
        
        report = response.choices[0].message.content.strip()
        
        # 清洗推理模型可能泄漏到最终报告中的 <think>...</think> 思维链片段
        # （同 shareholding_agent_tugraph.py:357 保持一致；reasoning 模型的 chain-of-thought
        #  会偶尔突破"只输出 JSON/Cypher"约束，泄漏到自然语言报告头部。）
        report = re.sub(r"<think>.*?</think>", "", report, flags=re.DOTALL).strip()
        if report.startswith("</think>"):
            report = report[len("</think>"):].lstrip()
        
        # 评估置信度
        confidence = state.get("confidence", 90)
        if len(rows) == 0 and "shared" in state["intent"]:
            confidence = 95
            
        return {"final_report": report, "confidence": confidence}
        
    except Exception as e:
        print(f"  ❌ [MiniMax Answer Error] {e}。退回到规则生成回复。")
        # 降级备用纯文本生成报告
        if not rows:
            report = f"🔍 针对问题 [{question}]，图库中未查询到任何记录，当前状态正常。"
        else:
            report = f"🔍 针对问题 [{question}]，查询到如下 {len(rows)} 条图数据记录:\n" + json.dumps(rows, ensure_ascii=False, indent=2)
        return {"final_report": report, "confidence": 60}

# ---------- 构建 LangGraph 状态机 ----------
def build_agent():
    """构建 LangGraph 状态机: START -> classify -> cypher_gen -> cypher_exec -> answer -> END"""
    g = StateGraph(FraudState)
    g.add_node("classify", node_intent_classify)
    g.add_node("cypher_gen", node_cypher_gen)
    g.add_node("cypher_exec", node_cypher_exec)
    g.add_node("answer", node_answer)
    
    g.add_edge(START, "classify")
    g.add_edge("classify", "cypher_gen")
    g.add_edge("cypher_gen", "cypher_exec")
    g.add_edge("cypher_exec", "answer")
    g.add_edge("answer", END)
    
    return g.compile()
