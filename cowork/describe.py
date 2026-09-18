"""Natural-language task briefs for the laptop monitor (coding, research, crawler)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from cowork.protocol import Task

BRAND_ZH = {
    "lienu": "LIENU jewelry（东京・藏前）",
    "hasuna": "HASUNA（伦理珠宝 / 天然石・珍珠）",
    "les-bon-bon": "les bon bon（彩宝・珍珠）",
    "artida-oud": "ARTIDA OUD（SV925＋天然石）",
    "synchronicity": "synchronicity（作家银饰与稀有石）",
    "siri-siri": "SIRI SIRI（东京・玻璃与七宝）",
    "lucine": "LUCINE（神户工房・银与天然石・珍珠）",
    "mariha": "MARIHA（华奢日常银饰）",
}

TYPE_ZH = {
    "plan": "拆任务",
    "implement": "写实现",
    "review": "做审查",
    "enqueue": "入队种子页",
    "fetch": "抓取",
    "extract": "汇总抽取",
}


def _as_dict(task: Task | dict[str, Any]) -> dict[str, Any]:
    if isinstance(task, Task):
        return task.to_dict()
    return dict(task or {})


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").replace("www.", "")


def describe_task(task: Task | dict[str, Any]) -> str:
    """One-sentence operator brief: what this task actually does."""
    data = _as_dict(task)
    kind = str(data.get("type") or "")
    title = str(data.get("title") or data.get("id") or "")
    body = str(data.get("body") or "").strip()
    brand = str(data.get("brand") or "")
    brand_name = BRAND_ZH.get(brand, brand)
    urls = [u for u in (data.get("seed_urls") or []) if u]
    path = str(data.get("path") or "")
    content = str(data.get("content") or "").strip()
    mode = str(data.get("mode") or "")
    limit = int(data.get("max_images") or 0)
    depends = [d for d in (data.get("depends_on") or []) if d]
    scenario = str(data.get("scenario") or "")

    if kind == "enqueue":
        shops = "、".join(_host(u) or u for u in urls) or "给定官网"
        extra = f" {body}" if body else ""
        return f"把 {len(urls) or 5} 个官方站点写入抓取队列（{shops}），只允许白名单域名。{extra}".strip()

    if kind == "fetch":
        start = urls[0] if urls else "官方列表页"
        target = brand_name or _host(start) or title
        if mode == "browser":
            n = limit or 16
            return (
                f"用本机 Codex 打开「{target}」官网，从列表点进商品详情子页，"
                f"每件商品保存产品主图到 pics/{brand or 'brand'}/，目标 {n} 张。"
                f" 起点：{start}"
            )
        return f"抓取 {target} 页面原文：{start}"

    if kind == "extract":
        deps = "、".join(depends) if depends else "已完成的 fetch"
        return f"把 {deps} 抓到的产品图做成目录 pics/CATALOG.md，并写出 structured.jsonl。"

    if kind == "plan":
        extra = body or title
        where = f" 产出路径 {path}。" if path else ""
        if scenario == "research" or mode == "research":
            return f"把数学研究拆成可执行步骤：{extra}。{where}".strip()
        if mode == "codex":
            return f"把应用需求拆成可实现的步骤（builder 用本机 Codex）：{extra}。{where}".strip()
        return f"把需求拆成可实现的步骤：{extra}。{where}".strip()

    if kind == "implement":
        if scenario == "research" or mode == "research":
            where = path or "workspace 研究笔记"
            return f"用本机 Codex 尝试推进证明并写入 {where}（必须含命题、已知结果、尝试、缺口）。"
        if mode == "codex":
            where = path or "应用入口"
            return f"用本机 Codex 按 AgentScope 实现应用并写入 {where}。"
        snippet = (content[:80] + "…") if len(content) > 80 else content
        where = path or "工作区文件"
        extra = f" 内容：{snippet}" if snippet else (f" {body}" if body else "")
        return f"按方案写入 {where}。{extra}".strip()

    if kind == "review":
        deps = "、".join(depends) if depends else "上游实现"
        if scenario == "research" or mode == "research":
            return f"验收 {deps} 的研究笔记：四段结构是否齐全，有没有把未解决问题写成已证。"
        if mode == "codex":
            return f"验收 {deps} 是否交出可运行的 AgentScope 应用（必要文件与 agentscope 引用）。"
        return f"验收 {deps} 的产出是否通过（文件是否存在、实现结果是否完整）。"

    bits = [TYPE_ZH.get(kind, kind or "任务"), title]
    if scenario:
        bits.append(f"场景 {scenario}")
    if body:
        bits.append(body)
    return "：".join(x for x in bits if x)
