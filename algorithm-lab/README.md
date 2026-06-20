# 🧪 Nebula-Intelligence 算法交互实验室 (Algorithm Lab)

本目录由 **“实验室全自动管理器 (Lab-Orchestrator)”** 统一管理，支持跨领域关联演算。

## 📋 实验室管理 Agent：`lab_manager.py` (LangGraph)

系统不再是碎片的脚本集合，而是一个由 **首席科学家 Agent** 控制的智库：

### 1. 核心调度流程
- **Inventory Node**: 自动盘点实验室中可用的子演算场景（如股权、欺诈）。
- **Execution Nodes**: 按序执行股权穿透和欺诈扫描。
- **Cross-Reflection (跨域反思)**: **这是实验室的核心灵魂**。
  - **逻辑**: 如果 `shareholding` 发现了活跃节点，在 `fraud_detection` 扫描中同样留有痕迹。
  - **输出**: Agent 会主动指出——“该大股东在反欺诈库中已有污点”，实现跨空间的风控对齐。

### 2. 子实验室列表
- **`shareholding/`**: 寻找公司最终受益人 (UBO)。
- **`fraud_detection/`**: 发现资产共用风险。
- **`basketball/`**: 社交关系链条分析。

## 🚀 实验室全自动运行
```bash
cd algorithm-lab
# 启动管理器一键巡检所有实验室
python3 lab_manager.py
```

## 🛠️ 环境依赖
本目录已配置统一的 `package.json` 依赖，供所有子场景共享：
- **`nebula-nodejs`**: 基础连接。
- **`langgraph`**: 状态机编排。

---
**维护者**: Gemini CLI Agent
**管理模式**: 全自动场景调度
**更新日期**: 2026-03-26
