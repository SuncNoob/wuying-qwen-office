# 千问办公 - Qwen Office Assistant

基于 [AgentScope](https://github.com/agentscope-ai/agentscope) 和通义千问（DashScope）构建的最小可用办公助手应用。

## 功能概述

这是一个多 Agent 协作的办公助手系统，包含三个核心 Agent：

- **办公秘书**：路由 Agent，负责理解用户需求并分发到合适的 specialist Agent
- **日程助理**：处理日程、会议、日历相关的请求
- **待办助理**：处理任务、待办事项、清单相关的请求

## 安装

### 1. 克隆仓库

```bash
git clone <repository-url>
cd wuying-qwen-office
```

### 2. 创建虚拟环境（推荐）

```bash
python3 -m venv .venv
source .venv/bin/activate  # Linux/macOS
# 或 .venv\Scripts\activate  # Windows
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 配置 API Key

复制 `.env.example` 到 `.env` 并填入你的 DashScope API Key：

```bash
cp .env.example .env
```

编辑 `.env` 文件，填入你的 API Key：

```
DASHSCOPE_API_KEY=your_api_key_here
```

API Key 可以从 [阿里云 DashScope 控制台](https://dashscope.console.aliyun.com/) 获取。

**注意**：`.env` 文件已被 `.gitignore` 忽略，不会被提交到仓库。

## 运行

### 交互模式（需要 API Key）

```bash
python app.py
```

进入交互界面后，输入你的请求，系统会自动路由到合适的 Agent：

```
您: 我想安排明天下午的会议
[日程助理] 收到日程请求：「我想安排明天下午的会议」。...

您: 帮我添加一个待办事项：完成项目报告
[待办助理] 收到待办请求：「帮我添加一个待办事项：完成项目报告」。...
```

输入 `quit`、`exit` 或 `q` 退出。

### 演示模式（无需 API Key）

```bash
python app.py --demo
```

演示模式使用模拟响应，适合快速体验和理解系统工作方式。

### 查看帮助

```bash
python app.py --help
```

## 技术栈

- **AgentScope 2.x**：多 Agent 框架
- **通义千问 (Qwen)**：通过 DashScope API 调用
- **Python 3.11+**：运行环境

## 项目结构

```
.
├── app.py              # 主入口文件
├── requirements.txt    # Python 依赖
├── .env.example        # 环境变量模板
├── README.md           # 本文件
└── .gitignore          # Git 忽略规则
```

## Agent 说明

### 办公秘书 (Office Secretary)

- **职责**：作为路由 Agent，分析用户输入并分发到合适的 specialist Agent
- **路由规则**：基于关键词匹配（日程/会议/安排 → 日程助理，待办/任务/清单 → 待办助理）
- **实现**：继承 `agentscope.agent.Agent`

### 日程助理 (Schedule Assistant)

- **职责**：处理日程、会议、日历相关的请求
- **关键词**：日程、会议、安排、日历、appointment、schedule
- **实现**：继承 `agentscope.agent.Agent`

### 待办助理 (Todo Assistant)

- **职责**：处理任务、待办事项、清单相关的请求
- **关键词**：待办、任务、todo、task、清单
- **实现**：继承 `agentscope.agent.Agent`

## 环境变量

| 变量名 | 说明 | 必需 |
|--------|------|------|
| `DASHSCOPE_API_KEY` | 通义千问 DashScope API Key | 是（交互模式） |

## 开发

### 无 Key 导入

应用设计为可以在没有 API Key 的情况下导入和运行 `--help`：

```bash
python -c "import app"  # 不会崩溃
python app.py --help    # 正常工作
```

### 扩展 Agent

要添加新的 Agent：

1. 创建新的 Agent 类，继承 `agentscope.agent.Agent`
2. 实现 `reply` 方法
3. 在 `OfficeSecretary.route` 中添加路由规则
4. 在 `create_agents_with_agentscope` 中实例化

## 许可证

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！
