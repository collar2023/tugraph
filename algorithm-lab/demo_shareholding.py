"""
demo_shareholding.py — 股权穿透/最终受益人(UBO)大模型直连研判测试 Demo (TuGraph 版本)
================================================================================

执行流程:
  自然语言输入 ──> LLM 意图识别与参数提取 ──> Cypher 生成 ──> TuGraph 数据库穿透查询 ──> UBO 合规研判报告

运行前需要设置 MiniMax API Key:
  export MINIMAX_API_KEY="您的 MiniMax API Key"
"""
import os
import sys

try:
    from shareholding_agent_tugraph import build_agent
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from shareholding_agent_tugraph import build_agent

def run_one(app, question: str):
    print(f"\n{'='*80}")
    print(f"📨 用户提问: {question}")
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
        
        print("\n📝 [股权合规研判报告]:")
        print(result["final_report"])
        print(f"\n✓ 置信度: {result['confidence']}/100")
        print(f"✓ 执行的 Cypher: {result['cypher']}")
        
    except Exception as e:
        print(f"❌ 运行失败: {e}")

def main():
    print("=============================================================")
    print("🚀 实验室 (Algorithm-Lab) MiniMax + TuGraph 股权穿透 Demo")
    print("=============================================================")
    
    api_key = os.environ.get("MINIMAX_API_KEY")
    if not api_key:
        print("\n❌ 错误: 未检测到 MINIMAX_API_KEY 环境变量。")
        print("请在终端中运行以下命令设置 API Key，然后再次启动脚本:")
        print("   export MINIMAX_API_KEY=\"你的APIKey值\"\n")
        sys.exit(1)
        
    print(f"✓ 已检测到 MiniMax API Key.")
    print(f"✓ 连接 TuGraph: bolt://localhost:7687 (DB: default)\n")
    
    print("⚙️  正在编译 LangGraph 状态机...")
    app = build_agent()
    print("✓ 状态机编译完成。")
    
    # 测试问题
    questions = [
        "系统里有哪些公司?",
        "帮我分析 Hicks PLC 的股权结构和最终受益人(UBO)有哪些?"
    ]
    
    for q in questions:
        run_one(app, q)
        
    print(f"\n{'='*80}")
    print("🎉 股权穿透直连测试全部完成！")
    print("   瘦身版架构已成功拓展至【股权研判】场景。")
    print(f"{'='*80}")

if __name__ == "__main__":
    main()
