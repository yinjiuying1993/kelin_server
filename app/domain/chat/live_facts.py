"""Detect weather/news turns and shape MCP facts for the instruction pack."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

LiveKind = Literal["weather", "news"]

_WEATHER = re.compile(r"(天气|气温|下雨|下雪|预报|降温|升温)")
_NEWS = re.compile(r"(新闻|头条|热点|资讯)")
_CITY = re.compile(
    r"(?:([\u4e00-\u9fff]{2,8}?)(?:市|地区)?)(?:的)?(?:今天|明天|后天)?(?:天气|气温|预报|下雨|下雪)"
    r"|(?:今天|明天|后天)([\u4e00-\u9fff]{2,8}?)(?:市)?(?:天气|气温|预报)"
    r"|([\u4e00-\u9fff]{2,8}?)(?:市)?(?:天气|气温|预报)"
)
_NOT_CITY = frozenset(
    {
        "今天",
        "明天",
        "后天",
        "昨天",
        "什么",
        "怎么",
        "怎样",
        "如何",
        "多少",
        "哪里",
        "哪儿",
        "外面",
        "本地",
        "那里",
        "你好",
        "在吗",
        "谢谢",
        "好的",
        "不是",
        "可以",
        "知道",
        "没有",
    }
)
_URL = re.compile(r"https://[^\s\"'<>]+")


@dataclass(frozen=True, slots=True)
class LiveFact:
    kind: LiveKind
    text: str = ""
    city: str | None = None
    need_city: bool = False
    urls: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return bool(self.text.strip()) and not self.need_city


def classify_live_need(user_text: str) -> LiveKind | None:
    text = user_text.strip()
    if not text:
        return None
    if _WEATHER.search(text) is not None:
        return "weather"
    if _NEWS.search(text) is not None:
        return "news"
    return None


def extract_weather_city(user_text: str) -> str | None:
    match = _CITY.search(user_text.strip())
    if match is None:
        return None
    city = next((part for part in match.groups() if part), "")
    city = city.strip()
    if not city or city in _NOT_CITY:
        return None
    return city


def urls_from_text(text: str) -> tuple[str, ...]:
    seen: list[str] = []
    for match in _URL.finditer(text):
        url = match.group(0).rstrip(").,，。；;")
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            continue
        if url in seen:
            continue
        seen.append(url)
    return tuple(seen)


def _news_reply_rule(pack: str) -> str:
    if any(token in pack for token in ("吃瓜", "大瓜", "娱乐", "明星", "八卦", "微博", "抖音", "偶像")):
        kind = "只选娱乐、明星、微博或抖音上的瓜，不要混进时事。"
    elif any(token in pack for token in ("时事", "新闻", "热点", "要闻", "时政")):
        kind = "只选时事、社会或国际要闻，不要混进娱乐八卦。"
    else:
        kind = ""
    return (
        "news_reply: 事实已按热度从高到低排列。"
        f"{kind}只选最热的10条。"
        "每条写成「序号. **标题** — 一句话。[详情](链接)」。不要超过10条，不要说只拿到部分。"
    )


def _headline_list(text: str) -> bool:
    rows = [line.strip() for line in text.splitlines() if line.strip()]
    return len(rows) >= 2 and any(line.startswith("http") or " http" in line for line in rows)


def public_reply_text(text: str) -> str | None:
    """Readable chat text. A whole JSON document is not something the client can show."""
    stripped = text.strip()
    if not stripped:
        return None
    body = _without_json_fence(stripped)
    if not body or body[0] not in '{["':
        return stripped
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return stripped
    if isinstance(parsed, str):
        inner = parsed.strip()
        if not inner or _json_container(inner):
            return None
        return inner
    return None


def _without_json_fence(text: str) -> str:
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if len(lines) < 2 or lines[-1].strip() != "```":
        return text
    return "\n".join(lines[1:-1]).strip()


def _json_container(text: str) -> bool:
    body = _without_json_fence(text.strip())
    if not body or body[0] not in "{[":
        return False
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, (dict, list))


def assemble_tool_reply(text: str, *, limit: int = 10) -> str:
    """Turn Bailian tool text into the user-visible reply. No model rewrite."""
    stripped = text.strip()
    if not stripped:
        return ""
    weather = _weather_reply(stripped)
    if weather is not None:
        return weather
    prose = public_reply_text(stripped)
    if prose is None:
        return ""
    rows = [line.strip() for line in prose.splitlines() if line.strip()]
    if not any(_URL.search(line) for line in rows):
        return prose
    lines: list[str] = []
    for cleaned in rows:
        match = _URL.search(cleaned)
        if match is None:
            continue
        url = match.group(0).rstrip(").,，。；;")
        title = cleaned[: match.start()].strip() or cleaned[match.end() :].strip() or url
        lines.append(f"{len(lines) + 1}. **{title}** [详情]({url})")
        if len(lines) >= limit:
            break
    return "\n".join(lines) if lines else prose


def _weather_reply(text: str) -> str | None:
    if not text.startswith("{"):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    forecasts = payload.get("forecasts")
    if not isinstance(forecasts, list) or not forecasts:
        return None
    city = payload.get("city")
    city_name = city.strip() if isinstance(city, str) else ""
    labels = ("今天", "明天", "后天")
    sentences: list[str] = []
    for index, day in enumerate(forecasts[:3]):
        if not isinstance(day, dict):
            continue
        sentence = _weather_day(
            city_name if index == 0 else "",
            labels[index],
            day,
            with_wind=index == 0,
        )
        if sentence:
            sentences.append(sentence)
    if not sentences:
        return None
    return "".join(sentences)


def _weather_day(city: str, when: str, day: dict[str, object], *, with_wind: bool) -> str:
    day_sky = _text(day.get("dayweather"))
    night_sky = _text(day.get("nightweather"))
    if day_sky and night_sky and day_sky != night_sky:
        sky = f"白天{day_sky}，夜间{night_sky}"
    else:
        sky = day_sky or night_sky
    if not sky:
        return ""
    sentence = f"{city}{when}{sky}"
    low = _text(day.get("nighttemp"))
    high = _text(day.get("daytemp"))
    if low and high:
        sentence += f"，{low}到{high}℃"
    elif high or low:
        sentence += f"，{high or low}℃"
    if with_wind:
        wind = _text(day.get("daywind"))
        power = _text(day.get("daypower"))
        if wind and power:
            sentence += f"，{wind}风{power}级"
        elif wind:
            sentence += f"，{wind}风"
    return sentence + "。"


def _text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def apply_tool_fact(pack: str, text: str) -> str:
    """Append Bailian Auto MCP text for the spirit model."""
    lines = [line for line in pack.splitlines() if not line.startswith("evidence:")]
    lines.append("evidence: 工具")
    if _headline_list(text):
        lines.append(_news_reply_rule(pack))
    lines.append("facts:")
    for chunk in text.splitlines():
        cleaned = chunk.strip()
        if cleaned:
            lines.append(f"- {cleaned}")
    return "\n".join(lines)


def apply_live_fact(pack: str, fact: LiveFact) -> str:
    """Rewrite evidence and append facts for a weather/news turn."""
    lines = pack.splitlines()
    label = "天气" if fact.kind == "weather" else "新闻"
    out: list[str] = []
    replaced = False
    for line in lines:
        if line.startswith("evidence:"):
            out.append(f"evidence: {label if fact.ready else '无'}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"evidence: {label if fact.ready else '无'}")
    if fact.kind == "weather":
        out.append(f"weather_city: {fact.city or '无'}")
    if fact.ready:
        out.append("facts:")
        for chunk in fact.text.splitlines():
            cleaned = chunk.strip()
            if cleaned:
                out.append(f"- {cleaned}")
    return "\n".join(out)
