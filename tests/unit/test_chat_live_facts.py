"""Weather/news instruction facts for Bailian MCP turns."""

from __future__ import annotations

from app.domain.chat.live_facts import (
    LiveFact,
    apply_live_fact,
    apply_tool_fact,
    assemble_tool_reply,
    classify_live_need,
    extract_weather_city,
    public_reply_text,
    urls_from_text,
)


def test_classify_weather_and_news() -> None:
    from app.domain.chat.mode import classify_user_text

    assert classify_live_need("杭州今天天气怎么样") == "weather"
    assert classify_live_need("最近有什么新闻") == "news"
    assert classify_live_need("今天几号") is None
    assert classify_user_text("今天天气怎么样") == "chat"
    assert classify_user_text("最近有什么新闻") == "chat"


def test_extract_weather_city() -> None:
    assert extract_weather_city("杭州天气") == "杭州"
    assert extract_weather_city("北京今天天气怎么样") == "北京"
    assert extract_weather_city("今天天气怎么样") is None


def test_apply_live_fact_rewrites_evidence() -> None:
    pack = "mode: companion\nevidence: 无\nuser: 杭州天气"
    weather = apply_live_fact(
        pack, LiveFact(kind="weather", text="晴 18℃", city="杭州")
    )
    assert "evidence: 天气" in weather
    assert "weather_city: 杭州" in weather
    assert "- 晴 18℃" in weather
    missing = apply_live_fact(pack, LiveFact(kind="weather", need_city=True))
    assert "evidence: 无" in missing
    assert "weather_city: 无" in missing
    news = apply_live_fact(
        pack, LiveFact(kind="news", text="标题A https://news.example/a")
    )
    assert "evidence: 新闻" in news
    assert urls_from_text("见 https://news.example/a 与 http://bad.example") == (
        "https://news.example/a",
    )


def test_tool_news_asks_for_the_hottest_ten() -> None:
    pack = "mode: companion\nevidence: 无\nuser: 今天有什么大瓜"
    text = "\n".join(
        f"标题{index} https://news.example/{index}" for index in range(1, 13)
    )
    packed = apply_tool_fact(pack, text)
    assert "只选最热的10条" in packed
    assert "微博或抖音" in packed
    affairs = apply_tool_fact("user: 今天的新闻热点", text)
    assert "时事" in affairs
    assert "娱乐八卦" in affairs
    assert "标题1 https://news.example/1" in packed
    weather = apply_tool_fact(pack, "杭州 晴 18℃")
    assert "news_reply:" not in weather
    assembled = assemble_tool_reply(text)
    assert assembled.startswith("1. **标题1** [详情](https://news.example/1)")
    assert "11. " not in assembled
    assert assemble_tool_reply("杭州 晴 18℃") == "杭州 晴 18℃"
    raw = (
        '{"city":"北京市","forecasts":['
        '{"date":"2026-10-21","week":"3","dayweather":"晴","nightweather":"晴",'
        '"daytemp":"16","nighttemp":"2","daywind":"西南","nightwind":"西南","daypower":"1-3"},'
        '{"date":"2026-10-22","week":"4","dayweather":"晴","nightweather":"多云",'
        '"daytemp":"16","nighttemp":"4","daywind":"南","daypower":"1-3"}'
        "]}"
    )
    spoken = assemble_tool_reply(raw)
    assert spoken == "北京市今天晴，2到16℃，西南风1-3级。明天白天晴，夜间多云，4到16℃。"
    assert "daytemp" not in spoken
    assert assemble_tool_reply('{"reply":"嗯","intent":"companion"}') == ""
    assert assemble_tool_reply('[{"title":"头条"}]') == ""
    assert public_reply_text('{"reply":"嗯"}') is None
    assert public_reply_text('"我在。"') == "我在。"
    assert public_reply_text("我在 {家里}。") == "我在 {家里}。"
