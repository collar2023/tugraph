const GENERATE_DIFFICULTY = "medium"; // 范例生成难度：'low' / 'medium' / 'high'

const SYSTEM_PROMPT = `你是一名世界级的前线部署工程师（FDE，Forward Deployed Engineer），精通 Palantir Ontology（本体）与动力学架构设计。
你的任务是将用户输入的一句话或一段业务描述，深度解构并映射为“十层思维逻辑”模型（包括四层本体与动力学架构、五层治理与运营架构、以及一层顶层抽象）。

你必须输出一个合法的 JSON 对象，不包含任何 Markdown 格式包裹（直接输出 JSON 文本，不要用 \`\`\`json 格式）。确保 JSON 的所有 Key 和 Value 符合以下结构：

{
  "ontology": {
    "objects": [
      { "id": "唯一英文ID", "name": "中文名称", "type": "中文类型(如物理资产/人员主体/合规文件/企业机构等)", "status": "中文当前状态属性" }
    ],
    "links": [
      { "source": "源点ID", "target": "终点ID", "label": "中文链接名称(如满足申请条件/划转资金)", "properties": { "name": "中文属性名", "type": "中文属性类型" } }
    ],
    "rules": [
      { "name": "中文验证规则与约束名", "rule": "中文物理限额/动作拦截校验规则说明" }
    ],
    "actions": [
      { "name": "中文行动名称", "description": "中文行动描述", "trigger": "中文触发条件", "result": "中文状态属性变更结果" }
    ]
  },
  "governance": {
    "stakeholders": [
      { "role": "中文利益相关角色", "responsibility": "中文核心职责" }
    ],
    "policies": [
      { "name": "中文企业策略/标准名称", "description": "中文规范与准则说明" }
    ],
    "metrics": [
      { "name": "中文度量指标名称", "value": "中文模拟评估值(如75%或正常等)" }
    ],
    "risks": [
      { "name": "中文风险点名称", "impact": "中文对现金流或业务的影响" }
    ],
    "automation": [
      { "process": "中文自动化工作流名称", "trigger_rule": "中文触发与执行规则" }
    ]
  },
  "summary": "一句中文顶层抽象总结：说明如何通过工程进度和业务动作将物理事实转化为经营权或财务目标。"
}

请注意：
1. 必须根据用户选择的“行业背景”进行针对性的专业建模（例如房地产行业要扣紧工程进度、预售证和资金监管；金融审计要扣紧三方对账和虚开；反欺诈要扣紧设备、手机号 and 团伙关联等）。
2. Rules 必须是不可逾越的底线逻辑验证规则。
3. 特别重要：本系统是展示给中国企业家/CEO查看的经营决策治理模拟盘。因此，除了对象 id 和关联的 source/target id 允许使用英文大写字母标识（如 BUILDING_12、PRE_SALE_PERMIT）以方便计算外，JSON 对象中的所有其他属性、名称、关系 label、对象类型 type、行动 action、状态 status 等必须一律且尽量使用中文输出，不得出现多余的英文标识。`;

const HTML_CONTENT = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>大模型本体建模模拟器</title>
  <!-- Tailwind CSS -->
  <script src="https://cdn.tailwindcss.com"></script>
  <!-- Google Fonts -->
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
  <style>
    body {
      font-family: 'Inter', sans-serif;
      background: radial-gradient(circle at center, #111424 0%, #070913 100%);
      color: #e2e8f0;
      min-height: 100vh;
    }
    .code-font {
      font-family: 'JetBrains Mono', monospace;
    }
    .glass {
      background: rgba(16, 20, 38, 0.6);
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
      border: 1px solid rgba(255, 255, 255, 0.05);
      box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
    }
    .glow-cyan {
      box-shadow: 0 0 15px rgba(6, 182, 212, 0.3);
    }
    .glow-violet {
      box-shadow: 0 0 15px rgba(139, 92, 246, 0.3);
    }
    .node-glow-cyan {
      filter: drop-shadow(0 0 8px rgba(6, 182, 212, 0.7));
    }
    .node-glow-violet {
      filter: drop-shadow(0 0 8px rgba(139, 92, 246, 0.7));
    }
    .node-glow-emerald {
      filter: drop-shadow(0 0 8px rgba(16, 185, 129, 0.7));
    }
    .node-glow-amber {
      filter: drop-shadow(0 0 8px rgba(245, 158, 11, 0.7));
    }
  </style>
</head>
<body class="p-4 md:p-6">
  <div class="max-w-[1600px] mx-auto space-y-6">
    <!-- Header -->
    <header class="flex flex-col md:flex-row justify-between items-center glass p-4 rounded-xl gap-4">
      <div class="flex items-center gap-3">
        <svg class="w-8 h-8 text-cyan-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"></path>
        </svg>
        <h1 class="text-xl font-bold tracking-wider text-transparent bg-clip-text bg-gradient-to-r from-cyan-400 to-violet-400">大模型本体建模模拟器</h1>
      </div>
      <div class="flex items-center gap-3">
        <label for="industry" class="text-sm text-gray-400">行业场景:</label>
        <select id="industry" class="bg-slate-900 border border-slate-700 text-gray-200 px-3 py-1.5 rounded-lg focus:outline-none focus:border-cyan-400 text-sm">
          <option value="Real Estate">房地产与空间资产</option>
          <option value="High-End Manufacturing">高端制造与工业物联网</option>
          <option value="Healthcare">医疗健康与临床合规</option>
          <option value="Finance & Audit">金融审计与反洗钱</option>
          <option value="Logistics & Supply Chain">智慧物流与供应链</option>
          <option value="Sports Education">体育教育与青少年培训</option>
          <option value="AI Governance & Software Engineering" selected>企业AI数字化治理与软件工程</option>
        </select>
      </div>
    </header>

    <!-- Main Workspace -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      
      <!-- Left Column: Input (Q1) & CEO View (Q3) -->
      <div class="space-y-6 flex flex-col">
        <!-- Q1: Input Query -->
        <div class="glass p-5 rounded-xl flex-1 flex flex-col gap-4">
          <div class="flex justify-between items-center">
            <h2 class="text-base font-semibold text-cyan-400">数据源与业务文本输入 (Q1)</h2>
            <div class="text-xs text-gray-500">自然语言或导入多模态资产</div>
          </div>
          <textarea id="inputText" class="flex-1 w-full min-h-[100px] bg-slate-950 border border-slate-800 rounded-lg p-3 text-sm focus:outline-none focus:border-cyan-500 text-gray-100 placeholder-slate-600 resize-y" placeholder="例如：系统检测到小王提交的PR中包含了未审计的第三方大模型 API 调用，触发了企业AI数字化合规准则中的数据出境安全红线，该PR的自动合并动作被拦截，并指派首席合规官进行人工审计。"></textarea>
          
          <!-- File Upload Zone -->
          <div id="dropzone" class="border border-dashed border-slate-700 hover:border-cyan-500/50 rounded-lg p-3 text-center cursor-pointer transition-all bg-slate-950/40 relative">
            <input type="file" id="fileInput" class="hidden" accept=".csv,.pdf,.txt,.json,.png,.jpg,.jpeg,.webp" />
            <div id="dropzonePrompt" class="space-y-1">
              <div class="text-slate-400 text-xs font-semibold flex items-center justify-center gap-1.5">
                <span>📥</span> 拖拽或点击导入 CSV, PDF, 图像等数据资产
              </div>
              <div class="text-[10px] text-slate-500">支持多模态实体抽取与映射 (文件大小上限 4MB)</div>
            </div>
            <!-- Uploaded File Preview Card (Hidden by default) -->
            <div id="filePreviewCard" class="hidden flex items-center justify-between bg-slate-900/80 border border-slate-800 rounded-md p-2 text-left">
              <div class="flex items-center gap-2 overflow-hidden">
                <span id="fileIcon" class="text-base">📄</span>
                <div class="overflow-hidden">
                  <div id="fileName" class="text-xs text-gray-200 font-semibold truncate">data.csv</div>
                  <div id="fileInfo" class="text-[10px] text-slate-500">12.5 KB • CSV</div>
                </div>
              </div>
              <button id="removeFileBtn" class="text-slate-400 hover:text-rose-400 p-1 text-xs transition-colors">✕</button>
            </div>
          </div>

          <div class="flex gap-3 justify-between items-center w-full">
            <div>
              <button id="injectExampleBtn" type="button" class="px-3 py-2 bg-slate-850 hover:bg-slate-800 border border-slate-700 hover:border-cyan-500/50 text-cyan-400 font-semibold rounded-lg text-xs transition-all active:scale-95">
                随机生成范例
              </button>
            </div>
            <div class="flex gap-2">
              <button id="clearBtn" type="button" class="px-4 py-2 border border-slate-700 hover:bg-slate-800 transition-colors text-xs font-semibold rounded-lg">清空</button>
              <button id="analyzeBtn" type="button" class="px-5 py-2 bg-gradient-to-r from-cyan-500 to-cyan-600 hover:from-cyan-600 hover:to-cyan-700 text-slate-950 font-bold rounded-lg text-xs shadow-lg transition-all duration-300 transform active:scale-95">开始多模态抽取</button>
            </div>
          </div>
        </div>

        <!-- Q3: CEO Operational View -->
        <div class="glass p-5 rounded-xl min-h-[350px] flex flex-col">
          <h2 class="text-base font-semibold text-violet-400 mb-4">CEO 决策治理模拟盘 (Governance View)</h2>
          <div id="ceoGrid" class="grid grid-cols-1 md:grid-cols-2 gap-4 flex-1">
            <!-- Stakeholders -->
            <div class="bg-slate-950/60 p-3 rounded-lg border border-slate-900 flex flex-col">
              <div class="text-xs font-bold text-gray-400 mb-2 flex items-center gap-1.5">
                <span class="w-1.5 h-1.5 bg-blue-400 rounded-full"></span> 利益相关方 (Stakeholders)
              </div>
              <ul id="stakeholdersList" class="text-xs text-gray-300 space-y-1.5 overflow-y-auto max-h-[100px]">
                <li class="text-slate-600 italic">暂无数据</li>
              </ul>
            </div>
            <!-- Policy -->
            <div class="bg-slate-950/60 p-3 rounded-lg border border-slate-900 flex flex-col">
              <div class="text-xs font-bold text-gray-400 mb-2 flex items-center gap-1.5">
                <span class="w-1.5 h-1.5 bg-emerald-400 rounded-full"></span> 企业策略 (Policy)
              </div>
              <ul id="policiesList" class="text-xs text-gray-300 space-y-1.5 overflow-y-auto max-h-[100px]">
                <li class="text-slate-600 italic">暂无数据</li>
              </ul>
            </div>
            <!-- Metrics -->
            <div class="bg-slate-950/60 p-3 rounded-lg border border-slate-900 flex flex-col">
              <div class="text-xs font-bold text-gray-400 mb-2 flex items-center gap-1.5">
                <span class="w-1.5 h-1.5 bg-cyan-400 rounded-full"></span> 度量指标 (Metrics)
              </div>
              <ul id="metricsList" class="text-xs text-gray-300 space-y-1.5 overflow-y-auto max-h-[100px]">
                <li class="text-slate-600 italic">暂无数据</li>
              </ul>
            </div>
            <!-- Risks -->
            <div class="bg-slate-950/60 p-3 rounded-lg border border-slate-900 flex flex-col">
              <div class="text-xs font-bold text-gray-400 mb-2 flex items-center gap-1.5">
                <span class="w-1.5 h-1.5 bg-amber-500 rounded-full"></span> 风险监控 (Risks)
              </div>
              <ul id="risksList" class="text-xs text-gray-300 space-y-1.5 overflow-y-auto max-h-[100px]">
                <li class="text-slate-600 italic">暂无数据</li>
              </ul>
            </div>
          </div>
          <!-- Automation -->
          <div class="mt-4 bg-slate-950/80 p-3 rounded-lg border border-slate-900">
            <div class="text-xs font-bold text-violet-400 mb-1.5 flex items-center gap-1.5">
              🚀 触发自动化工作流 (Automation)
            </div>
            <div id="automationText" class="text-xs text-gray-300 italic">暂无激活的工作流触发器</div>
          </div>
        </div>
      </div>

      <!-- Right Column: Graph (Q2) & Technical Schema (Q4) -->
      <div class="space-y-6 flex flex-col">
        <!-- Q2: Graph Visualization -->
        <div class="glass p-5 rounded-xl flex-1 min-h-[380px] flex flex-col">
          <h2 class="text-base font-semibold text-cyan-400 mb-3">本体拓扑网络可视化 (Ontology Network)</h2>
          <div class="relative flex-1 w-full bg-slate-950/85 rounded-lg border border-slate-900 overflow-hidden flex items-center justify-center">
            <svg id="networkSvg" class="w-full h-full min-h-[300px]">
              <!-- Grids background -->
              <defs>
                <pattern id="grid" width="30" height="30" patternUnits="userSpaceOnUse">
                  <path d="M 30 0 L 0 0 0 30" fill="none" stroke="rgba(255,255,255,0.02)" stroke-width="1"/>
                </pattern>
                <marker id="arrow" viewBox="0 0 10 10" refX="22" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                  <path d="M 0 1 L 10 5 L 0 9 z" fill="rgba(6,182,212,0.6)" />
                </marker>
              </defs>
              <rect width="100%" height="100%" fill="url(#grid)" />
              <g id="linksGroup"></g>
              <g id="nodesGroup"></g>
            </svg>
            <div id="loadingOverlay" class="absolute inset-0 bg-slate-950/80 hidden flex-col items-center justify-center gap-3">
              <div class="w-10 h-10 border-4 border-cyan-400 border-t-transparent rounded-full animate-spin"></div>
              <div class="text-xs text-cyan-400 font-semibold tracking-wider animate-pulse">大模型正在编译本体中...</div>
            </div>
          </div>
        </div>

        <!-- Q4: 10-Layer Ontology & Governance -->
        <div class="glass p-5 rounded-xl min-h-[320px] flex flex-col">
          <h2 class="text-base font-semibold text-emerald-400 mb-3">四段十层本体建模抽象图景 (10-Layer Ontology & Governance)</h2>
          <div class="flex-1 overflow-auto bg-slate-950 p-4 rounded-lg border border-slate-900 max-h-[280px] text-xs leading-relaxed space-y-4" id="reportViewer">
            <div class="text-slate-500 italic">等待分析并生成十层决策模型...</div>
          </div>
        </div>
      </div>

    </div>

    <!-- Summary (Bottom Bar) -->
    <footer class="glass p-4 rounded-xl">
      <div class="text-xs font-bold text-cyan-400 mb-1 flex items-center gap-1.5">
        🧠 顶层经营抽象 (Ontology Summary)
      </div>
      <p id="summaryText" class="text-xs text-gray-300 leading-relaxed italic">请在上方输入语句并点击运行，大模型将自动生成其本体的顶层抽象概括。</p>
    </footer>
  </div>

  <script>
    const industryExamples = {
      "Real Estate": "12#楼已经主体建到第十层了,可以拿预售证回笼资金了。",
      "High-End Manufacturing": "3号机械臂的伺服电机温度在运行中连续过热超标，触发安全停机行动，并生成设备维护工单以规避停产损失。",
      "Healthcare": "患者张三有严重的青霉素过敏史，但在系统下达处方Action时却配了阿莫西林胶囊，触发临床安全红线警报并予以拦截。",
      "Finance & Audit": "采购合同2026-A109由买方付了全款，但供应商却开具了不同金额的发票，怀疑有虚开发票或账目不符风险。",
      "Logistics & Supply Chain": "冷链集装箱CONT_029的温度在过去2小时内持续飙升至12度以上，需要紧急指派调度员拦截，并将生鲜货物进行转移避险。",
      "Sports Education": "青少年篮球班今天排课时，指派的张教练其红十字救护员证书已过期，触发排课合规性审查警报，排课动作被自动拦截并自动指派合格的替补教练。",
      "AI Governance & Software Engineering": "系统检测到小王提交的PR中包含了未审计的第三方大模型 API 调用，触发了企业AI数字化合规准则中的数据出境安全红线，该PR的自动合并动作被拦截，并指派首席合规官进行人工审计。"
    };

    const industrySelector = document.getElementById("industry");
    const inputText = document.getElementById("inputText");
    const clearBtn = document.getElementById("clearBtn");
    const analyzeBtn = document.getElementById("analyzeBtn");
    const injectExampleBtn = document.getElementById("injectExampleBtn");
    const reportViewer = document.getElementById("reportViewer");
    const summaryText = document.getElementById("summaryText");
    const stakeholdersList = document.getElementById("stakeholdersList");
    const policiesList = document.getElementById("policiesList");
    const metricsList = document.getElementById("metricsList");
    const risksList = document.getElementById("risksList");
    const automationText = document.getElementById("automationText");
    const loadingOverlay = document.getElementById("loadingOverlay");

    // File Upload Elements
    const dropzone = document.getElementById("dropzone");
    const fileInput = document.getElementById("fileInput");
    const dropzonePrompt = document.getElementById("dropzonePrompt");
    const filePreviewCard = document.getElementById("filePreviewCard");
    const fileName = document.getElementById("fileName");
    const fileInfo = document.getElementById("fileInfo");
    const fileIcon = document.getElementById("fileIcon");
    const removeFileBtn = document.getElementById("removeFileBtn");

    let uploadedFile = null;

    industrySelector.addEventListener("change", () => {
      inputText.placeholder = "例如：" + industryExamples[industrySelector.value];
    });

    injectExampleBtn.addEventListener("click", async () => {
      const industry = industrySelector.value;
      const originalText = injectExampleBtn.textContent;
      injectExampleBtn.textContent = "生成中...";
      injectExampleBtn.disabled = true;
      inputText.value = "正在由大模型动态构建逼真的业务场景...";

      try {
        const response = await fetch("/api/generate-example", {
          method: "POST",
          headers: {
            "Content-Type": "application/json"
          },
          body: JSON.stringify({ industry })
        });
        const result = await response.json();
        if (result.error) {
          alert("生成失败: " + result.error);
          inputText.value = "";
        } else {
          let fullText = result.text || "";
          inputText.value = "";
          let idx = 0;
          const speed = 15;
          function typeWriter() {
            if (idx < fullText.length) {
              inputText.value += fullText.charAt(idx);
              idx++;
              setTimeout(typeWriter, speed);
            }
          }
          typeWriter();
        }
      } catch (err) {
        alert("请求发生错误: " + err);
        inputText.value = "";
      } finally {
        injectExampleBtn.textContent = originalText;
        injectExampleBtn.disabled = false;
      }
    });

    clearBtn.addEventListener("click", () => {
      inputText.value = "";
      clearUploadedFile();
      reportViewer.innerHTML = '<div class="text-slate-500 italic">等待分析并生成十层决策模型...</div>';
      summaryText.textContent = "请在上方输入语句并点击运行，大模型将自动生成其本体的顶层抽象概括。";
      stakeholdersList.innerHTML = '<li class="text-slate-600 italic">暂无数据</li>';
      policiesList.innerHTML = '<li class="text-slate-600 italic">暂无数据</li>';
      metricsList.innerHTML = '<li class="text-slate-600 italic">暂无数据</li>';
      risksList.innerHTML = '<li class="text-slate-600 italic">暂无数据</li>';
      automationText.innerHTML = '暂无激活的工作流触发器';
      clearGraph();
    });

    // File handling functionality
    dropzone.addEventListener("click", (e) => {
      if (e.target.closest("#removeFileBtn") || e.target.closest("#filePreviewCard")) {
        return;
      }
      fileInput.click();
    });

    ["dragenter", "dragover"].forEach(eventName => {
      dropzone.addEventListener(eventName, (e) => {
        e.preventDefault();
        e.stopPropagation();
        dropzone.classList.add("border-cyan-500", "bg-cyan-500/5");
      }, false);
    });

    ["dragleave", "drop"].forEach(eventName => {
      dropzone.addEventListener(eventName, (e) => {
        e.preventDefault();
        e.stopPropagation();
        dropzone.classList.remove("border-cyan-500", "bg-cyan-500/5");
      }, false);
    });

    dropzone.addEventListener("drop", (e) => {
      const dt = e.dataTransfer;
      const files = dt.files;
      if (files.length > 0) {
        handleFile(files[0]);
      }
    });

    fileInput.addEventListener("change", () => {
      if (fileInput.files.length > 0) {
        handleFile(fileInput.files[0]);
      }
    });

    function getFileIcon(type, name) {
      if (type.startsWith("image/")) return "🖼️";
      if (type === "application/pdf") return "📕";
      if (name.endsWith(".csv")) return "📊";
      if (name.endsWith(".json")) return "⚙️";
      return "📄";
    }

    function formatBytes(bytes) {
      if (bytes === 0) return "0 B";
      const k = 1024;
      const sizes = ["B", "KB", "MB", "GB"];
      const i = Math.floor(Math.log(bytes) / Math.log(k));
      return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
    }

    async function handleFile(file) {
      if (file.size > 4 * 1024 * 1024) {
        alert("文件大小不能超过 4MB！");
        return;
      }
      try {
        const base64Data = await fileToBase64(file);
        uploadedFile = {
          name: file.name,
          type: file.type || "application/octet-stream",
          data: base64Data
        };

        fileName.textContent = file.name;
        fileInfo.textContent = formatBytes(file.size) + " • " + (file.type ? file.type.split("/")[1].toUpperCase() : "UNKNOWN");
        fileIcon.textContent = getFileIcon(file.type, file.name);

        dropzonePrompt.classList.add("hidden");
        filePreviewCard.classList.remove("hidden");
        dropzone.classList.remove("border-dashed");
        dropzone.classList.add("border-solid", "border-cyan-500/30");
      } catch (err) {
        alert("文件读取失败: " + err.message);
      }
    }

    function fileToBase64(file) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.readAsDataURL(file);
        reader.onload = () => {
          const base64 = reader.result.split(",")[1];
          resolve(base64);
        };
        reader.onerror = error => reject(error);
      });
    }

    removeFileBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      clearUploadedFile();
    });

    function clearUploadedFile() {
      uploadedFile = null;
      fileInput.value = "";
      filePreviewCard.classList.add("hidden");
      dropzonePrompt.classList.remove("hidden");
      dropzone.classList.remove("border-solid", "border-cyan-500/30");
      dropzone.classList.add("border-dashed");
    }

    function clearGraph() {
      document.getElementById("linksGroup").innerHTML = "";
      document.getElementById("nodesGroup").innerHTML = "";
    }

    // Topology Graph Globals & Drag & Drop
    let draggedNode = null;
    let draggedG = null;
    let activeNodesMap = {};
    let activeRelations = [];
    let activeLinksGroup = null;
    let activeWidth = 600;
    let activeHeight = 350;

    const svg = document.getElementById("networkSvg");
    svg.addEventListener("mousemove", (e) => {
      if (!draggedNode || !draggedG) return;
      const rect = svg.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      
      draggedNode.x = Math.max(20, Math.min((rect.width || activeWidth) - 20, x));
      draggedNode.y = Math.max(20, Math.min((rect.height || activeHeight) - 20, y));
      
      draggedG.setAttribute("transform", "translate(" + draggedNode.x + "," + draggedNode.y + ")");
      redrawLinks(activeRelations, activeNodesMap, activeLinksGroup);
    });

    window.addEventListener("mouseup", () => {
      draggedNode = null;
      draggedG = null;
    });

    function redrawLinks(relations, nodesMap, linksGroup) {
      if (!linksGroup) return;
      linksGroup.innerHTML = "";
      relations.forEach(link => {
        const sourceNode = nodesMap[link.source];
        const targetNode = nodesMap[link.target];

        if (sourceNode && targetNode) {
          const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
          line.setAttribute("x1", sourceNode.x);
          line.setAttribute("y1", sourceNode.y);
          line.setAttribute("x2", targetNode.x);
          line.setAttribute("y2", targetNode.y);
          line.setAttribute("stroke", "rgba(6, 182, 212, 0.4)");
          line.setAttribute("stroke-width", "2");
          line.setAttribute("marker-end", "url(#arrow)");
          linksGroup.appendChild(line);

          const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
          const midX = (sourceNode.x + targetNode.x) / 2;
          const midY = (sourceNode.y + targetNode.y) / 2;
          text.setAttribute("x", midX);
          text.setAttribute("y", midY - 6);
          text.setAttribute("fill", "#94a3b8");
          text.setAttribute("font-size", "9");
          text.setAttribute("text-anchor", "middle");
          text.setAttribute("stroke", "#0b0f19");
          text.setAttribute("stroke-width", "2px");
          text.setAttribute("paint-order", "stroke fill");
          text.textContent = link.label;
          linksGroup.appendChild(text);
        }
      });
    }

    function renderGraph(ontology) {
      clearGraph();
      const svg = document.getElementById("networkSvg");
      const rect = svg.getBoundingClientRect();
      activeWidth = rect.width || svg.clientWidth || 600;
      activeHeight = rect.height || svg.clientHeight || 350;
      const centerX = activeWidth / 2;
      const centerY = activeHeight / 2;

      const entities = ontology.objects || [];
      activeRelations = ontology.links || [];

      if (entities.length === 0) return;

      activeNodesMap = {};
      entities.forEach(node => {
        activeNodesMap[node.id] = { ...node, layer: 0, x: centerX, y: centerY };
      });

      // Iteratively push nodes to the right if they have incoming relations (Sugiyama Layout LR)
      const iterations = Math.min(10, activeRelations.length);
      for (let iter = 0; iter < iterations; iter++) {
        activeRelations.forEach(link => {
          const src = activeNodesMap[link.source];
          const tgt = activeNodesMap[link.target];
          if (src && tgt) {
            if (tgt.layer <= src.layer) {
              tgt.layer = src.layer + 1;
            }
          }
        });
      }

      // Group nodes by layer
      const layers = {};
      Object.values(activeNodesMap).forEach(node => {
        if (!layers[node.layer]) {
          layers[node.layer] = [];
        }
        layers[node.layer].push(node);
      });

      const layerKeys = Object.keys(layers).map(Number).sort((a, b) => a - b);
      const numCols = layerKeys.length;

      // Position nodes Left-to-Right
      const paddingX = activeWidth * 0.15;
      const paddingY = activeHeight * 0.15;
      const usableWidth = activeWidth - 2 * paddingX;
      const usableHeight = activeHeight - 2 * paddingY;

      layerKeys.forEach((layerKey, colIdx) => {
        const colNodes = layers[layerKey];
        const x = numCols > 1 ? paddingX + colIdx * (usableWidth / (numCols - 1)) : centerX;
        
        const numRows = colNodes.length;
        colNodes.forEach((node, rowIdx) => {
          const y = numRows > 1 ? paddingY + rowIdx * (usableHeight / (numRows - 1)) : centerY;
          node.x = x;
          node.y = y;
        });
      });

      activeLinksGroup = document.getElementById("linksGroup");
      const nodesGroup = document.getElementById("nodesGroup");

      // Draw original links
      redrawLinks(activeRelations, activeNodesMap, activeLinksGroup);

      // Draw nodes
      Object.values(activeNodesMap).forEach((node, idx) => {
        const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
        g.setAttribute("transform", "translate(" + node.x + "," + node.y + ")");
        g.style.cursor = "grab";
        
        g.addEventListener("mousedown", (e) => {
          draggedNode = node;
          draggedG = g;
          nodesGroup.appendChild(g); // bring to front
        });

        const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        circle.setAttribute("cx", "0");
        circle.setAttribute("cy", "0");
        circle.setAttribute("r", idx === 0 ? "16" : "12");
        
        let fillColor = "#1e293b";
        let strokeColor = "#64748b";
        let glowClass = "node-glow-cyan";

        const typeLower = (node.type || "").toLowerCase();
        if (idx === 0) {
          fillColor = "#06b6d4";
          strokeColor = "#22d3ee";
          glowClass = "node-glow-cyan";
        } else if (typeLower.includes("asset") || typeLower.includes("object") || typeLower.includes("物") || typeLower.includes("资")) {
          fillColor = "#064e3b";
          strokeColor = "#10b981";
          glowClass = "node-glow-emerald";
        } else if (typeLower.includes("agent") || typeLower.includes("person") || typeLower.includes("role") || typeLower.includes("人") || typeLower.includes("主")) {
          fillColor = "#0891b2";
          strokeColor = "#22d3ee";
          glowClass = "node-glow-cyan";
        } else if (typeLower.includes("corp") || typeLower.includes("org") || typeLower.includes("comp") || typeLower.includes("企") || typeLower.includes("机")) {
          fillColor = "#4c1d95";
          strokeColor = "#a78bfa";
          glowClass = "node-glow-violet";
        } else if (node.status) {
          fillColor = "#78350f";
          strokeColor = "#f59e0b";
          glowClass = "node-glow-amber";
        }
        
        circle.setAttribute("fill", fillColor);
        circle.setAttribute("stroke", strokeColor);
        circle.setAttribute("stroke-width", "2");
        circle.classList.add(glowClass);
        g.appendChild(circle);

        const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
        text.setAttribute("x", "0");
        text.setAttribute("y", idx === 0 ? "30" : "24");
        text.setAttribute("fill", "#e2e8f0");
        text.setAttribute("font-size", "10");
        text.setAttribute("text-anchor", "middle");
        text.setAttribute("stroke", "#0b0f19");
        text.setAttribute("stroke-width", "3px");
        text.setAttribute("paint-order", "stroke fill");
        text.textContent = node.name;
        g.appendChild(text);

        if (node.status) {
          const stateText = document.createElementNS("http://www.w3.org/2000/svg", "text");
          stateText.setAttribute("x", "0");
          stateText.setAttribute("y", "-18");
          stateText.setAttribute("fill", "#f59e0b");
          stateText.setAttribute("font-size", "8");
          stateText.setAttribute("font-weight", "bold");
          stateText.setAttribute("text-anchor", "middle");
          stateText.setAttribute("stroke", "#0b0f19");
          stateText.setAttribute("stroke-width", "3px");
          stateText.setAttribute("paint-order", "stroke fill");
          stateText.textContent = node.status;
          g.appendChild(stateText);
        }

        nodesGroup.appendChild(g);
      });
    }

    analyzeBtn.addEventListener("click", async () => {
      const text = inputText.value.trim();
      const industry = industrySelector.value;

      if (!text && !uploadedFile) {
        alert("请输入业务描述文字或上传数据资产进行分析！");
        return;
      }

      loadingOverlay.classList.remove("hidden");

      try {
        const response = await fetch("/api/analyze", {
          method: "POST",
          headers: {
            "Content-Type": "application/json"
          },
          body: JSON.stringify({ text, industry, file: uploadedFile })
        });

        const result = await response.json();

        if (result.error) {
          alert("错误: " + result.error);
          return;
        }

        const ontology = result.ontology || {};
        const gov = result.governance || {};

        const nodesMap = {};
        (ontology.objects || []).forEach(o => {
          nodesMap[o.id] = o;
        });

        let reportHtml = '<div class="space-y-4">' +
          '<div>' +
            '<h3 class="text-sm font-bold text-cyan-400 border-b border-slate-800 pb-1 mb-2">【层级一：静态本体与动力学 (Ontology & Kinetics)】</h3>' +
            '<ul class="space-y-2 text-gray-300">' +
              '<li><strong class="text-cyan-300 font-semibold">1. Object (对象类型):</strong> ' + (ontology.objects || []).map(o => o.name + ' (' + o.id + ' • ' + (o.status || '无状态') + ')').join(', ') + '</li>' +
              '<li><strong class="text-cyan-300 font-semibold">2. Link (关系链接):</strong> ' + (ontology.links || []).map(l => (nodesMap[l.source]?.name || l.source) + ' -(' + l.label + ')-> ' + (nodesMap[l.target]?.name || l.target)).join(', ') + '</li>' +
              '<li><strong class="text-cyan-300 font-semibold">3. Rule (验证规则):</strong> ' + (ontology.rules || []).map(r => (r.name || '规则') + ': ' + (r.rule || r.description)).join('; ') + '</li>' +
              '<li><strong class="text-cyan-300 font-semibold">4. Action (行动事务):</strong> ' + (ontology.actions || []).map(a => a.name + '(' + a.description + '): ' + a.trigger + ' ➔ ' + a.result).join('; ') + '</li>' +
            '</ul>' +
          '</div>' +
          '<div class="mt-4">' +
            '<h3 class="text-sm font-bold text-violet-400 border-b border-slate-800 pb-1 mb-2">【层级二：治理与运营 (Governance)】</h3>' +
            '<ul class="space-y-2 text-gray-300">' +
              '<li><strong class="text-violet-300 font-semibold">5. Stakeholder (利益相关方):</strong> ' + (gov.stakeholders || []).map(s => s.role + ' (' + s.responsibility + ')').join(', ') + '</li>' +
              '<li><strong class="text-violet-300 font-semibold">6. Policy (企业策略):</strong> ' + (gov.policies || []).map(p => p.name + ': ' + p.description).join('; ') + '</li>' +
              '<li><strong class="text-violet-300 font-semibold">7. Metric (运营指标):</strong> ' + (gov.metrics || []).map(m => m.name + ' (' + m.value + ')').join(', ') + '</li>' +
              '<li><strong class="text-violet-300 font-semibold">8. Risk (监控风险):</strong> ' + (gov.risks || []).map(r => r.name + ': ' + r.impact).join('; ') + '</li>' +
              '<li><strong class="text-violet-300 font-semibold">9. Automation (自动触发):</strong> ' + (gov.automation || []).map(a => '【' + a.process + '】 ' + (a.trigger_rule || '')).join('; ') + '</li>' +
            '</ul>' +
          '</div>' +
          '<div class="mt-4">' +
            '<h3 class="text-sm font-bold text-emerald-400 border-b border-slate-800 pb-1 mb-2">【层级三：顶层抽象】</h3>' +
            '<p class="text-gray-300 italic"><strong class="text-emerald-300 font-semibold">10. Summary (顶层总结):</strong> ' + (result.summary || '无') + '</p>' +
          '</div>' +
        '</div>';

        reportViewer.innerHTML = reportHtml;
        summaryText.textContent = result.summary || "未返回抽象总结";

        stakeholdersList.innerHTML = (gov.stakeholders || []).map(s => 
          '<li><span class="text-blue-400 font-bold">• ' + s.role + '</span>: ' + s.responsibility + '</li>'
        ).join("") || '<li class="text-slate-600 italic">无数据</li>';

        policiesList.innerHTML = (gov.policies || []).map(p => 
          '<li><span class="text-emerald-400 font-bold">' + p.name + '</span>: ' + p.description + '</li>'
        ).join("") || '<li class="text-slate-600 italic">无数据</li>';

        metricsList.innerHTML = (gov.metrics || []).map(m => 
          '<li class="flex justify-between"><span>' + m.name + '</span><span class="text-cyan-400 font-mono font-bold">' + m.value + '</span></li>'
        ).join("") || '<li class="text-slate-600 italic">无数据</li>';

        risksList.innerHTML = (gov.risks || []).map(r => 
          '<li><span class="text-amber-500 font-bold">' + r.name + '</span>: ' + r.impact + '</li>'
        ).join("") || '<li class="text-slate-600 italic">无数据</li>';

        if (gov.automation && gov.automation.length > 0) {
          automationText.innerHTML = gov.automation.map(a => 
            '<div>【' + a.process + '】 ' + (a.trigger_rule || '') + '</div>'
          ).join("");
          automationText.classList.remove("italic", "text-slate-600");
        } else {
          automationText.innerHTML = "暂无激活的工作流触发器";
          automationText.classList.add("italic", "text-slate-600");
        }

        renderGraph(result.ontology);

      } catch (err) {
        alert("请求发生错误，请检查网络或控制台日志: " + err);
      } finally {
        loadingOverlay.classList.add("hidden");
      }
    });
  </script>
</body>
</html>`;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // 1. GET /: Serve the interactive SPA
    if (request.method === "GET" && url.pathname === "/") {
      return new Response(HTML_CONTENT, {
        headers: { "Content-Type": "text/html;charset=UTF-8" }
      });
    }

    // 2. POST /api/generate-example: Generate dynamic business scenario examples
    if (request.method === "POST" && url.pathname === "/api/generate-example") {
      try {
        const body = await request.json();
        const industry = body.industry || "General";
        const apiKey = env.GEMINI_API_KEY;
        if (!apiKey) {
          return new Response(
            JSON.stringify({ error: "未配置 GEMINI_API_KEY" }),
            { status: 400, headers: { "Content-Type": "application/json" } }
          );
        }

        const model = env.GEMINI_MODEL || "gemini-3.1-flash-lite";
        const targetUrl = `https://ai-gateway-403802525344.asia-east1.run.app/gemini/v1beta/models/${model}:generateContent?key=${apiKey}`;

        const prompt = `请为行业场景“${industry}”从企业 CEO / 决策者的经营和因果推演视角，随机生成一段高质量的、逼真的业务话术描述或系统警报事件文本。

难度级别要求为：${GENERATE_DIFFICULTY} （初级对应单步物理状态判定；中级对应多维度状态约束与行动触发；高级对应复杂的因果传导链条、风险熔断自愈决策与多系统协同）。

要求：
1. 语言必须极其专业、真实，直击该行业的经营痛点与管理词汇（如数据防出境安全、模型权限漂移、三方API风险等）。
2. 直接输出生成的业务文本内容，严禁包含任何 Markdown 格式包裹（不要用 \`\`\` 格式）、不要包含任何“好的，以下是为您生成的...”等前导或后置客套话。
3. 文本字数严格控制在 80 到 200 字之间。`;

        const response = await fetch(targetUrl, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Proxy-Auth": env.PROXY_KEY || "121218679"
          },
          body: JSON.stringify({
            contents: [
              {
                role: "user",
                parts: [{ text: prompt }]
              }
            ],
            generationConfig: {
              temperature: 0.8
            }
          })
        });

        if (!response.ok) {
          const errText = await response.text();
          return new Response(
            JSON.stringify({ error: `API 调用失败: ${response.status} - ${errText}` }),
            { status: 500, headers: { "Content-Type": "application/json" } }
          );
        }

        const resData = await response.json();
        const contentText = resData.candidates?.[0]?.content?.parts?.[0]?.text || "";
        return new Response(JSON.stringify({ text: contentText.trim() }), {
          headers: { "Content-Type": "application/json" }
        });
      } catch (err) {
        return new Response(
          JSON.stringify({ error: `服务器内部错误: ${err.message}` }),
          { status: 500, headers: { "Content-Type": "application/json" } }
        );
      }
    }

    // 3. POST /api/analyze: Call Gemini API
    if (request.method === "POST" && url.pathname === "/api/analyze") {
      try {
        const body = await request.json();
        const userText = body.text || "";
        const industry = body.industry || "General";
        const file = body.file || null;
        
        // Read GEMINI_API_KEY from environment variables / secrets
        const apiKey = env.GEMINI_API_KEY;
        if (!apiKey) {
          return new Response(
            JSON.stringify({ error: "服务器未配置 GEMINI_API_KEY 环境变量，请在 Cloudflare 后端绑定您的 API Key。" }), 
            { status: 400, headers: { "Content-Type": "application/json" } }
          );
        }

        const model = env.GEMINI_MODEL || "gemini-3.1-flash-lite";
        
        let prompt = `行业背景: ${industry}\n`;
        if (userText) {
          prompt += `业务描述或要求: "${userText}"\n`;
        }
        if (file) {
          prompt += `请结合上传的这一份名为 "${file.name}" 的数据资产进行多模态深度解构，并进行本体建模。抽取其中的抽象实体、物理实体、核心指标、规则和关联关系。`;
        }

        // Route requests through the Taiwan proxy gateway to bypass region restrictions
        const targetUrl = `https://ai-gateway-403802525344.asia-east1.run.app/gemini/v1beta/models/${model}:generateContent?key=${apiKey}`;

        const parts = [
          { text: prompt }
        ];

        if (file && file.data && file.type) {
          parts.push({
            inlineData: {
              mimeType: file.type,
              data: file.data
            }
          });
        }

        const response = await fetch(targetUrl, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Proxy-Auth": env.PROXY_KEY || "121218679"
          },
          body: JSON.stringify({
            contents: [
              {
                role: "user",
                parts: parts
              }
            ],
            systemInstruction: {
              parts: [
                { text: SYSTEM_PROMPT }
              ]
            },
            generationConfig: {
              responseMimeType: "application/json",
              temperature: 0.1
            }
          })
        });

        if (!response.ok) {
          const errText = await response.text();
          return new Response(
            JSON.stringify({ error: `Gemini API 调用失败: ${response.status} - ${errText}` }), 
            { status: 500, headers: { "Content-Type": "application/json" } }
          );
        }

        const resData = await response.json();
        const contentText = resData.candidates?.[0]?.content?.parts?.[0]?.text || "";

        try {
          // Gemini returns native json directly when responseMimeType is set
          const parsedResult = JSON.parse(contentText.trim());
          return new Response(JSON.stringify(parsedResult), {
            headers: { "Content-Type": "application/json" }
          });
        } catch (jsonErr) {
          return new Response(
            JSON.stringify({ 
              error: "Gemini 返回的内容解析为 JSON 失败，请重试。",
              raw_content: contentText
            }), 
            { status: 500, headers: { "Content-Type": "application/json" } }
          );
        }

      } catch (err) {
        return new Response(
          JSON.stringify({ error: `服务器内部错误: ${err.message}` }), 
          { status: 500, headers: { "Content-Type": "application/json" } }
        );
      }
    }

    return new Response("Not Found", { status: 404 });
  }
};
