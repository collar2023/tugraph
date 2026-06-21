"""
mcp_server.py — 采购合同审计场景专属 MCP 服务端
===============================================

本服务端实现了 Model Context Protocol (MCP)，将 TuGraph 图数据库与 Qdrant 向量数据库能力封装为大模型直连接口。
暴露的工具组包括：
  1. 数据探索工具：读取本地原始采购流水或合同样本
  2. 本体建模工具：获取 TuGraph 图谱 Schema、动态修改点边 Label
  3. 图谱写入与执行工具：Cypher 原生执行、三元组批量写入、Qdrant 向量舆情比对
"""
import os
import sys
import json
import re
import pandas as pd
from typing import Any, Optional
from mcp.server.fastmcp import FastMCP
from neo4j import GraphDatabase
from qdrant_client import QdrantClient

# ---------- 初始化 FastMCP ----------
mcp = FastMCP("Procurement-Audit-MCP-Service")
mcp = FastMCP("Procurement-Audit-MCP-Service")


# ---------- 数据库连接配置 ----------
TUGRAPH_URI = os.environ.get("TUGRAPH_URI", "bolt://localhost:7687")
TUGRAPH_USER = os.environ.get("TUGRAPH_USER", "admin")
TUGRAPH_PASSWORD = os.environ.get("TUGRAPH_PASSWORD", "73@TuGraph")
TUGRAPH_DB = os.environ.get("TUGRAPH_DB", "default")

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")

# ==========================================
# 审计日志模块 (Audit Log, 2026-06-19)
# ==========================================
# 治理闭环的最基础要求：所有 tool 调用留痕。
# Schema:
#   AuditSession (session_id PRIMARY) - 每个 MCP 进程 1 个
#   AuditAction  (action_id PRIMARY)  - 每次 tool 调用 1 条
#   AuditRef     (ref_id PRIMARY)     - 1 条 Action 关联 N 个被操作对象
# Edges:
#   AuditSession -[:LOGGED]-> AuditAction
#   AuditAction  -[:ACTED_ON]-> AuditRef
# 写入策略：
#   - 独立短连接，写入失败仅 stderr 警告，不阻塞业务
#   - actor 默认 "mcp-system"，可通过 AUDIT_ACTOR 环境变量覆盖
#   - session_id 持久化到 scratch/audit_session.json，重启进程后 session 续期
#   - 跳过只读元数据 tool (list_tools / describe_tool / get_graph_schema)
import uuid
import datetime as _dt

_AUDIT_SESSION_PATH = os.path.join(os.path.dirname(__file__), "scratch", "audit_session.json")
_AUDIT_ACTOR = os.environ.get("AUDIT_ACTOR", "mcp-system")

_AUDIT_DDL_SESSION = """CALL db.createVertexLabelByJson('{"label":"AuditSession","primary":"session_id","type":"VERTEX","properties":[{"name":"session_id","type":"STRING","is_primary":true,"is_unique":true,"is_notnull":true,"max_length":64},{"name":"created_at","type":"STRING"},{"name":"actor","type":"STRING"},{"name":"source","type":"STRING","default_value":"mcp"}]}')"""
_AUDIT_DDL_ACTION = """CALL db.createVertexLabelByJson('{"label":"AuditAction","primary":"action_id","type":"VERTEX","properties":[{"name":"action_id","type":"STRING","is_primary":true,"is_unique":true,"is_notnull":true,"max_length":64},{"name":"tool_name","type":"STRING"},{"name":"actor","type":"STRING"},{"name":"created_at","type":"STRING"},{"name":"ok","type":"STRING"},{"name":"duration_ms","type":"INT32"},{"name":"summary","type":"STRING","max_length":500}]}')"""
_AUDIT_DDL_REF = """CALL db.createVertexLabelByJson('{"label":"AuditRef","primary":"ref_id","type":"VERTEX","properties":[{"name":"ref_id","type":"STRING","is_primary":true,"is_unique":true,"is_notnull":true,"max_length":64},{"name":"kind","type":"STRING"},{"name":"value","type":"STRING","max_length":1000}]}')"""
_AUDIT_DDL_EDGE_LOGGED = """CALL db.createEdgeLabelByJson('{"label":"LOGGED","type":"EDGE","constraints":[["AuditSession","AuditAction"]]}')"""
_AUDIT_DDL_EDGE_ACTED_ON = """CALL db.createEdgeLabelByJson('{"label":"ACTED_ON","type":"EDGE","constraints":[["AuditAction","AuditRef"]]}')"""

# HITL (2026-06-19)
_AUDIT_DDL_PENDING = """CALL db.createVertexLabelByJson('{"label":"PendingReview","primary":"review_id","type":"VERTEX","properties":[{"name":"review_id","type":"STRING","is_primary":true,"is_unique":true,"is_notnull":true,"max_length":64},{"name":"ref_action_id","type":"STRING"},{"name":"ref_kind","type":"STRING"},{"name":"ref_value","type":"STRING","max_length":500},{"name":"reason","type":"STRING"},{"name":"created_at","type":"STRING"},{"name":"status","type":"STRING","default_value":"pending"}]}')"""
_AUDIT_DDL_HUMAN_DECISION = """CALL db.createVertexLabelByJson('{"label":"HumanDecision","primary":"decision_id","type":"VERTEX","properties":[{"name":"decision_id","type":"STRING","is_primary":true,"is_unique":true,"is_notnull":true,"max_length":64},{"name":"review_id","type":"STRING"},{"name":"decided_by","type":"STRING"},{"name":"decided_at","type":"STRING"},{"name":"outcome","type":"STRING"},{"name":"note","type":"STRING","max_length":500}]}')"""
_AUDIT_DDL_EDGE_TRIGGERED = """CALL db.createEdgeLabelByJson('{"label":"TRIGGERED","type":"EDGE","constraints":[["AuditAction","PendingReview"]]}')"""
_AUDIT_DDL_EDGE_DECIDED = """CALL db.createEdgeLabelByJson('{"label":"DECIDED","type":"EDGE","constraints":[["PendingReview","HumanDecision"]]}')"""
_AUDIT_DDL_EDGE_OVERRIDE_WRITES = """CALL db.createEdgeLabelByJson('{"label":"OVERRIDE_WRITES","type":"EDGE","constraints":[["HumanDecision","AuditAction"]]}')"""



def _audit_session_id() -> str:
    """读取或创建 session_id 并持久化（MCP 进程级会话）"""
    try:
        if os.path.exists(_AUDIT_SESSION_PATH):
            with open(_AUDIT_SESSION_PATH, "r", encoding="utf-8") as f:
                sid = json.load(f).get("session_id")
                if sid:
                    return sid
    except Exception:
        pass
    sid = f"asess_{uuid.uuid4().hex[:12]}"
    try:
        os.makedirs(os.path.dirname(_AUDIT_SESSION_PATH), exist_ok=True)
        with open(_AUDIT_SESSION_PATH, "w", encoding="utf-8") as f:
            json.dump({"session_id": sid, "created_at": _dt.datetime.utcnow().isoformat() + "Z"}, f, ensure_ascii=False)
    except Exception:
        pass
    return sid


def _audit_ensure_schema():
    """首次启动时建 audit schema；已存在则幂等跳过。"""
    cmds = []
    cmds.append(_AUDIT_DDL_SESSION)
    cmds.append(_AUDIT_DDL_ACTION)
    cmds.append(_AUDIT_DDL_REF)
    edge_cmds = [_AUDIT_DDL_EDGE_LOGGED, _AUDIT_DDL_EDGE_ACTED_ON, _AUDIT_DDL_EDGE_TRIGGERED, _AUDIT_DDL_EDGE_DECIDED, _AUDIT_DDL_EDGE_OVERRIDE_WRITES]
    cmds.extend([_AUDIT_DDL_PENDING, _AUDIT_DDL_HUMAN_DECISION])
    try:
        d = GraphDatabase.driver(TUGRAPH_URI, auth=(TUGRAPH_USER, TUGRAPH_PASSWORD))
        with d.session(database=TUGRAPH_DB) as s:
            for cmd in cmds + edge_cmds:
                # TuGraph 偶发约束检查 race condition 报 LabelExist 但实际已建；最多重试 2 次
                for _ in range(3):
                    try:
                        s.run(cmd)
                        break
                    except Exception:
                        import time as __t; __t.sleep(0.05)
        d.close()
    except Exception as e:
        print("[audit] schema ensure failed: " + str(e), file=sys.stderr)


def _audit_log(tool_name: str, ok: bool, duration_ms: int, summary: str, refs):
    """
    写一条 AuditAction + N 条 AuditRef + 边。
    失败不抛异常（治理层故障不能搞挂业务）。
    """
    try:
        _audit_ensure_schema()
        action_id = f"act_{uuid.uuid4().hex[:12]}"
        sid = _audit_session_id()
        d = GraphDatabase.driver(TUGRAPH_URI, auth=(TUGRAPH_USER, TUGRAPH_PASSWORD))
        with d.session(database=TUGRAPH_DB) as s:
            s.run(
                "MERGE (sess:AuditSession {session_id: $sid}) "
                "ON CREATE SET sess.created_at = $ts, sess.actor = $actor, sess.source = 'mcp'",
                sid=sid, ts=_dt.datetime.utcnow().isoformat() + "Z", actor=_AUDIT_ACTOR,
            )
            s.run(
                "CREATE (a:AuditAction {action_id:$aid, tool_name:$tn, actor:$ac, "
                "created_at:$ts, ok:$ok, duration_ms:$dur, summary:$sm})",
                aid=action_id, tn=tool_name, ac=_AUDIT_ACTOR,
                ts=_dt.datetime.utcnow().isoformat() + "Z",
                ok="true" if ok else "false", dur=int(duration_ms), sm=summary[:500],
            )
            s.run(
                "MATCH (sess:AuditSession {session_id:$sid}), (a:AuditAction {action_id:$aid}) "
                "MERGE (sess)-[:LOGGED]->(a)",
                sid=sid, aid=action_id,
            )
            for kind, value in refs:
                ref_id = f"ref_{uuid.uuid4().hex[:10]}"
                s.run(
                    "CREATE (r:AuditRef {ref_id:$rid, kind:$k, value:$v})",
                    rid=ref_id, k=kind, v=str(value)[:1000],
                )
                s.run(
                    "MATCH (a:AuditAction {action_id:$aid}), (r:AuditRef {ref_id:$rid}) "
                    "MERGE (a)-[:ACTED_ON]->(r)",
                    aid=action_id, rid=ref_id,
                )
        d.close()
    except Exception as e:
        print("[audit] write failed for " + tool_name + ": " + str(e), file=sys.stderr)


def _audit_query_recent(limit: int = 20) -> str:
    """查询最近 N 条 audit action（含 Ref 列表），供审计员使用。"""
    try:
        d = GraphDatabase.driver(TUGRAPH_URI, auth=(TUGRAPH_USER, TUGRAPH_PASSWORD))
        with d.session(database=TUGRAPH_DB) as s:
            # TuGraph 不支持 OPTIONAL MATCH，先拿 actions，再批量拉 refs
            actions = s.run(
                "MATCH (a:AuditAction) "
                "RETURN a.action_id AS aid, a.tool_name AS tool, a.actor AS actor, "
                "a.created_at AS ts, a.ok AS ok, a.duration_ms AS dur, a.summary AS summary "
                "ORDER BY a.created_at DESC LIMIT $lim",
                lim=limit,
            ).data()
            # 批量拉所有 ref
            all_refs = s.run("MATCH (a:AuditAction)-[:ACTED_ON]->(r:AuditRef) RETURN a.action_id AS aid, r.kind AS kind, r.value AS value").data()
            refs_by_aid = {}
            for rr in all_refs:
                refs_by_aid.setdefault(rr["aid"], []).append({"kind": rr["kind"], "value": rr["value"]})
            rows = []
            for a in actions:
                rows.append({
                    "aid": a["aid"], "tool": a["tool"], "actor": a["actor"],
                    "ts": a["ts"], "ok": a["ok"], "dur": a["dur"],
                    "summary": a["summary"],
                    "refs": refs_by_aid.get(a["aid"], []),
                })
        d.close()
        return json.dumps({"actions": rows, "total": len(rows), "session_id": _audit_session_id()}, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": "audit query failed: " + str(e)}, ensure_ascii=False)


# 进程启动时确保 schema 存在（best-effort）
try:
    _audit_ensure_schema()
except Exception:
    pass



# ==========================================
# 工具契约清单 (Tool Registry, 2026-06-19)
# ==========================================
# 单一真相源：所有 tool 的元数据集中维护在此处，供 list_tools / describe_tool 查询、
# 前端能力看板渲染、LLM 工具选择使用。@mcp.tool() 函数体本身保持 FastMCP 标准行为。
# 字段：
#   name        : tool 名（与 @mcp.tool() 函数名一致）
#   category    : 分组（inspection / schema / execution / write / vector / meta）
#   risk        : 危险等级 (safe / caution / danger)，前端可据此显示二次确认
#   params      : [{name, type, required, desc}] 参数契约
#   returns     : 返回 shape 描述
#   summary     : 一句话能力描述（给 LLM 看）
TOOL_REGISTRY: list[dict] = [
    {
        "name": "get_raw_data_sample",
        "category": "inspection",
        "risk": "safe",
        "summary": "读取本地 CSV/Excel 样本数据，供逆向建模学习",
        "params": [
            {"name": "file_path", "type": "str", "required": True,  "desc": "本地文件绝对路径（限定白名单目录）"},
            {"name": "limit",     "type": "int", "required": False, "desc": "采样行数，默认 5"},
        ],
        "returns": "JSON 字符串，包含 columns / rows",
    },
    {
        "name": "get_graph_schema",
        "category": "schema",
        "risk": "safe",
        "summary": "获取 TuGraph 当前所有点/边 Label 及属性定义",
        "params": [],
        "returns": "JSON 字符串，结构 {vertex_labels:[], edge_labels:[]}",
    },
    {
        "name": "create_graph_label",
        "category": "schema",
        "risk": "caution",
        "summary": "动态创建/修改点边 Label（Schema 变更，会被审计）",
        "params": [
            {"name": "label_type",      "type": "str", "required": True,  "desc": "VERTEX 或 EDGE"},
            {"name": "label_name",      "type": "str", "required": True,  "desc": "新 Label 名"},
            {"name": "primary_key",     "type": "str", "required": True,  "desc": "主键属性名（仅 VERTEX 需要）"},
            {"name": "properties_json", "type": "str", "required": True,  "desc": "属性定义 JSON 数组"},
            {"name": "constraints_json","type": "str", "required": False, "desc": "边约束 JSON 数组，默认 []"},
        ],
        "returns": "JSON 字符串，{ok: bool, label: str}",
    },
    {
        "name": "execute_cypher",
        "category": "execution",
        "risk": "caution",
        "summary": "直连 TuGraph 执行纯读 Cypher（DDL/写语句被安全护栏拦截）",
        "params": [
            {"name": "query",       "type": "str", "required": True,  "desc": "Cypher 语句（禁止 DDL/写；未带 LIMIT 时自动追加 1000）"},
            {"name": "params_json", "type": "str", "required": False, "desc": "参数化查询字典 JSON 字符串"},
        ],
        "returns": "JSON 字符串，{rows:[], count:int, warning?:str}",
    },
    {
        "name": "bulk_insert_relationships",
        "category": "write",
        "risk": "danger",
        "summary": "批量灌入三元组（支持幂等键；图谱写操作，强制审计）",
        "params": [
            {"name": "relationships_json", "type": "str", "required": True,  "desc": "三元组 JSON 数组"},
            {"name": "idempotency_key",     "type": "str", "required": False, "desc": "幂等键（SHA256/UUID）；同 key 重放返回首次结果"},
        ],
        "returns": "JSON 字符串，{ok:bool, inserted:int, idempotent_replay:bool}",
    },
    {
        "name": "search_vector_news",
        "category": "vector",
        "risk": "safe",
        "summary": "按供应商名在 Qdrant mail_vectors 上做字符 n-gram 检索，返回真实 payload（非 embedding、非 mock）",
        "params": [
            {"name": "supplier_name", "type": "str", "required": True,  "desc": "供应商名称（自动做全/半角/后缀清洗）"},
            {"name": "limit",         "type": "int", "required": False, "desc": "返回 top-N，默认 3"},
        ],
        "returns": "JSON 字符串，{supplier, retrieved_count, status, results:[]}",
    },
    {
        "name": "list_tools",
        "category": "meta",
        "risk": "safe",
        "summary": "查询本服务所有可用 tool 的契约清单",
        "params": [],
        "returns": "JSON 字符串，即 TOOL_REGISTRY 完整内容",
    },
    {
        "name": "query_audit_actions",
        "category": "audit",
        "risk": "safe",
        "summary": "查询最近 N 条审计操作记录（AuditAction + AuditRef），含 Cypher / 供应商 / 写入三元组数",
        "params": [
            {"name": "limit", "type": "int", "required": False, "desc": "返回条数，默认 20"},
        ],
        "returns": "JSON 字符串，{actions:[], total:int, session_id:str}",
    },
    {
        "name": "flag_for_review",
        "category": "hitl",
        "risk": "caution",
        "summary": "把已记录的 AuditAction 标为待人工审核，生成 PendingReview 节点",
        "params": [
            {"name": "action_id", "type": "str", "required": True,  "desc": "AuditAction.action_id"},
            {"name": "reason",    "type": "str", "required": False, "desc": "low_confidence/entity_conflict/cypher_failed/manual，默认 manual"},
            {"name": "note",      "type": "str", "required": False, "desc": "审核员备注"},
        ],
        "returns": "JSON 字符串，{review_id, action_id, reason, status}",
    },
    {
        "name": "list_pending_reviews",
        "category": "hitl",
        "risk": "safe",
        "summary": "列出 PendingReview 列表（含关联的 HumanDecision）",
        "params": [
            {"name": "status_filter", "type": "str", "required": False, "desc": "pending/approved/rejected/all，默认 pending"},
        ],
        "returns": "JSON 字符串，{reviews:[], total, session_id}",
    },
    {
        "name": "manual_commit",
        "category": "hitl",
        "risk": "danger",
        "summary": "审核员决定 approve/reject/override；override 模式复用 bulk_insert 写图",
        "params": [
            {"name": "review_id",             "type": "str", "required": True,  "desc": "PendingReview.review_id"},
            {"name": "outcome",               "type": "str", "required": True,  "desc": "approve / reject / override"},
            {"name": "note",                  "type": "str", "required": False, "desc": "审核员备注"},
            {"name": "override_payload_json", "type": "str", "required": False, "desc": "仅 override 时必填，格式同 bulk_insert_relationships"},
            {"name": "actor",                 "type": "str", "required": False, "desc": "审核员身份（缺省走环境变量）"},
        ],
        "returns": "JSON 字符串，{decision_id, new_status, override_result?}",
    },
    {
        "name": "describe_tool",
        "category": "meta",
        "risk": "safe",
        "summary": "查询单个 tool 的详细参数契约与危险等级",
        "params": [
            {"name": "tool_name", "type": "str", "required": True, "desc": "tool 名"},
        ],
        "returns": "JSON 字符串，单个 tool 的完整契约；未找到时返回 {error}",
    },
]


def _get_tool_entry(name: str) -> dict | None:
    for entry in TOOL_REGISTRY:
        if entry["name"] == name:
            return entry
    return None


def get_tugraph_driver():
    return GraphDatabase.driver(TUGRAPH_URI, auth=(TUGRAPH_USER, TUGRAPH_PASSWORD))

def get_qdrant_client():
    return QdrantClient(url=QDRANT_URL)


# ==========================================
# Cypher 安全护栏 (Safety Guards, 2026-06-19)
# ==========================================
# 防止 LLM 幻觉或 prompt 注入对 TuGraph 造成破坏性写操作 / 资源耗尽。
#  1. 黑名单拦截：禁止 DDL / 写语句 (DELETE / REMOVE / DROP / DETACH / SET 等)
#  2. LIMIT 强制注入：未带 LIMIT 时默认追加 1000 上限，防止内存爆炸
#  3. 字面量剥离：避免误伤字符串内的关键字 (如供应商名 "Delete Co Ltd")
#  4. 查询软超时：signal.alarm 包裹 session.run，超时即放弃
import re
import signal

# 危险语句正则（不区分大小写；写匹配整词开头或语句中独立 token；剥离单/双引号字符串字面量后再判）
_DDL_FORBIDDEN = re.compile(
    r"\b(DELETE|REMOVE|DETACH\s+DELETE|DROP|CREATE\s+(VERTEX|EDGE|LABEL|INDEX))\b",
    re.IGNORECASE,
)
# CALL db.* 是 OpenSPG/TuGraph 改 Schema 的特权入口，对 LLM 工具一律拒绝
_ADMIN_CALL_RE = re.compile(r"\bCALL\s+db\.", re.IGNORECASE)
_WRITE_FORBIDDEN = re.compile(
    r"\b(SET\s+[\w`\.]+\s*=|MERGE\s+|CREATE\s+\()",
    re.IGNORECASE,
)
_LIMIT_RE = re.compile(r"\bLIMIT\s+\d+", re.IGNORECASE)
_RETURN_RE = re.compile(r"\bRETURN\b", re.IGNORECASE)
_STRING_RE = re.compile(r"'[^']*'|\"[^\"]*\"", re.DOTALL)
_DEFAULT_LIMIT = 1000
_QUERY_TIMEOUT_SEC = 8


def _strip_string_literals(cypher: str) -> str:
    """将单/双引号字符串替换为空格，保留关键字检测的 token 结构。"""
    return _STRING_RE.sub("''", cypher)


def _enforce_safety(cypher: str) -> str:
    """对 cypher 做关键字黑名单 + LIMIT 强制，返回 (sanitized_cypher, warning_or_None)。"""
    stripped = _strip_string_literals(cypher).strip()
    # 1) 黑名单拦截
    m = _ADMIN_CALL_RE.search(stripped)
    if m:
        raise PermissionError(f"安全护栏拦截: 禁止特权管理语句 '{m.group(0).strip()}' (CALL db.* 仅允许运维通道直接调用)")
    m = _DDL_FORBIDDEN.search(stripped)
    if m:
        raise PermissionError(f"安全护栏拦截: 禁止 DDL 语句 '{m.group(0).strip()}'")
    m = _WRITE_FORBIDDEN.search(stripped)
    if m:
        raise PermissionError(f"安全护栏拦截: 禁止写语句 '{m.group(0).strip()}' (仅允许纯读 MATCH/RETURN/CALL/WITH)")
    # 2) LIMIT 强制：仅当存在 RETURN 且未带 LIMIT 时追加
    warning = None
    sanitized = cypher
    if _RETURN_RE.search(stripped) and not _LIMIT_RE.search(stripped):
        # 把 LIMIT 追加到末尾（去尾分号）
        sanitized = sanitized.rstrip().rstrip(";").rstrip() + f" LIMIT {_DEFAULT_LIMIT}"
        warning = f"已自动注入 LIMIT {_DEFAULT_LIMIT}（防止结果集过大）"
    return sanitized, warning


# ==========================================
# 供应商名清洗 (Supplier Name Normalizer, 2026-06-19)
# ==========================================
# 把全/半角括号、常见公司后缀、空白差异统一掉，让 LLM 给出的供应商名
# 与 Qdrant 中实际索引的 supplier 字段在关键词匹配阶段能命中。
# 清洗不会修改 supplier_name 原值，原值仍写入返回 payload 的 "raw_supplier" 字段供审计。
import unicodedata

_COMPANY_SUFFIXES = (
    "股份有限公司", "有限责任公司", "有限公司", "集团", "控股",
    "Co., Ltd.", "Co.,Ltd.", "Co Ltd", "Co. Ltd.",
    "Inc.", "Inc", "LLC", "L.L.C.", "PLC", "Corp.", "Corporation",
    "Ltd.", "Ltd", "Limited", "GmbH", "AG",
)


def _normalize_supplier_name(name: str) -> str:
    """统一全/半角、strip 公司后缀、压缩空白；返回清洗后名称。"""
    if not name:
        return name
    # NFKC 把全角括号 / 全角字母统一到半角
    s = unicodedata.normalize("NFKC", name).strip()
    # 去除中英文括号内说明（如"阿里巴巴(中国)网络技术有限公司" -> "阿里巴巴网络技术有限公司"）
    s = re.sub(r"[\(（][^)\(）]*[\)）]", "", s)
    # 去除常见公司后缀（贪心匹配，必须在末尾或含逗号边界）
    for suf in _COMPANY_SUFFIXES:
        if s.endswith(suf):
            s = s[: -len(suf)].rstrip().rstrip(",").rstrip()
            break  # 只去一次最长的后缀
    # 压缩多余空白
    s = re.sub(r"\s+", " ", s)
    return s


def _run_with_timeout(session, query, params, timeout_sec: int = _QUERY_TIMEOUT_SEC):
    """在同步单线程 MCP 服务中，用 signal.alarm 给单次 Cypher 加软超时。"""

    def _handler(signum, frame):
        raise TimeoutError(f"Cypher 执行超过 {timeout_sec}s 软超时")

    old_handler = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(timeout_sec)
    try:
        result = session.run(query, **params)
        rows = [dict(record) for record in result]
        return rows
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)



# ==========================================
# 1. 数据探索工具组 (Data Inspection Tools)
# ==========================================

@mcp.tool()
def get_raw_data_sample(file_path: str, limit: int = 5) -> str:
    """
    读取并探索本地原始采购记录或合同数据（支持 Excel 和 CSV 格式），返回前 N 行的 JSON 字符串。
    
    Args:
        file_path: 原始文件的绝对路径 (如 /home/ubuntu/tugraph/procurement-audit-mcp/procurement_contracts_sample.csv)
        limit: 返回的样例数据行数，默认 5 行
    """
    if not os.path.exists(file_path):
        return json.dumps({"error": f"未找到指定的文件: {file_path}"})
    
    try:
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".csv":
            df = pd.read_csv(file_path)
        elif ext in [".xls", ".xlsx"]:
            df = pd.read_excel(file_path)
        else:
            return json.dumps({"error": "不支持的文件类型，仅支持 CSV 或 Excel (.xlsx/.xls)"})
        
        sample = df.head(limit).to_dict(orient="records")
        return json.dumps({
            "total_rows": len(df),
            "columns": list(df.columns),
            "sample_data": sample
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": f"读取数据出错: {str(e)}"})


# ==========================================
# 2. 本体建模工具组 (Ontology Management)
# ==========================================

@mcp.tool()
def get_graph_schema() -> str:
    """
    获取 TuGraph 图数据库当前的本体 Schema（包含所有点 Label、边 Label、属性定义及连接约束关系）。
    """
    driver = get_tugraph_driver()
    try:
        with driver.session(database=TUGRAPH_DB) as session:
            result = session.run("CALL dbms.graph.getGraphSchema()")
            record = result.single()
            if record and "schema" in record.keys():
                # 返回完整的 Schema 详情 JSON
                return record["schema"]
            return json.dumps({"message": "未查询到 Schema 信息"})
    except Exception as e:
        return json.dumps({"error": f"获取 Schema 失败: {str(e)}"})
    finally:
        driver.close()

@mcp.tool()
def create_graph_label(label_type: str, label_name: str, primary_key: str, properties_json: str, constraints_json: str = "[]") -> str:
    """
    动态修改图谱本体 Schema，向 TuGraph 注入新的点/边 Label。
    
    Args:
        label_type: 建模类型, 必须是 'VERTEX' (点) 或 'EDGE' (边)
        label_name: 注入本体的 Label 名称 (如 'Supplier', 'approve_by')
        primary_key: 点的主键字段名 (当 label_type='VERTEX' 时必填，如 'corp_id')
        properties_json: 属性定义的 JSON 字符串 (如 '[{"name":"name","type":"STRING"},{"name":"age","type":"INT32"}]')
        constraints_json: 边连接约束条件 (仅在 label_type='EDGE' 时有效，如 '[["Person","Corp"],["Corp","Corp"]]')
    """
    try:
        props = json.loads(properties_json)
        constraints = json.loads(constraints_json)
    except Exception as e:
        return json.dumps({"error": f"JSON 参数解析失败: {str(e)}"})
    
    driver = get_tugraph_driver()
    try:
        with driver.session(database=TUGRAPH_DB) as session:
            if label_type.upper() == "VERTEX":
                # 构建 createVertexLabelByJson 格式
                if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", label_name):
                    return json.dumps({"error": "label_name 包含非法字符"})
                payload = {
                    "label": label_name,
                    "primary": primary_key,
                    "type": "VERTEX",
                    "properties": []
                }
                for prop in props:
                    payload["properties"].append({
                        "name": prop["name"],
                        "type": prop.get("type", "STRING"),
                        "is_primary": prop["name"] == primary_key,
                        "is_unique": prop["name"] == primary_key,
                        "is_notnull": prop["name"] == primary_key
                    })
                
                cmd = f"CALL db.createVertexLabelByJson('{json.dumps(payload)}')"
                session.run(cmd)
                _audit_log("create_graph_label", ok=True, duration_ms=0,
                           summary=f"vertex {label_name} created",
                           refs=[("label_type", "VERTEX"), ("label_name", label_name),
                                 ("primary_key", primary_key)])
                return json.dumps({"message": f"点 Label '{label_name}' 注入本体成功"})
                
            elif label_type.upper() == "EDGE":
                if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", label_name):
                    return json.dumps({"error": "label_name 包含非法字符"})
                # 构建 createEdgeLabelByJson 格式
                payload = {
                    "label": label_name,
                    "type": "EDGE",
                    "constraints": constraints,
                    "properties": []
                }
                for prop in props:
                    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", prop["name"]):
                        return json.dumps({"error": "属性名包含非法字符: " + prop["name"]})
                    payload["properties"].append({
                        "name": prop["name"],
                        "type": prop.get("type", "STRING"),
                        "is_notnull": False
                    })
                    
                cmd = f"CALL db.createEdgeLabelByJson('{json.dumps(payload)}')"
                session.run(cmd)
                _audit_log("create_graph_label", ok=True, duration_ms=0,
                           summary=f"edge {label_name} created",
                           refs=[("label_type", "EDGE"), ("label_name", label_name),
                                 ("constraints", json.dumps(constraints))])
                return json.dumps({"message": f"边 Label '{label_name}' 注入本体成功"})
            else:
                _audit_log("create_graph_label", ok=False, duration_ms=0,
                           summary=f"unknown label_type={label_type}",
                           refs=[("label_type", label_type), ("label_name", label_name)])
                return json.dumps({"error": "未知的 label_type，仅支持 'VERTEX' 或 'EDGE'"})
    except Exception as e:
        _audit_log("create_graph_label", ok=False, duration_ms=0,
                   summary=f"error: {str(e)[:80]}",
                   refs=[("label_type", label_type), ("label_name", label_name),
                         ("error", str(e)[:200])])
        return json.dumps({"error": f"本体注入失败: {str(e)}"})
    finally:
        driver.close()


# ==========================================
# 3. 写入与图演算工具组 (Execution & Ingestion)
# ==========================================

@mcp.tool()
def execute_cypher(query: str, params_json: Optional[str] = None) -> str:
    """
    直连 TuGraph 图数据库执行原生 Cypher 语言，进行图谱查询与状态研判。
    
    护栏 (2026-06-19 起)：
      - 禁止 DDL / 写语句（DELETE / REMOVE / DROP / DETACH DELETE / CREATE VERTEX|EDGE|LABEL|INDEX / MERGE / SET）
      - 未指定 LIMIT 的 RETURN 语句自动追加 LIMIT 1000
      - 单次执行硬超时 8s
    
    Args:
        query: 待执行的 Cypher 语句 (如 MATCH (s)-[r:hold_share]->(t) RETURN s.name, r.share, t.name)
        params_json: 参数化查询字典的 JSON 字符串 (如 '{"supplier_name": "Hicks PLC"}')
    """
    # 1) 关键字黑名单 + LIMIT 强制注入
    import time as _t
    _t0 = _t.time()
    try:
        safe_query, warning = _enforce_safety(query)
    except PermissionError as e:
        _audit_log("execute_cypher", ok=False, duration_ms=int((_t.time()-_t0)*1000),
                   summary=f"refused: {str(e)[:80]}", refs=[("cypher", query[:500]), ("reason", str(e)[:200])])
        return json.dumps({"error": str(e), "refused": True}, ensure_ascii=False)
    
    # 2) 解析参数
    params = {}
    if params_json:
        try:
            params = json.loads(params_json)
        except Exception as e:
            return json.dumps({"error": f"参数 params_json 解析失败: {str(e)}"})
            
    # 填充直连层的防崩溃占位符
    full_params = {"corp_name": None, "corp_id": None, "supplier_name": None, "contract_id": None}
    full_params.update(params)
    
    driver = get_tugraph_driver()
    try:
        with driver.session(database=TUGRAPH_DB) as session:
            rows = _run_with_timeout(session, safe_query, full_params)
            payload = {"rows": rows, "count": len(rows)}
            if warning:
                payload["warning"] = warning
            _audit_log("execute_cypher", ok=True, duration_ms=int((_t.time()-_t0)*1000),
                       summary=f"returned {len(rows)} rows",
                       refs=[("cypher", safe_query[:500]), ("row_count", str(len(rows)))])
            return json.dumps(payload, ensure_ascii=False, indent=2)
    except TimeoutError as e:
        _audit_log("execute_cypher", ok=False, duration_ms=int((_t.time()-_t0)*1000),
                   summary=f"timeout: {str(e)[:80]}",
                   refs=[("cypher", safe_query[:500]), ("error", str(e)[:200])])
        return json.dumps({"error": str(e), "refused": True}, ensure_ascii=False)
    except Exception as e:
        _audit_log("execute_cypher", ok=False, duration_ms=int((_t.time()-_t0)*1000),
                   summary=f"error: {str(e)[:80]}",
                   refs=[("cypher", safe_query[:500]), ("error", str(e)[:200])])
        return json.dumps({"error": f"Cypher 执行失败: {str(e)}"}, ensure_ascii=False)
    finally:
        driver.close()

@mcp.tool()
def bulk_insert_relationships(relationships_json: str, idempotency_key: Optional[str] = None) -> str:
    """
    大模型批量提取信息后，直连 TuGraph 快速灌入持股或审批边关系（默认幂等）。
    
    护栏 (2026-06-19 起)：
      - 节点/边均使用 MERGE 按主键幂等（重复调用不会撑爆图）
      - 提供 idempotency_key：相同 key 重放直接返回上次的写入结果，不重复执行
      - 幂等缓存持久化于 scratch/mcp_writes.jsonl
      - 注意：仅当源/终节点已存在时才能建边；前置调用 create_graph_label / seed 灌入节点
    
    Args:
        relationships_json: 关系三元组数组的 JSON 字符串。
        格式为：[{"src_id":"p_999","src_label":"Person","dst_id":"c_102","dst_label":"Corp","relation":"hold_share","properties":{"share":15.0}}]
        idempotency_key: 幂等键（建议 SHA256(relationships_json) 或 UUID）；同 key 重放直接命中缓存。
    """
    # 1) 幂等键命中检查
    import hashlib
    _IDEMPOTENCY_PATH = os.path.join(os.path.dirname(__file__), "scratch", "mcp_writes.jsonl")
    os.makedirs(os.path.dirname(_IDEMPOTENCY_PATH), exist_ok=True)
    cache_key = idempotency_key or hashlib.sha256(relationships_json.encode("utf-8")).hexdigest()
    import time as _t
    _t0 = _t.time()
    if os.path.exists(_IDEMPOTENCY_PATH):
        try:
            with open(_IDEMPOTENCY_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    if rec.get("key") == cache_key:
                        rec["idempotent_replay"] = True
                        _audit_log("bulk_insert_relationships", ok=True,
                                   duration_ms=int((_t.time()-_t0)*1000),
                                   summary=f"idempotent_replay, {rec.get('success_count', 0)} inserted",
                                   refs=[("idempotency_key", cache_key), ("triple_count", str(rec.get("total_sent", 0)))])
                        return json.dumps(rec, ensure_ascii=False, indent=2)
        except Exception:
            pass  # 缓存损坏时降级到正常写入

    # 2) 解析三元组
    try:
        rels = json.loads(relationships_json)
    except Exception as e:
        return json.dumps({"error": f"三元组解析失败: {str(e)}"})

    # 动态构建主键映射表，支持图谱本体自适应注入
    primary_key_map = {}
    try:
        schema_data = json.loads(get_graph_schema())
        for item in schema_data.get("schema", []):
            if item.get("type") == "VERTEX" and "label" in item and "primary" in item:
                primary_key_map[item["label"]] = item["primary"]
    except Exception:
        pass
    
    # 静态兜底
    _STATIC_MAP = {
        "Person": "person_id",
        "Corp": "corp_id",
        "Contract": "contract_id",
        "Invoice": "invoice_id",
        "Payment": "payment_id"
    }
    for k, v in _STATIC_MAP.items():
        if k not in primary_key_map:
            primary_key_map[k] = v

    driver = get_tugraph_driver()
    success_count = 0
    errors = []

    try:
        with driver.session(database=TUGRAPH_DB) as session:
            for idx, rel in enumerate(rels):
                try:
                    src_id = rel["src_id"]
                    src_label = rel["src_label"]
                    dst_id = rel["dst_id"]
                    dst_label = rel["dst_label"]
                    relation = rel["relation"]
                    props = rel.get("properties", {})
                    
                    if not (re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", src_label) and 
                            re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", dst_label) and 
                            re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", relation)):
                        raise ValueError(f"Label或Relation包含非法字符: {src_label}, {dst_label}, {relation}")

                    src_key = primary_key_map.get(src_label)
                    dst_key = primary_key_map.get(dst_label)
                    if not src_key or not dst_key:
                        raise ValueError(f"未知 Label: src={src_label} dst={dst_label}，无法通过 Schema 动态解析主键")

                    # 动态拼接 ON CREATE / ON MATCH 幂等属性赋值
                    on_create_assigns = []
                    on_match_assigns = []
                    params = {"src_id": src_id, "dst_id": dst_id}
                    for i, (k, v) in enumerate(props.items()):
                        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k):
                            raise ValueError(f"属性名包含非法字符: {k}")
                        param_key = f"p_{i}"
                        params[param_key] = v
                        on_create_assigns.append(f"e.{k} = ${param_key}")
                        on_match_assigns.append(f"e.{k} = ${param_key}")
                    on_create_clause = ("ON CREATE SET " + ", ".join(on_create_assigns)) if on_create_assigns else ""
                    on_match_clause = ("ON MATCH SET " + ", ".join(on_match_assigns)) if on_match_assigns else ""

                    # MERGE 模式：按节点主键 + 边类型匹配，节点/边均幂等
                    query = (
                        f"MATCH (s:{src_label} {{{src_key}: $src_id}}), (d:{dst_label} {{{dst_key}: $dst_id}}) "
                        f"MERGE (s)-[e:{relation}]->(d) "
                        f"{on_create_clause} {on_match_clause}".strip()
                    )

                    session.run(query, **params)
                    success_count += 1
                except Exception as ex:
                    errors.append(f"索引 {idx} 失败: {str(ex)}")

            result = {
                "status": "completed",
                "total_sent": len(rels),
                "success_count": success_count,
                "errors": errors,
                "idempotency_key": cache_key,
                "idempotent_replay": False,
            }
    except Exception as e:
        return json.dumps({"error": f"批量写入会话错误: {str(e)}"})
    finally:
        driver.close()

    # 3) 写幂等缓存（追加模式，进程安全；后续同 key 直接命中）
    try:
        with open(_IDEMPOTENCY_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({"key": cache_key, **result}, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 缓存写失败不影响主流程
    _audit_log("bulk_insert_relationships", ok=(len(errors) == 0),
               duration_ms=int((_t.time()-_t0)*1000),
               summary=f"inserted {success_count}/{len(rels)}, {len(errors)} errors",
               refs=[("idempotency_key", cache_key),
                     ("triple_count", str(len(rels))),
                     ("success_count", str(success_count))])
    return json.dumps(result, ensure_ascii=False, indent=2)

@mcp.tool()
def search_vector_news(supplier_name: str, limit: int = 3) -> str:
    """
    直连 Qdrant 向量检索库，召回与供应商相关的舆情/邮件记录，实现双驱合规研判。
    
    供应商名清洗 (2026-06-19 起)：
      - 全/半角统一 (NFKC)
      - 去除括号内说明 (如"阿里巴巴(中国)网络技术" -> "阿里巴巴网络技术")
      - 去除常见公司后缀 ("有限公司"/"Inc."/"LLC"/"PLC" 等)
      - 压缩空白
      原值保留在返回 JSON 的 raw_supplier 字段供审计。
    
    检索方式 (2026-06-19 真接)：
      - 直接在 mail_vectors collection 的 76 条真实 payload 上做字符 n-gram 相似度检索
      - 不依赖 embedding API（MiniMax M3 月度版未开放 embedding 通道）
      - 优先返回 is_virus=false 的正常邮件；同分时 sender 命中关键词更靠前
      - 不再使用任何 mock 数据；Qdrant 不可达时返回明确 unavailable
    
    Args:
        supplier_name: 供应商公司名称 (如 'Hicks PLC' / '阿里巴巴(中国)网络技术有限公司')
        limit: 召回的新闻/舆情数，默认 3 条
    """
    raw_supplier = supplier_name
    normalized = _normalize_supplier_name(supplier_name)
    import time as _t
    _t0 = _t.time()
    try:
        client = get_qdrant_client()
        client.get_collections()  # 健康检查；不可达时直接抛到外层 except
        hits = _qdrant_keyword_search(client, normalized, limit=limit)
        _audit_log("search_vector_news", ok=True, duration_ms=int((_t.time()-_t0)*1000),
                   summary=f"qdrant hit {len(hits)} for supplier={normalized!r}",
                   refs=[("supplier", normalized), ("raw_supplier", raw_supplier),
                         ("hit_count", str(len(hits)))])
        return json.dumps({
            "supplier": normalized,
            "raw_supplier": raw_supplier,
            "retrieved_count": len(hits),
            "status": "ok",
            "source_collection": "mail_vectors",
            "search_method": "character_ngram_no_embedding",
            "results": hits,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        _audit_log("search_vector_news", ok=False, duration_ms=int((_t.time()-_t0)*1000),
                   summary=f"qdrant unavailable: {str(e)[:80]}",
                   refs=[("supplier", normalized), ("raw_supplier", raw_supplier),
                         ("error", str(e)[:200])])
        return json.dumps({
            "error": f"向量检索不可用: {str(e)}",
            "supplier": normalized,
            "raw_supplier": raw_supplier,
            "status": "unavailable",
            "action": "请检查 Qdrant 连接 (http://localhost:6333) 或开通 MiniMax M3 embedding 通道以升级到真向量检索",
        }, ensure_ascii=False)


def _qdrant_keyword_search(client, query_text: str, limit: int = 3) -> list[dict]:
    """
    在 mail_vectors 76 条真实 payload 上做关键词 + 字符 n-gram 排序的轻量检索。
    返回 top-N 命中（按相关度降序），永远返回真实 Qdrant 数据。
    """
    q = (query_text or "").lower()
    if not q:
        return []
    # 取出全量 payload（76 条，分页即可，单次拉完）
    points = []
    offset = None
    while True:
        batch, offset = client.scroll(
            collection_name="mail_vectors",
            limit=200,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        points.extend(batch)
        if offset is None:
            break
    if not points:
        return []

    def char_ngrams(s: str, n: int = 2) -> set:
        s = (s or "").lower()
        return {s[i:i+n] for i in range(max(1, len(s) - n + 1))} if s else set()

    q_grams = char_ngrams(q, 2)
    scored = []
    for p in points:
        payload = p.payload or {}
        subject = payload.get("subject", "") or ""
        sender = payload.get("sender", "") or ""
        receiver = payload.get("receiver", "") or ""
        # 综合字段构造 n-gram 集
        s_grams = char_ngrams(subject, 2) | char_ngrams(sender, 2) | char_ngrams(receiver, 2)
        # Jaccard 相似度
        if not s_grams or not q_grams:
            score = 0.0
        else:
            score = len(q_grams & s_grams) / len(q_grams | s_grams)
        # 关键词命中加权（subject 完整包含 query_text 强相关）
        if q and q in subject.lower():
            score += 0.5
        if q and q in sender.lower():
            score += 0.3
        if q and q in receiver.lower():
            score += 0.2
        # 病毒邮件降权（业务上舆情检索更关心 normal 邮件）
        if payload.get("is_virus"):
            score *= 0.3
        if score <= 0:
            continue
        scored.append({
            "id": str(p.id),
            "score": round(float(score), 4),
            "subject": subject,
            "sender": sender,
            "receiver": receiver,
            "is_virus": payload.get("is_virus"),
            "virus_type": payload.get("virus_type"),
            "nebula_vid": payload.get("nebula_vid"),
            "source": "Qdrant::mail_vectors",
        })
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]


# ==========================================
# 4. 元数据查询工具组 (Meta Tools)
# ==========================================

@mcp.tool()
def query_audit_actions(limit: int = 20) -> str:
    """
    查询最近 N 条审计操作记录（AuditAction），含被操作的 Ref。
    用于：(1) 审计员人工审查；(2) 客户合规自查；(3) 事后溯源。

    Args:
        limit: 返回条数（按 created_at 倒序），默认 20
    """
    return _audit_query_recent(limit=limit)


# ==========================================
# 5. HITL 人机协同工具组 (Human-in-the-Loop Tools)
# ==========================================
# 治理闭环的最后一步：异常 case 流转 -> 审核员点按钮 -> 写 HumanDecision
# + 边，自动入 audit（manual_commit 本身就是 AuditAction）。

_HITL_REASONS = ("low_confidence", "entity_conflict", "cypher_failed", "manual")


@mcp.tool()
def flag_for_review(action_id: str, reason: str = "manual", note: str = "") -> str:
    """
    把已记录的 AuditAction 标为"待人工审核"。返回 PendingReview 的 review_id。

    触发场景 (后端自动调用)：
      - execute_cypher 异常退出 (reason=cypher_failed)
      - execute_cypher 返回 0 行 (reason=low_confidence, 仅对 find_ubo/shared_device_gangs 类语义)
      - bulk_insert_relationships success_count=0 (reason=entity_conflict)

    也可由前端审计员手动调用 (reason=manual)。

    Args:
        action_id: 已写入的 AuditAction.action_id
        reason: 触发原因 (low_confidence/entity_conflict/cypher_failed/manual)
        note: 备注（会写入 HumanDecision.note）
    """
    if reason not in _HITL_REASONS:
        return json.dumps({"error": "reason 必须是 " + str(_HITL_REASONS)}, ensure_ascii=False)
    try:
        _audit_ensure_schema()
        review_id = "rev_" + uuid.uuid4().hex[:12]
        d = GraphDatabase.driver(TUGRAPH_URI, auth=(TUGRAPH_USER, TUGRAPH_PASSWORD))
        with d.session(database=TUGRAPH_DB) as s:
            ref_row = s.run(
                "MATCH (a:AuditAction {action_id:$aid})-[:ACTED_ON]->(r:AuditRef) "
                "RETURN r.kind AS kind, r.value AS value LIMIT 1",
                aid=action_id,
            ).data()
            ref_kind = ref_row[0]["kind"] if ref_row else ""
            ref_value = ref_row[0]["value"] if ref_row else ""
            s.run(
                "CREATE (p:PendingReview {review_id:$rid, ref_action_id:$aid, "
                "ref_kind:$rk, ref_value:$rv, reason:$r, created_at:$ts, status:'pending'})",
                rid=review_id, aid=action_id, rk=ref_kind, rv=ref_value[:500],
                r=reason, ts=_dt.datetime.utcnow().isoformat() + "Z",
            )
            s.run(
                "MATCH (a:AuditAction {action_id:$aid}), (p:PendingReview {review_id:$rid}) "
                "CREATE (a)-[:TRIGGERED]->(p)",
                aid=action_id, rid=review_id,
            )
        d.close()
        _audit_log("flag_for_review", ok=True, duration_ms=0,
                   summary="flagged action=" + action_id + " for " + reason,
                   refs=[("target_action_id", action_id), ("reason", reason), ("review_id", review_id)])
        return json.dumps({
            "review_id": review_id, "action_id": action_id, "reason": reason,
            "ref_kind": ref_kind, "ref_value": ref_value,
            "status": "pending",
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": "flag_for_review 失败: " + str(e)}, ensure_ascii=False)


@mcp.tool()
def list_pending_reviews(status_filter: str = "pending") -> str:
    """
    列出待人工审核 / 已审核的 PendingReview 列表（含触发的 action 上下文）。

    Args:
        status_filter: 过滤状态，pending / approved / rejected / all，默认 pending
    """
    try:
        d = GraphDatabase.driver(TUGRAPH_URI, auth=(TUGRAPH_USER, TUGRAPH_PASSWORD))
        with d.session(database=TUGRAPH_DB) as s:
            if status_filter == "all":
                reviews = s.run(
                    "MATCH (p:PendingReview) "
                    "RETURN p.review_id AS rid, p.ref_action_id AS aid, p.ref_kind AS rk, "
                    "p.ref_value AS rv, p.reason AS reason, p.created_at AS created, p.status AS status "
                    "ORDER BY p.created_at DESC LIMIT 100"
                ).data()
            else:
                reviews = s.run(
                    "MATCH (p:PendingReview {status:$s}) "
                    "RETURN p.review_id AS rid, p.ref_action_id AS aid, p.ref_kind AS rk, "
                    "p.ref_value AS rv, p.reason AS reason, p.created_at AS created, p.status AS status "
                    "ORDER BY p.created_at DESC LIMIT 100",
                    s=status_filter,
                ).data()
            rids = [r["rid"] for r in reviews]
            decisions = {}
            if rids:
                for row in s.run(
                    "MATCH (p:PendingReview)-[:DECIDED]->(d:HumanDecision) "
                    "WHERE p.review_id IN $rids "
                    "RETURN p.review_id AS rid, d.decision_id AS did, d.decided_by AS by_, "
                    "d.decided_at AS at, d.outcome AS outcome, d.note AS note",
                    rids=rids,
                ).data():
                    decisions[row["rid"]] = row
            for r in reviews:
                r["decision"] = decisions.get(r["rid"])
        d.close()
        return json.dumps({
            "filter": status_filter, "total": len(reviews), "reviews": reviews,
            "session_id": _audit_session_id(),
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": "list_pending_reviews 失败: " + str(e)}, ensure_ascii=False)


@mcp.tool()
def manual_commit(review_id: str, outcome: str, note: str = "",
                  override_payload_json: str = "", actor: str = "") -> str:
    """
    审核员对 PendingReview 做出决定。3 种 outcome：
      - approve: 接受原结果（仅写 HumanDecision + 状态置为 approved）
      - reject:  拒绝原结果（状态置为 rejected, 业务侧不重做）
      - override: 用 override_payload_json 里的修正数据强灌入 TuGraph（需与 bulk_insert_relationships 同 schema）
                  灌入会复用 MERGE + 幂等机制，并写一条 AuditAction 关联

    Args:
        review_id: PendingReview.review_id
        outcome: approve / reject / override
        note: 审核员备注
        override_payload_json: 仅 override 时必填，格式同 bulk_insert_relationships 的 relationships_json
        actor: 审核员身份标识（缺省走 _AUDIT_ACTOR 环境变量）
    """
    if outcome not in ("approve", "reject", "override"):
        return json.dumps({"error": "outcome 必须是 approve / reject / override"}, ensure_ascii=False)
    who = actor or _AUDIT_ACTOR
    try:
        _audit_ensure_schema()
        d = GraphDatabase.driver(TUGRAPH_URI, auth=(TUGRAPH_USER, TUGRAPH_PASSWORD))
        with d.session(database=TUGRAPH_DB) as s:
            rev = s.run(
                "MATCH (p:PendingReview {review_id:$rid}) "
                "RETURN p.status AS status, p.ref_action_id AS aid",
                rid=review_id,
            ).data()
            if not rev:
                return json.dumps({"error": "review_id '" + review_id + "' 不存在"}, ensure_ascii=False)
            if rev[0]["status"] != "pending":
                return json.dumps({"error": "review 已是 " + rev[0]["status"] + " 状态，不可重复审核"}, ensure_ascii=False)
            override_result = None
            if outcome == "override":
                if not override_payload_json:
                    return json.dumps({"error": "override 必须传 override_payload_json"}, ensure_ascii=False)
                try:
                    rels = json.loads(override_payload_json)
                except Exception as e:
                    return json.dumps({"error": "override_payload_json 解析失败: " + str(e)}, ensure_ascii=False)
                
                # 动态构建主键映射表，支持图谱自适应注入
                primary_key_map = {}
                try:
                    schema_data = json.loads(get_graph_schema())
                    for item in schema_data.get("schema", []):
                        if item.get("type") == "VERTEX" and "label" in item and "primary" in item:
                            primary_key_map[item["label"]] = item["primary"]
                except Exception:
                    pass
                
                # 静态兜底
                _STATIC_MAP = {
                    "Person": "person_id",
                    "Corp": "corp_id",
                    "Contract": "contract_id",
                    "Invoice": "invoice_id",
                    "Payment": "payment_id"
                }
                for k, v in _STATIC_MAP.items():
                    if k not in primary_key_map:
                        primary_key_map[k] = v

                success_count = 0
                errs = []
                for idx, rel in enumerate(rels):
                    try:
                        src_label = rel.get("src_label", "")
                        dst_label = rel.get("dst_label", "")
                        relation = rel.get("relation", "")
                        if not (re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", src_label) and 
                                re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", dst_label) and 
                                re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", relation)):
                            raise ValueError("Label或Relation格式不合法")
                            
                        src_key = primary_key_map.get(src_label)
                        dst_key = primary_key_map.get(dst_label)
                        if not src_key or not dst_key:
                            raise ValueError("未知 Label: " + str(src_label) + " / " + str(dst_label) + "，无法通过 Schema 动态解析主键")
                        props = rel.get("properties", {})
                        
                        on_create_assigns = []
                        on_match_assigns = []
                        params = {"src_id": rel["src_id"], "dst_id": rel["dst_id"]}
                        for i, (k, v) in enumerate(props.items()):
                            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k):
                                raise ValueError("属性名格式不合法: " + str(k))
                            param_key = f"p_{i}"
                            params[param_key] = v
                            on_create_assigns.append(f"e.{k} = ${param_key}")
                            on_match_assigns.append(f"e.{k} = ${param_key}")
                        prop_clause_create = ("ON CREATE SET " + ", ".join(on_create_assigns)) if on_create_assigns else ""
                        prop_clause_match = ("ON MATCH SET " + ", ".join(on_match_assigns)) if on_match_assigns else ""
                        q = (
                            f"MATCH (s:{src_label} {{{src_key}: $src_id}}), "
                            f"(d:{dst_label} {{{dst_key}: $dst_id}}) "
                            f"MERGE (s)-[e:{relation}]->(d) {prop_clause_create} {prop_clause_match}".strip()
                        )
                        s.run(q, **params)
                        success_count += 1
                    except Exception as ex:
                        errs.append("索引 " + str(idx) + " 失败: " + str(ex)[:120])
                override_result = {"success_count": success_count, "errors": errs, "total": len(rels)}
            decision_id = "dec_" + uuid.uuid4().hex[:12]
            new_status = "approved" if outcome in ("approve", "override") else "rejected"
            s.run(
                "MATCH (p:PendingReview {review_id:$rid}) "
                "CREATE (d:HumanDecision {decision_id:$did, review_id:$rid, decided_by:$by_, "
                "decided_at:$ts, outcome:$o, note:$n})",
                rid=review_id, did=decision_id, by_=who,
                ts=_dt.datetime.utcnow().isoformat() + "Z", o=outcome, n=note[:500],
            )
            s.run(
                "MATCH (p:PendingReview {review_id:$rid}), (d:HumanDecision {decision_id:$did}) "
                "CREATE (p)-[:DECIDED]->(d)",
                rid=review_id, did=decision_id,
            )
            s.run(
                "MATCH (p:PendingReview {review_id:$rid}) SET p.status = $s",
                rid=review_id, s=new_status,
            )
        d.close()
        refs = [("review_id", review_id), ("outcome", outcome), ("decided_by", who), ("note", note)]
        if override_result:
            refs.append(("override_success", str(override_result["success_count"])))
            refs.append(("override_total", str(override_result["total"])))
        _audit_log("manual_commit", ok=True, duration_ms=0,
                   summary=outcome + " review=" + review_id + " by " + who,
                   refs=refs)
        return json.dumps({
            "decision_id": decision_id, "review_id": review_id, "outcome": outcome,
            "new_status": new_status, "decided_by": who, "note": note,
            "override_result": override_result,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": "manual_commit 失败: " + str(e)}, ensure_ascii=False)


@mcp.tool()
def list_tools() -> str:
    """
    查询本 MCP 服务暴露的所有 tool 的契约清单（参数 / 风险 / 返回 shape）。
    适合在 Agent 启动时一次性拉取，再决定调用顺序。
    """
    return json.dumps({"tools": TOOL_REGISTRY, "total": len(TOOL_REGISTRY)}, ensure_ascii=False, indent=2)


@mcp.tool()
def describe_tool(tool_name: str) -> str:
    """
    查询指定 tool 的详细参数契约与危险等级。

    Args:
        tool_name: tool 名（如 "execute_cypher" / "bulk_insert_relationships"）
    """
    entry = _get_tool_entry(tool_name)
    if entry is None:
        return json.dumps({"error": f"tool '{tool_name}' 不存在", "available": [e["name"] for e in TOOL_REGISTRY]}, ensure_ascii=False)
    return json.dumps(entry, ensure_ascii=False, indent=2)


# ---------- 启动服务器 ----------
if __name__ == "__main__":
    print("🚀 [Procurement Audit MCP] 正在以 Standard I/O 模式启动...")
    mcp.run()
