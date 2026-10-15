# -*- coding: utf-8 -*-
"""出口地图的纯逻辑：经纬度 → 画布坐标、出口 → 该画哪张图、点亮哪块。不依赖 tkinter。

投影公式照搬 d3-geo（geoAlbersUsa / geoNaturalEarth1），geodata.CHECKS 里存了 d3 算出的校验点，
test_logic.py 会逐点比对，误差须 < 0.5（画布单位）。
"""
import math

import geodata as G

US_K, US_T = 1300.0, (487.5, 305.0)


def _conic_equal_area(y0, y1):
    """d3 conicEqualAreaRaw（弧度）。"""
    sy0 = math.sin(y0)
    n = (sy0 + math.sin(y1)) / 2
    c = 1 + sy0 * (2 * n - sy0)
    r0 = math.sqrt(c) / n

    def raw(x, y):
        r = math.sqrt(max(c - 2 * n * math.sin(y), 0)) / n
        x *= n
        return r * math.sin(x), r0 - r * math.cos(x)
    return raw


def _projection(parallels, rotate, center, scale, translate):
    """d3 projection：rotate（仅经度）→ raw → 以 center 为原点缩放平移，y 轴朝下。"""
    raw = _conic_equal_area(math.radians(parallels[0]), math.radians(parallels[1]))
    cx, cy = raw(math.radians(center[0]), math.radians(center[1]))

    def f(lon, lat):
        lam = math.radians(((lon + rotate + 180) % 360) - 180)
        x, y = raw(lam, math.radians(lat))
        return translate[0] + scale * (x - cx), translate[1] - scale * (y - cy)
    return f


_k, (_x, _y) = US_K, US_T
_LOWER48 = _projection((29.5, 45.5), 96, (-0.6, 38.7), _k, (_x, _y))
_ALASKA = _projection((55, 65), 154, (-2, 58.5), 0.35 * _k, (_x - 0.307 * _k, _y + 0.201 * _k))
_HAWAII = _projection((8, 18), 157, (-3, 19.9), _k, (_x - 0.205 * _k, _y + 0.212 * _k))


def project_us(lon, lat, state=None):
    """geoAlbersUsa：阿拉斯加、夏威夷按州代码走各自的插图投影（d3 按裁剪框判断，结果一致）。"""
    if state == "AK":
        return _ALASKA(lon, lat)
    if state == "HI":
        return _HAWAII(lon, lat)
    return _LOWER48(lon, lat)


def _natural_earth_raw(lam, phi):
    phi2 = phi * phi
    phi4 = phi2 * phi2
    return (lam * (0.8707 - 0.131979 * phi2 + phi4 * (-0.013791 + phi4 * (0.003971 * phi2 - 0.001529 * phi4))),
            phi * (1.007226 + phi2 * (0.015085 + phi4 * (-0.044475 + 0.028874 * phi2 - 0.005916 * phi4))))


def project_world(lon, lat):
    x, y = _natural_earth_raw(math.radians(lon), math.radians(lat))
    k = G.WORLD_FIT["scale"]
    tx, ty = G.WORLD_FIT["translate"]
    return tx + k * x, ty - k * y


def map_spec(ip):
    """出口 → 地图规格；没有国家码返回 None（不弹浮窗）。
    返回 {kind: 'us'|'world', size, shapes: {id: rings}, on: 点亮的 id 或 None, dot: (x, y) 或 None,
          title, label, sub}"""
    if not ip or not ip.get("cc"):
        return None
    lat, lon = ip.get("lat"), ip.get("lon")
    has_ll = lat is not None and lon is not None
    coord = ""
    if has_ll:
        coord = f"{abs(lat):.2f}°{'N' if lat >= 0 else 'S'} {abs(lon):.2f}°{'E' if lon >= 0 else 'W'}"
    city = ip.get("city") or ""
    sub = " · ".join(x for x in (city, coord) if x) or "旧记录，无坐标"
    country = ip.get("country") or ip["cc"]
    if ip["cc"] == "US":
        code = (ip.get("region_code") or "").upper()
        fips = G.STATE_FIPS.get(code)
        en = G.US_STATES[fips]["name"] if fips else ""
        region = ip.get("region") or en
        return {"kind": "us", "size": G.US_SIZE,
                "shapes": {k: v["rings"] for k, v in G.US_STATES.items()}, "outline": G.US_NATION,
                "on": fips, "dot": project_us(lon, lat, code) if has_ll else None,
                "title": "出口位置 · 美国", "label": f"{region} {en}".strip() or "美国", "sub": sub}
    num = G.CC2NUM.get(ip["cc"])
    region = ip.get("region") or ""
    return {"kind": "world", "size": G.WORLD_SIZE, "shapes": G.WORLD, "outline": [],
            "on": num if num in G.WORLD else None, "dot": project_world(lon, lat) if has_ll else None,
            "title": f"出口位置 · {country}", "label": " · ".join(x for x in (country, region) if x), "sub": sub}
