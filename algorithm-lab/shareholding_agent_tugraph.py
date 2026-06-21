"""
shareholding_agent_tugraph.py — Algorithm Lab 股权穿透/最终受益人(UBO)大模型直连研判智能体 (TuGraph 版本)
=============================================================================================

通过 MiniMax API + LangGraph + TuGraph 直连：
  1. 意图分类与参数提取 (利用 MiniMax 提取，支持提取企业名称/ID)
  2. Cypher 生成 (预定义模板 + LLM 动态生成混合模式)
  3. TuGraph 7687 Bolt 直连执行 (使用 neo4j 驱动)
  4. 业务化研判报告 (利用 MiniMax 结合多跳股权路径进行 UBO 穿透分析)
"""
from __future__ import annotations
import os
import json
import re
from typing import TypedDict, Any
from neo4j import GraphDatabase
from openai import OpenAI
from langgraph.graph import END, START, StateGraph

# ---------- TuGraph 连接配置 ----------
TUGRAPH_URI = "bolt://localhost:7687"
TUGRAPH_USER = "admin"
TUGRAPH_PASSWORD = "73@TuGraph"
TUGRAPH_DB = "default"

# ---------- MiniMax 客户端初始化 ----------
def get_minimax_client() -> tuple[OpenAI, str]:
    api_key = os.environ.get("MINIMAX_API_KEY")
    if not api_key:
        raise ValueError(
            "请设置 MINIMAX_API_KEY 环境变量!\n"
            "例如: export MINIMAX_API_KEY='您的MiniMax API Key'"
        )
    base_url = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1")
    model = os.environ.get("MINIMAX_MODEL", "MiniMax-M3")
    client = OpenAI(base_url=base_url, api_key=api_key)
    return client, model

# ---------- State 定义 ----------
class LabState(TypedDict):
    question: str
    intent: str
    extracted_params: dict
    cypher: str
    rows: list
    raw_count: int
    final_report: str
    confidence: int
    error: str

# ---------- 领域本体定义 (Ontology) ----------
DOMAIN_ONTOLOGY = {
    "entities": {
        "Person": {
            "description": "自然人股东",
            "properties": {
                "person_id": "STRING, 唯一ID (如 p_1031)",
                "name": "STRING, 姓名"
            }
        },
        "Corp": {
            "description": "企业公司",
            "properties": {
                "corp_id": "STRING, 唯一ID (如 c_102)",
                "name": "STRING, 公司名称"
            }
        }
    },
    "relations": [
        {"source": "Person/Corp", "edge": "hold_share", "target": "Corp", "description": "持股关系，带属性 share (持股比例)"}
    ]
}

# ---------- 意图模板 ----------
INTENT_TEMPLATES = {
    "list_corps": {
        "description": "列出系统中的公司列表",
        "template": "MATCH (c:Corp) RETURN c.corp_id AS corp_id, c.name AS name LIMIT 10",
    },
    "find_ubo": {
        "description": "寻找特定公司(支持按公司名 corp_name 或ID corp_id)的最终受益人/穿透股权链",
        "template": (
            "MATCH (s)-[r:hold_share]->(t)-[:hold_share*0..4]->(target:Corp) "
            "WHERE target.name = $corp_name OR target.corp_id = $corp_id "
            "RETURN DISTINCT coalesce(s.person_id, s.corp_id) AS src_id, s.name AS src_name, label(s) AS src_type, "
            "                coalesce(t.person_id, t.corp_id) AS dst_id, t.name AS dst_name, label(t) AS dst_type, "
            "                r.share AS share"
        ),
        "params": ["corp_name", "corp_id"]
    }
}

# ---------- Node 1: 意图分类与参数提取 ----------
def node_intent_classify(state: LabState) -> dict:
    """利用 MiniMax 进行股权场景意图分类与参数提取"""
    question = state["question"]
    try:
        client, model = get_minimax_client()
        prompt = f"""
        你是一个精通企业股权架构领域的本体分类器。请阅读以下【本体结构】，分析用户问题的【查询意图】和【需要提取的参数】。
        
        【本体结构】:
        {json.dumps(DOMAIN_ONTOLOGY, ensure_ascii=False, indent=2)}
        
        【任务要求】:
        意图分类值必须是以下之一:
        - "list_corps" (列出所有公司列表)
        - "find_ubo" (进行股权穿透、查找最终受益人/UBO，需要参数 corp_name 或 corp_id)
        - "unknown" (无法匹配)
        
        提取参数时：
        - 若用户指定了公司名，提取公司名（如 "Hicks PLC"），存入参数 key "corp_name"
        - 若用户指定了公司ID，提取ID（如 "c_102"），存入参数 key "corp_id"
        
        请严格以 JSON 格式输出，不要任何 Markdown 包装（不要 ```json），确保输出可以直接被 json.loads 解析。
        示例:
        {{"intent": "find_ubo", "params": {{"corp_name": "Hicks PLC"}}, "confidence": 95}}
        
        用户提问: "{question}"
        JSON输出:"""
        
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )
        
        content = response.choices[0].message.content.strip()
        # 清洗推理模型 <think> 标记及 Markdown 代码块
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        content = re.sub(r"^```json\s*", "", content, flags=re.IGNORECASE)
        content = re.sub(r"\s*```$", "", content, flags=re.IGNORECASE)
        
        data = json.loads(content)
        print(f"  🧠 [MiniMax Lab Intent] intent='{data.get('intent')}'  params={data.get('params')}")
        return {
            "intent": data.get("intent", "unknown"),
            "extracted_params": data.get("params", {}),
            "confidence": data.get("confidence", 70)
        }
    except Exception as e:
        print(f"  ❌ [MiniMax Classify Error] {e}，降级回退到规则匹配。")
        # 降级备用规则匹配
        intent = "list_corps"
        params = {}
        # 简单规则匹配
        if "穿透" in question or "控制" in question or "股东" in question or "受益人" in question or "ubo" in question.lower() or "hicks" in question.lower():
            intent = "find_ubo"
            # 尝试查找 Hicks PLC 这种特定公司名
            for cname in ["Hicks PLC", "Ortega-Hoffman", "Hall-Wilson", "Moore-Kim"]:
                if cname.lower() in question.lower():
                    params["corp_name"] = cname
                    break
            # 尝试查找 c_102 这种 ID
            m = re.search(r"c_\d+", question)
            if m:
                params["corp_id"] = m.group(0)
                
        return {"intent": intent, "extracted_params": params, "confidence": 50}

# ---------- Node 2: Cypher 生成 ----------
def node_cypher_gen(state: LabState) -> dict:
    """生成用于 TuGraph 的 Cypher 语句"""
    intent = state["intent"]
    params = state.get("extracted_params", {})
    
    # 1. 如果匹配到了预定义模板，直接生成参数化/硬编码的 Cypher
    if intent in INTENT_TEMPLATES:
        tpl = INTENT_TEMPLATES[intent]
        cypher = tpl["template"]
        print(f"  📝 [CypherGen] 使用预置本体模板: {intent}")
        return {"cypher": cypher}
    
    # 2. 如果属于未知意图，让 MiniMax 动态生成 Cypher
    print("  📝 [CypherGen] 未知意图，调用 MiniMax 动态生成 Cypher...")
    try:
        client, model = get_minimax_client()
        prompt = f"""
        你是一个精通 TuGraph Cypher 语法的图谱数据库专家。请根据【本体结构】，将用户的自然语言问题翻译成合法的 Cypher 查询。
        
        【本体结构】:
        {json.dumps(DOMAIN_ONTOLOGY, ensure_ascii=False, indent=2)}
        
        【限制条件】:
        - 仅输出一条 Cypher 语句，不要有任何 Markdown 块包裹（不要 ```cypher 或 ```）。
        - 只能使用本体中定义的 Label (Person, Corp) 和 Edge (hold_share)。
        
        用户提问: "{state["question"]}"
        Cypher查询:"""
        
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )
        
        cypher = response.choices[0].message.content.strip()
        cypher = re.sub(r"<think>.*?</think>", "", cypher, flags=re.DOTALL).strip()
        cypher = re.sub(r"^```(cypher|sql|ngql)?\s*", "", cypher, flags=re.IGNORECASE)
        cypher = re.sub(r"\s*```$", "", cypher, flags=re.IGNORECASE)
        
        print(f"  📝 [MiniMax Cypher] 动态生成: {cypher}")
        return {"cypher": cypher}
        
    except Exception as e:
        print(f"  ❌ [MiniMax Cypher Error] {e}。降级回退到全量列表查询。")
        return {"cypher": INTENT_TEMPLATES["list_corps"]["template"]}

# ---------- Helper: DFS Path Recovery from Edges ----------
def find_paths_to_target(edges: list[dict], target_id: str | None, target_name: str | None = None) -> list[dict]:
    """
    基于扁平关系边列表，在内存中执行 DFS 构建从 Person/Corp 到 target_id 的所有多跳股权路径。
    """
    # 1. 建立邻接表：dst_id -> [edge1, edge2, ...]
    adj = {}
    for edge in edges:
        dst = edge["dst_id"]
        if dst not in adj:
            adj[dst] = []
        adj[dst].append(edge)
        
    paths = []
    
    # 2. 递归 DFS 搜索
    def dfs(current_id, current_path):
        visited_ids = [n["id"] for n in current_path["nodes"]]
        incoming = adj.get(current_id, [])
        if not incoming:
            paths.append(current_path)
            return
            
        for edge in incoming:
            src_id = edge["src_id"]
            if src_id in visited_ids:
                # 环路检测，直接截断
                paths.append(current_path)
                continue
                
            src_node = {"id": src_id, "name": edge["src_name"], "type": edge["src_type"]}
            new_nodes = [src_node] + current_path["nodes"]
            new_shares = [edge["share"]] + current_path["shares"]
            dfs(src_id, {"nodes": new_nodes, "shares": new_shares})
            
    # 3. 定位 target 节点的信息
    resolved_target_id = target_id
    resolved_target_name = target_name
    
    # 如果只有 target_name 或 target_id，从边数据里反查补全
    for edge in edges:
        if target_id and edge["dst_id"] == target_id:
            resolved_target_name = edge["dst_name"]
            break
        if target_id and edge["src_id"] == target_id:
            resolved_target_name = edge["src_name"]
            break
        if target_name and edge["dst_name"] == target_name:
            resolved_target_id = edge["dst_id"]
            break
        if target_name and edge["src_name"] == target_name:
            resolved_target_id = edge["src_id"]
            break
            
    # 如果没有找到任何边，说明没有路径
    if not resolved_target_id:
        return []
        
    target_node = {"id": resolved_target_id, "name": resolved_target_name or resolved_target_id, "type": "Corp"}
    
    # 4. 执行 DFS
    dfs(resolved_target_id, {"nodes": [target_node], "shares": []})
    return paths

# ---------- Node 3: TuGraph 执行 ----------
def node_cypher_exec(state: LabState) -> dict:
    """直连 TuGraph 执行 Cypher 语句"""
    cypher = state["cypher"]
    params = state.get("extracted_params", {})
    intent = state["intent"]
    
    driver = GraphDatabase.driver(TUGRAPH_URI, auth=(TUGRAPH_USER, TUGRAPH_PASSWORD))
    try:
        with driver.session(database=TUGRAPH_DB) as session:
            # 确保模板参数在 params 中未定义时有默认值，避免 Cypher 执行时报 Parameter missing 错误
            full_params = {"corp_name": None, "corp_id": None}
            full_params.update(params)
            result = session.run(cypher, **full_params)
            raw_rows = [dict(record) for record in result]
            
            # 如果是 find_ubo 意图，执行后置 DFS 路径还原
            if intent == "find_ubo" and raw_rows:
                target_id = params.get("corp_id")
                target_name = params.get("corp_name")
                paths = find_paths_to_target(raw_rows, target_id, target_name)
                
                # 重新包装为 downstream 节点兼容的格式
                formatted_paths = []
                for p in paths:
                    formatted_paths.append({
                        "node_ids": [n["id"] for n in p["nodes"]],
                        "node_names": [n["name"] for n in p["nodes"]],
                        "shares": p["shares"]
                    })
                rows = formatted_paths
            else:
                rows = raw_rows
                
            print(f"  ⚙️  [TuGraph Exec] 成功执行 Cypher，返回 {len(rows)} 行数据")
            return {"rows": rows, "raw_count": len(rows), "error": ""}
    except Exception as e:
        err_msg = str(e)
        print(f"  ❌ [TuGraph Exec Error] {err_msg}")
        return {"rows": [], "raw_count": 0, "error": err_msg}
    finally:
        driver.close()

# ---------- Node 4: 业务化研判报告 ----------
def node_answer(state: LabState) -> dict:
    """利用 MiniMax 进行股权穿透研判报告的撰写"""
    question = state["question"]
    rows = state["rows"]
    cypher = state["cypher"]
    error = state.get("error", "")
    
    if error:
        report = f"❌ 数据库执行出错:\n`{error}`"
        return {"final_report": report, "confidence": 0}
        
    try:
        client, model = get_minimax_client()
        prompt = f"""
        你是一位资深商业合规和企业风控官。请根据以下【图数据库查询到的股权穿透路径数据】，撰写一份详细的【企业股权穿透与最终受益人(UBO)研判报告】。
        
        【用户提问】: {question}
        【查询 Cypher】: {cypher}
        【查询到的关系路径数据 (JSON)】:
        {json.dumps(rows, ensure_ascii=False, indent=2)}
        
        【报告撰写要求】:
        1. 针对用户的提问，进行客观、准确 of 中文解答。
        2. 如果数据为空 ( [] )，说明“未发现相关控股路径”。
        3. 对于每一条路径（Path）：
           - 路径数据形式为: node_ids (节点ID链条), node_names (姓名/公司名称链条), shares (每一级持股比例链条)。
           - 请详细还原控制链路。例如，如果链条为：[Jasmine Bell, Company A, Company B]，持股比例链条为：[10.0, 50.0]。这代表 Jasmine Bell 持有 Company A 10% 股份，Company A 持有 Company B 50% 股份。
           - 计算或描述**最终受益人 (UBO)** 的穿透控股比例。
        4. 分析是否存在特殊风险结构：例如“循环持股”、“代持嫌疑”、“大股东高度集中”、“控制链过长导致规避监管”等。
        5. 给出最终的风控合规建议（如：准入、强化尽调、关注控股稳定性）。
        
        请直接输出中文研判报告:"""
        
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )
        
        report = response.choices[0].message.content.strip()
        report = re.sub(r"<think>.*?</think>", "", report, flags=re.DOTALL).strip()
        
        confidence = state.get("confidence", 90)
        return {"final_report": report, "confidence": confidence}
    except Exception as e:
        print(f"  ❌ [MiniMax Lab Answer Error] {e}。退回到原始数据输出。")
        report = f"🔍 针对问题 [{question}]，查询到如下股权记录:\n" + json.dumps(rows, ensure_ascii=False, indent=2)
        return {"final_report": report, "confidence": 60}

# ---------- 构建 LangGraph 状态机 ----------
def build_agent():
    g = StateGraph(LabState)
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
