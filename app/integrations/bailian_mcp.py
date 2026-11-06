"""Streamable HTTP client for Bailian Auto MCP. Vendor URLs stay in bailian.py."""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.providers.errors import ProviderError

_PROTOCOL = "2024-11-05"
_ACCEPT = "application/json, text/event-stream"


async def run_auto_mcp(
    client: httpx.AsyncClient,
    *,
    url: str,
    api_key: str,
    query: str,
    argument: str | None,
    default_city: str | None,
    timeout: float,
    user_text: str | None = None,
) -> str:
    """Search Bailian tools, then call the first one that actually returns text."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": _ACCEPT,
    }
    try:
        init = await client.post(
            url,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": _PROTOCOL,
                    "capabilities": {},
                    "clientInfo": {"name": "kelin-server", "version": "0.1.0"},
                },
            },
            timeout=timeout,
        )
    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        raise ProviderError("MODEL_UNAVAILABLE") from exc
    if init.status_code >= 400:
        raise ProviderError("MODEL_UNAVAILABLE")
    session_id = init.headers.get("mcp-session-id") or init.headers.get("Mcp-Session-Id")
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    try:
        await client.post(
            url,
            headers=headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            timeout=timeout,
        )
        focus = "\n".join(part for part in (query, argument or "", user_text or "") if part.strip())
        channel = _news_channel(focus)
        found = await client.post(
            url,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "search_tool",
                    "arguments": {"query": _news_search_query(query, channel), "topN": 8},
                },
            },
            timeout=timeout,
        )
    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        raise ProviderError("MODEL_UNAVAILABLE") from exc
    if found.status_code >= 400:
        raise ProviderError("MODEL_UNAVAILABLE")
    candidates = _candidate_tools(_tool_text(_json_rpc_result(found)), focus)
    missing = False
    last_error: ProviderError | None = None
    for index, (server_code, tool_name, schema) in enumerate(candidates, start=3):
        try:
            params = _call_params(schema, argument, default_city, channel)
        except ProviderError as exc:
            if exc.code != "TOOL_ARGUMENT_MISSING":
                raise
            missing = True
            continue
        try:
            called = await client.post(
                url,
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": index,
                    "method": "tools/call",
                    "params": {
                        "name": "call_tool",
                        "arguments": {
                            "server_code": server_code,
                            "tool_name": tool_name,
                            "params": params,
                        },
                    },
                },
                timeout=timeout,
            )
        except (httpx.TimeoutException, httpx.ConnectError):
            last_error = ProviderError("MODEL_UNAVAILABLE")
            continue
        if called.status_code >= 400:
            last_error = ProviderError("MODEL_UNAVAILABLE")
            continue
        try:
            text = _compact_tool_text(_tool_text(_json_rpc_result(called)))
        except ProviderError as exc:
            last_error = exc
            continue
        if _news_miss(text):
            last_error = ProviderError("MODEL_UNAVAILABLE")
            continue
        if text.strip():
            return text
    if missing and last_error is None:
        raise ProviderError("TOOL_ARGUMENT_MISSING")
    raise last_error or ProviderError("MODEL_UNAVAILABLE")


async def call_mcp_tool(
    client: httpx.AsyncClient,
    *,
    url: str,
    api_key: str,
    tool: str,
    arguments: dict[str, Any],
    timeout: float,
) -> str:
    """Initialize a short MCP session, call one tool, return concatenated text."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": _ACCEPT,
    }
    try:
        init = await client.post(
            url,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": _PROTOCOL,
                    "capabilities": {},
                    "clientInfo": {"name": "kelin-server", "version": "0.1.0"},
                },
            },
            timeout=timeout,
        )
    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        raise ProviderError("MODEL_UNAVAILABLE") from exc
    if init.status_code >= 400:
        raise ProviderError("MODEL_UNAVAILABLE")
    session_id = init.headers.get("mcp-session-id") or init.headers.get("Mcp-Session-Id")
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    try:
        await client.post(
            url,
            headers=headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            timeout=timeout,
        )
        call = await client.post(
            url,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            },
            timeout=timeout,
        )
    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        raise ProviderError("MODEL_UNAVAILABLE") from exc
    if call.status_code >= 400:
        raise ProviderError("MODEL_UNAVAILABLE")
    payload = _json_rpc_result(call)
    if not isinstance(payload, dict):
        raise ProviderError("MODEL_UNAVAILABLE")
    if payload.get("isError"):
        raise ProviderError("MODEL_UNAVAILABLE")
    text = _content_text(payload.get("content"))
    if not text:
        raise ProviderError("MODEL_UNAVAILABLE")
    return text


def _compact_tool_text(text: str) -> str:
    """Turn a news blob into headline lines. Other tool text is kept whole."""
    stripped = text.strip()
    if not stripped.startswith("{"):
        return stripped
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped
    if not isinstance(payload, dict):
        return stripped
    lines = _headline_lines(payload, "hot_news") or _headline_lines(payload, "news")
    if lines:
        return "\n".join(lines)
    return stripped


def _headline_lines(payload: dict[str, Any], kind: str) -> list[str]:
    title_suffix = f"_{kind}_title"
    lines: list[str] = []
    indexes = sorted(
        int(key.removeprefix("num_")[: -len(title_suffix)])
        for key in payload
        if key.startswith("num_")
        and key.endswith(title_suffix)
        and key.removeprefix("num_")[: -len(title_suffix)].isdigit()
    )
    for index in indexes:
        title = payload.get(f"num_{index}{title_suffix}")
        if not isinstance(title, str) or not title.strip():
            continue
        line = title.strip()
        url = payload.get(f"num_{index}_{kind}_url")
        if isinstance(url, str) and url.startswith("http"):
            line = f"{line} {url}"
        lines.append(line)
    return lines


_GOSSIP_TOKENS = ("吃瓜", "大瓜", "娱乐", "明星", "八卦", "微博", "抖音", "偶像")
_AFFAIRS_TOKENS = ("时事", "新闻", "热点", "要闻", "时政")


def _news_search_query(query: str, channel: str | None) -> str:
    """Ask for the tool that can filter this channel. A generic hot-list query hides it."""
    if channel == "娱乐最新":
        return "娱乐新闻"
    if channel == "国内最新":
        return "时事热点新闻"
    return query[:200]


def _news_channel(text: str) -> str | None:
    """Pick a news channel from the user's words. Gossip wins when both appear."""
    if any(token in text for token in _GOSSIP_TOKENS):
        return "娱乐最新"
    if any(token in text for token in _AFFAIRS_TOKENS):
        return "国内最新"
    return None


def _news_miss(text: str) -> bool:
    return "没有查询到相关新闻" in text


def _tool_text(payload: object) -> str:
    if not isinstance(payload, dict):
        raise ProviderError("MODEL_UNAVAILABLE")
    if payload.get("isError"):
        raise ProviderError("MODEL_UNAVAILABLE")
    text = _content_text(payload.get("content"))
    if not text:
        raise ProviderError("MODEL_UNAVAILABLE")
    return text


def _candidate_tools(text: str, query: str) -> list[tuple[str, str, dict[str, Any]]]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderError("MODEL_UNAVAILABLE") from exc
    rows = payload.get("search_data") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ProviderError("MODEL_UNAVAILABLE")
    picked: list[tuple[str, str, dict[str, Any]]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        server_code = row.get("server_code")
        tool_name = row.get("tool_name")
        schema = row.get("tool_inputSchema")
        if not isinstance(server_code, str) or not isinstance(tool_name, str):
            continue
        if not isinstance(schema, dict):
            schema = {}
        picked.append((server_code, tool_name, schema))
    if not picked:
        raise ProviderError("MODEL_UNAVAILABLE")
    channel = _news_channel(query)
    if channel is None and not any(token in query for token in ("新闻", "热点", "热搜")):
        return picked

    def rank(item: tuple[str, str, dict[str, Any]]) -> int:
        name = item[1]
        if channel is not None and name == "news.query":
            return 0
        if channel is None and name == "news.hotnews":
            return 0
        if "hot" in name.lower():
            return 1
        return 2

    return sorted(picked, key=rank)


def _call_params(
    schema: dict[str, Any],
    argument: str | None,
    default_city: str | None,
    channel: str | None = None,
) -> dict[str, str]:
    required = schema.get("required")
    props = schema.get("properties")
    names = [name for name in required if isinstance(name, str)] if isinstance(required, list) else []
    if not names:
        if channel and isinstance(props, dict) and "channel_name" in props:
            return {"channel_name": channel}
        return {}
    name = names[0]
    value = (argument or "").strip()
    if not value and name == "city" and default_city:
        value = default_city.strip()
    if not value:
        raise ProviderError("TOOL_ARGUMENT_MISSING")
    if isinstance(props, dict):
        spec = props.get(name)
        if isinstance(spec, dict) and spec.get("type") not in {None, "string"}:
            raise ProviderError("MODEL_UNAVAILABLE")
    return {name: value[:200]}


def _json_rpc_result(response: httpx.Response) -> Any:
    content_type = response.headers.get("content-type", "")
    body = response.text
    if "text/event-stream" in content_type or body.lstrip().startswith("event:"):
        data = _sse_last_data(body)
    else:
        data = body
    try:
        message = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ProviderError("MODEL_UNAVAILABLE") from exc
    if not isinstance(message, dict):
        raise ProviderError("MODEL_UNAVAILABLE")
    if message.get("error") is not None:
        raise ProviderError("MODEL_UNAVAILABLE")
    return message.get("result")


def _sse_last_data(body: str) -> str:
    last = ""
    for line in body.splitlines():
        if line.startswith("data:"):
            last = line[5:].strip()
    if not last:
        raise ProviderError("MODEL_UNAVAILABLE")
    return last


def _content_text(content: object) -> str:
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    return "\n".join(parts)
