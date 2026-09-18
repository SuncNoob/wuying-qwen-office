# 千问办公 — Agentic Coding Agent

**千问办公** 是一个 AI 编程 Agent，对标 [Qoder](https://qoder.ai)、[Claude Code](https://claude.ai)、[OpenAI Codex](https://openai.com/codex)。

用户用自然语言描述开发任务，Agent 在本地 workspace 里 **读代码、改代码、跑命令、交付可审查的结果**。

> ⚠️ 这不是办公秘书、不是日历、不是待办清单。这是一个 **Agentic Coding Agent**。

## 核心能力

| 工具 | 功能 |
|------|------|
| `read_file` | 读取文件内容 |
| `write_file` | 创建/覆写文件 |
| `edit_file` | 精准编辑文件片段 |
| `glob` | 按模式搜索文件 |
| `grep` | 在文件中搜索文本 |
| `run_shell` | 执行 shell 命令 |

Agent 通过 **ReAct（Reasoning + Acting）** 循环自主完成编码任务：分析 → 选工具 → 执行 → 验证 → 交付。

## 技术栈

- **[AgentScope 2.x](https://github.com/agentscope-ai/agentscope)** — Agent 框架（`Agent` + `Toolkit` + `ReActConfig`）
- **通义千问 (Qwen)** — 通过 DashScope API 调用（`DashScopeChatModel`）
- **Python 3.11+** — 运行环境

## 安装

```bash
# 1. 克隆仓库
git clone <repository-url>
cd wuying-qwen-office

# 2. 创建虚拟环境（推荐）
python3 -m venv .venv
source .venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置 API Key（可选，--demo 不需要）
cp .env.example .env
# 编辑 .env，填入你的 DashScope API Key
```

API Key 从 [阿里云 DashScope 控制台](https://dashscope.console.aliyun.com/) 获取。

## 使用

### Demo 模式（无需 API Key）

模拟一次完整的编码闭环：读文件 → 改代码 → 跑检查 → 打印工具调用轨迹。

```bash
python app.py --demo
```

输出示例：

```
📁 Workspace: /path/to/workspace
📄 Created sample file: /path/to/workspace/hello.py

================================================================
  🔧  Tool-Call Trajectory
================================================================

  [1] read_file  (0 ms)
      path: /path/to/workspace/hello.py
      → def greet(name):
            return "Hello, " + name
        ...

  [2] edit_file  (1 ms)
      path: /path/to/workspace/hello.py
      → File updated successfully.

  [3] run_shell  (42 ms)
      command: python3 -m py_compile hello.py
      → Syntax OK

  [4] run_shell  (15 ms)
      command: python3 hello.py
      → Hello, World!

----------------------------------------------------------------
  💬  Final Answer
----------------------------------------------------------------
  Done. I added type hints and a docstring to `greet()`,
  verified syntax, and ran the script — output: "Hello, World!"
================================================================
```

### CLI 一次性任务

```bash
# 指定 workspace 和任务
python app.py --workspace ./my-project "给 utils.py 添加单元测试"

# 使用当前目录
python app.py "修复 main.py 中的 import 错误"
```

### 交互式 REPL

```bash
python app.py
```

进入交互界面后，输入编码任务，Agent 会自主完成：

```
================================================================
  千问办公 — Agentic Coding Agent
================================================================
  Workspace : /path/to/workspace
  Model     : qwen-plus

  Type a coding task and press Enter.
  Commands:  /quit  /exit  /tools  /workspace
----------------------------------------------------------------

🧑‍💻 You > 给这个 Flask 应用添加健康检查端点
  🔧 [1] read_file
      → ...
  🔧 [2] edit_file
      → File updated successfully.
  🔧 [3] run_shell
      → Syntax OK

  🤖 Agent > 已添加 /health 端点，返回 {"status": "ok"}。语法检查通过。
```

### Web UI（Quest 风格）

```bash
python app.py --web --port 8080
```

浏览器打开 `http://localhost:8080`，在对话窗口中输入编码任务，实时查看工具调用步骤和文件变更。

### 查看帮助

```bash
python app.py --help
```

## 环境变量

| 变量 | 说明 | 必需 |
|------|------|------|
| `DASHSCOPE_API_KEY` | 通义千问 DashScope API Key | 是（`--demo` 除外） |
| `QWEN_MODEL` | 模型名称（默认 `qwen-plus`） | 否 |

## 项目结构

```
.
├── app.py              # 主入口（CLI + Agent + Web UI）
├── requirements.txt    # Python 依赖
├── .env.example        # 环境变量模板
├── README.md           # 本文件
└── .gitignore          # Git 忽略规则
```

## 架构

```
用户输入（自然语言）
    │
    ▼
┌──────────────────────────────────┐
│  AgentScope 2.x Agent           │
│  ├─ system_prompt (编码 Agent)   │
│  ├─ DashScopeChatModel (Qwen)   │
│  ├─ Toolkit                      │
│  │   ├─ Read                     │
│  │   ├─ Write                    │
│  │   ├─ Edit                     │
│  │   ├─ Bash (run_shell)         │
│  │   ├─ Glob                     │
│  │   └─ Grep                     │
│  └─ ReActConfig (max_iters=20)  │
└──────────────────────────────────┘
    │
    ▼
工具调用 → 文件读写 / Shell 执行 → 结果返回 Agent
    │
    ▼
Agent 继续推理 → 更多工具调用 / 最终回答
```

## 无 Key 导入

应用设计为在没有 API Key 的情况下也能正常运行 `--help` 和 `--demo`：

```bash
python app.py --help    # ✅ 正常
python app.py --demo    # ✅ 正常（不调用 DashScope）
python -c "import app"  # ✅ 不崩溃
```

## 与 Qoder / Claude Code / Codex 的对比

| 特性 | 千问办公 | Qoder | Claude Code | Codex |
|------|---------|-------|-------------|-------|
| AI 编程 Agent | ✅ | ✅ | ✅ | ✅ |
| 自然语言任务 | ✅ | ✅ | ✅ | ✅ |
| 本地 workspace | ✅ | ✅ | ✅ | ✅ |
| 读/写/编辑代码 | ✅ | ✅ | ✅ | ✅ |
| 执行命令 | ✅ | ✅ | ✅ | ✅ |
| 工具调用轨迹 | ✅ | ✅ | ✅ | ✅ |
| Web UI | ✅ | ✅ | ❌ | ❌ |
| 开源 | ✅ | ❌ | ❌ | ❌ |
| 通义千问 | ✅ | ❌ | ❌ | ❌ |

## License

MIT
