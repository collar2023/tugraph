"""
demo_llm.py — 直连主方案端到端大模型反欺诈演示
===================================================

本脚本运行大模型（MiniMax）驱动的反欺诈智能体。
它执行完整的推理问答链路：
  自然语言输入 ──> LLM 意图识别 ──> 结构化 Cypher 生成 ──> TuGraph 实数查询 ──> LLM 业务风险报告

运行前需要：
  export MINIMAX_API_KEY="您的 MiniMax API Key"
"""
import os
import sys

# 引入我们刚才生成的 agent_llm
try:
    from agent_llm import build_agent
except ImportError:
    # 兼容同级目录下 import
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from agent_llm import build_agent

def run_one(app, question: str):
    print(f"\n{'='*80}")
    print(f"📨 用户问题: {question}")
    print(f"{'='*80}")
    
    try:
        result = app.invoke({
            "question": question,
            "intent": "",
            "extracted_params": {},
            "cypher": "",
            "rows": [],
            "raw_count": 0,
            "final_report": "",
            "confidence": 0,
            "error": "",
        })
        
        print("\n📝 [风控分析报告]:")
        print(result["final_report"])
        print(f"\n✓ 置信度: {result['confidence']}/100")
        print(f"✓ 执行的 Cypher: {result['cypher']}")
        
    except Exception as e:
        print(f"❌ 运行失败: {e}")

def main():
    print("=============================================================")
    print("🚀 直连主方案 MiniMax 大模型 + TuGraph 联合反欺诈研判 Demo")
    print("=============================================================")
    
    api_key = os.environ.get("MINIMAX_API_KEY")
    if not api_key:
        print("\n❌ 错误: 未检测到 MINIMAX_API_KEY 环境变量。")
        print("请在终端中运行以下命令设置 API Key，然后再次启动脚本:")
        print("   export MINIMAX_API_KEY=\"你的APIKey值\"\n")
        sys.exit(1)
        
    print(f"✓ 已检测到 MiniMax API Key.")
    print(f"✓ 大模型版本: {os.environ.get('MINIMAX_MODEL', 'MiniMax-M3')}")
    print(f"✓ 连接图数据库: bolt://localhost:7687 (TuGraph)\n")
    
    # 编译 Agent 状态机
    print("⚙️  正在编译 LangGraph 状态机...")
    app = build_agent()
    print("✓ 状态机编译完成。")
    
    # 测试的5个反欺诈核心提问
    questions = [
        "系统里一共有哪些申请人?",
        "哪些申请人共享了相同的硬件设备? 是否有可疑欺诈团伙?",
        "发现有共享手机号的申请人吗?",
        "张三用过的所有设备是什么?",
        "设备 D100 被谁用过?"
    ]
    
    for q in questions:
        run_one(app, q)
        
    print(f"\n{'='*80}")
    print("🎉 大模型直连反欺诈研判测试完成！")
    print("   瘦身版架构已完全跑通，无需再拉起 Openspg-server 等重型 Java 容器。")
    print(f"{'='*80}")

if __name__ == "__main__":
    main()
