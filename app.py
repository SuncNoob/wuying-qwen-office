#!/usr/bin/env python3
"""
千问办公 - Qwen Office Assistant
A minimal office assistant built with AgentScope and Qwen/DashScope.

Agents:
  - 办公秘书 (Office Secretary): Router agent that dispatches tasks
  - 日程助理 (Schedule Assistant): Manages calendar and appointments
  - 待办助理 (Todo Assistant): Manages tasks and to-do items
"""

import os
import sys
import argparse
from typing import Optional

# Lazy import to allow --help without API key
_agentscope_loaded = False


def load_agentscope():
    """Load agentscope lazily to allow --help without API key."""
    global _agentscope_loaded
    if _agentscope_loaded:
        return
    
    try:
        import agentscope
        _agentscope_loaded = True
    except ImportError as e:
        print(f"Error: agentscope not installed. Run: pip install agentscope", file=sys.stderr)
        sys.exit(1)


def check_api_key() -> bool:
    """Check if DASHSCOPE_API_KEY is set."""
    key = os.environ.get("DASHSCOPE_API_KEY")
    if not key:
        print("Warning: DASHSCOPE_API_KEY not set. Agents will not work.", file=sys.stderr)
        print("Copy .env.example to .env and fill in your key.", file=sys.stderr)
        return False
    return True


class OfficeSecretary:
    """办公秘书 - Routes user requests to appropriate specialist agents."""
    
    def __init__(self, name: str = "办公秘书"):
        self.name = name
        self.agents = {}
        
    def register_agent(self, role: str, agent):
        """Register a specialist agent."""
        self.agents[role] = agent
        
    def route(self, user_input: str) -> str:
        """Route user input to the appropriate agent."""
        user_input_lower = user_input.lower()
        
        # Simple keyword-based routing
        if any(kw in user_input for kw in ["日程", "会议", "安排", "日历", "appointment", "schedule"]):
            if "schedule" in self.agents:
                return self.agents["schedule"].handle(user_input)
        elif any(kw in user_input for kw in ["待办", "任务", "todo", "task", "清单"]):
            if "todo" in self.agents:
                return self.agents["todo"].handle(user_input)
        
        # Default: secretary handles it
        return f"我是{self.name}，收到您的请求：「{user_input}」。请问您需要日程安排还是待办管理？"


class ScheduleAssistant:
    """日程助理 - Manages calendar and appointments."""
    
    def __init__(self, name: str = "日程助理"):
        self.name = name
        
    def handle(self, user_input: str) -> str:
        """Handle schedule-related requests."""
        return f"[{self.name}] 收到日程请求：「{user_input}」。我可以帮您查看、创建或修改日程安排。"


class TodoAssistant:
    """待办助理 - Manages tasks and to-do items."""
    
    def __init__(self, name: str = "待办助理"):
        self.name = name
        
    def handle(self, user_input: str) -> str:
        """Handle todo-related requests."""
        return f"[{self.name}] 收到待办请求：「{user_input}」。我可以帮您添加、完成或查看待办事项。"


def create_agents_with_agentscope():
    """Create agents using AgentScope framework (requires API key)."""
    load_agentscope()
    
    if not check_api_key():
        print("\nRunning in demo mode (no API key). Agents will use mock responses.")
        print("To enable real Qwen responses, set DASHSCOPE_API_KEY environment variable.\n")
        return create_demo_agents()
    
    # Import AgentScope 2.x components
    from agentscope.agent import Agent
    from agentscope.model import DashScopeChatModel
    from agentscope.credential import DashScopeCredential
    from agentscope.message import Msg
    
    # Initialize DashScope credential and model
    credential = DashScopeCredential(api_key=os.environ.get("DASHSCOPE_API_KEY"))
    model = DashScopeChatModel(
        credential=credential,
        model="qwen-turbo",
    )
    
    # Create agents using AgentScope 2.x
    class SecretaryAgent(Agent):
        def __init__(self, name: str, model):
            super().__init__(name=name)
            self.model = model
            self.sys_prompt = "你是办公秘书，负责理解用户需求并路由到合适的助理。"
            
        def reply(self, x: Msg = None) -> Msg:
            prompt = f"{self.sys_prompt}\n\n用户说：{x.content if x else ''}"
            response = self.model(prompt)
            return Msg(name=self.name, content=response.text, role="assistant")
    
    class ScheduleAgent(Agent):
        def __init__(self, name: str, model):
            super().__init__(name=name)
            self.model = model
            self.sys_prompt = "你是日程助理，专门处理日程、会议、日历相关的请求。"
            
        def reply(self, x: Msg = None) -> Msg:
            prompt = f"{self.sys_prompt}\n\n用户说：{x.content if x else ''}"
            response = self.model(prompt)
            return Msg(name=self.name, content=response.text, role="assistant")
    
    class TodoAgent(Agent):
        def __init__(self, name: str, model):
            super().__init__(name=name)
            self.model = model
            self.sys_prompt = "你是待办助理，专门处理任务、待办事项、清单相关的请求。"
            
        def reply(self, x: Msg = None) -> Msg:
            prompt = f"{self.sys_prompt}\n\n用户说：{x.content if x else ''}"
            response = self.model(prompt)
            return Msg(name=self.name, content=response.text, role="assistant")
    
    # Instantiate agents
    secretary = SecretaryAgent("办公秘书", model)
    schedule = ScheduleAgent("日程助理", model)
    todo = TodoAgent("待办助理", model)
    
    return secretary, schedule, todo


def create_demo_agents():
    """Create demo agents without API key (mock responses)."""
    secretary = OfficeSecretary()
    schedule = ScheduleAssistant()
    todo = TodoAssistant()
    secretary.register_agent("schedule", schedule)
    secretary.register_agent("todo", todo)
    return secretary, schedule, todo


def interactive_mode():
    """Run interactive console mode."""
    print("=" * 60)
    print("千问办公 - Qwen Office Assistant")
    print("=" * 60)
    print()
    
    secretary, schedule, todo = create_agents_with_agentscope()
    
    print("Agent 已就绪：")
    print(f"  - {secretary.name} (路由)")
    print(f"  - {schedule.name}")
    print(f"  - {todo.name}")
    print()
    print("输入 'quit' 或 'exit' 退出")
    print("-" * 60)
    print()
    
    while True:
        try:
            user_input = input("您: ").strip()
            
            if not user_input:
                continue
            
            if user_input.lower() in ["quit", "exit", "q"]:
                print("再见！")
                break
            
            # Route through secretary
            response = secretary.route(user_input)
            print(f"\n{response}\n")
            
        except KeyboardInterrupt:
            print("\n\n再见！")
            break
        except EOFError:
            break


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="千问办公 - Qwen Office Assistant (AgentScope)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python app.py                    # 交互模式
  python app.py --demo             # 演示模式（无需 API Key）
  
环境变量:
  DASHSCOPE_API_KEY    通义千问 API Key（从 https://dashscope.console.aliyun.com/ 获取）
  
Agent 说明:
  办公秘书    路由用户请求到合适的助理
  日程助理    处理日程、会议、日历相关请求
  待办助理    处理任务、待办事项、清单相关请求
        """
    )
    
    parser.add_argument(
        "--demo",
        action="store_true",
        help="运行演示模式（无需 API Key，使用模拟响应）"
    )
    
    args = parser.parse_args()
    
    if args.demo:
        print("运行演示模式...")
        secretary, schedule, todo = create_demo_agents()
        print(f"\n{secretary.route('我想安排明天的会议')}")
        print(f"{secretary.route('帮我添加一个待办事项')}")
        return
    
    interactive_mode()


if __name__ == "__main__":
    main()
