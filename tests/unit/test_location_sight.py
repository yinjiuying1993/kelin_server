from app.domain.location_sight import location_privacy_reason, location_sight_summary


def test_location_summary_is_label_and_city() -> None:
    assert location_sight_summary(label=" 外滩 ", city="上海") == "外滩 · 上海"


def test_location_privacy_allows_coarse_poi() -> None:
    assert location_privacy_reason("外滩", "上海") is None
    assert location_privacy_reason("人民公园", "上海") is None


def test_location_privacy_rejects_url_control_coord_and_street() -> None:
    assert location_privacy_reason("https://maps.example.com/bund", "上海") == "url"
    assert location_privacy_reason("外滩", "www.example.com") == "url"
    assert location_privacy_reason("外滩\x00", "上海") == "control_char"
    assert location_privacy_reason("31.230, 121.490", "上海") == "coordinates"
    assert location_privacy_reason("latitude 31.2", "上海") == "coordinates"
    assert location_privacy_reason("中山东一路12号", "上海") == "street_address"
    assert location_privacy_reason("外滩", "门牌18") == "street_address"
