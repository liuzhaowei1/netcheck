# -*- coding: utf-8 -*-
"""纯逻辑单测：不联网、不开窗口。运行：python test_logic.py"""
import netcheck_app as a

N = len(a.core.TARGETS)
FOREIGN = [t[2] for t in a.core.TARGETS]


def site(ok, ms=500, status=200, err="TimeoutError", foreign=True):
    return {"ok": ok, "ms": ms if ok else None, "status": status, "err": err, "foreign": foreign}


def all_sites(foreign_ok, domestic_ok=True):
    return [site(foreign_ok if f else domestic_ok, foreign=f) for f in FOREIGN]


PROXY_OK = {"enabled": True, "server": "127.0.0.1:7890", "alive": True}

# site_level
assert a.site_level(None) == ("muted", "…")
assert a.site_level(site(True, 1999)) == ("ok", "1999")
assert a.site_level(site(True, 2000)) == ("warn", "2000")
assert a.site_level(site(True, 300, status=403)) == ("bad", "403")
assert a.site_level(site(False)) == ("bad", "超时")
assert a.site_level(site(False, err="ConnectionResetError")) == ("bad", "失败")

# speed_level
assert [a.speed_level(v) for v in (None, 0.04, 1, 9.9, 10, 52)] == ["bad", "bad", "warn", "warn", "ok", "ok"]

# exit_changed
la = {"cc": "US", "city": "洛杉矶"}
assert not a.exit_changed(None, la)
assert not a.exit_changed(la, None)
assert not a.exit_changed(la, dict(la))
assert a.exit_changed(la, {"cc": "JP", "city": "东京"})
assert a.exit_changed(la, {"cc": "US", "city": "圣何塞"})

# foreign_all_down：没测完不下结论
partial = [site(False, foreign=f) if f else None for f in FOREIGN]
partial[FOREIGN.index(True)] = None
assert not a.foreign_all_down(partial)
assert a.foreign_all_down(all_sites(False))
assert not a.foreign_all_down(all_sites(True))
mixed = all_sites(False); mixed[FOREIGN.index(True)] = site(True)
assert not a.foreign_all_down(mixed)

# diagnose 优先级
assert a.diagnose({"enabled": False, "server": None, "alive": None}, all_sites(True), None)[1].startswith("系统代理已关闭")
assert "无响应" in a.diagnose({**PROXY_OK, "alive": False}, all_sites(True), None)[1]
assert a.diagnose(PROXY_OK, all_sites(False), None)[1].startswith("境外全部不通")
assert a.diagnose(PROXY_OK, all_sites(True), {"cc": "CN"})[1].startswith("境外出口是中国")
assert a.diagnose(PROXY_OK, all_sites(True), {"cc": "US"}) is None
assert a.diagnose(None, [None] * N, None) is None

# should_skip_speed
assert a.should_skip_speed({**PROXY_OK, "alive": False}, all_sites(True))
assert a.should_skip_speed(PROXY_OK, all_sites(False))
assert not a.should_skip_speed(PROXY_OK, all_sites(True))
PROXY_OFF = {"enabled": False, "server": None, "alive": None}
assert not a.should_skip_speed(PROXY_OFF, all_sites(True))
assert not a.should_skip_speed(PROXY_OFF, all_sites(False))  # 直连测速不看境外站点

# 托盘图标像素在网格内
px, w, h = a.tray_icon_pixels()
assert px and all(0 <= x < w and 0 <= y < h for x, y, _ in px)

# 出口历史
ipA = {"ip": "1.1.1.1", "cc": "US", "country": "美国", "region": "加州", "city": "洛杉矶", "org": "NetLab", "sources": 3}
ipB = dict(ipA, ip="2.2.2.2", org="Multacom")
h = a.push_history([], ipA, "2026-10-14 10:00")
assert h == [{"ip": "1.1.1.1", "cc": "US", "country": "美国", "region": "加州", "city": "洛杉矶",
              "org": "NetLab", "region_code": None, "lat": None, "lon": None,
              "time": "2026-10-14 10:00"}]          # 不带 sources 等多余字段
h = a.push_history(h, ipB, "2026-10-14 10:05")
assert [e["ip"] for e in h] == ["2.2.2.2", "1.1.1.1"]
for i in range(20):
    h = a.push_history(h, ipA, f"t{i}")
assert len(h) == a.HISTORY_N and h[0]["time"] == "t19"

# 上一次测试：当前画面是最新一条 → 取第二条；否则取第一条
h2 = [{"ip": "new"}, {"ip": "old"}]
assert a.prev_exit(h2, True)["ip"] == "old"
assert a.prev_exit(h2, False)["ip"] == "new"
assert a.prev_exit([{"ip": "only"}], True) is None
assert a.prev_exit([{"ip": "only"}], False)["ip"] == "only"
assert a.prev_exit([], False) is None and a.prev_exit(None, True) is None

assert a.short_time("2026-10-14 10:05", "2026-10-14") == "10:05"
assert a.short_time("2026-10-13 23:50", "2026-10-14") == "10-13 23:50"
assert a.short_time(None, "2026-10-14") == "?"

# 出口区大字：州/省 · 城市
assert a.hero_text({"region": "内华达州", "city": "拉斯维加斯"}) == "内华达州 · 拉斯维加斯"
assert a.hero_text({"region": "新北市", "city": "新北"}) == "新北市"          # 重复只留较长
assert a.hero_text({"region": "香港", "city": "香港"}) == "香港"
assert a.hero_text({"region": "South West", "city": "新加坡"}) == "South West · 新加坡"
assert a.hero_text({"region": None, "city": "洛杉矶"}) == "洛杉矶"
assert a.hero_text({"region": "加州", "city": ""}) == "加州"
assert a.hero_text({}) == "?"

# 出口地图：投影与 d3 校验点一致；出口 → 点亮哪块
import geodata as G, geomap as M
for c in G.CHECKS["us"].values():
    x, y = M.project_us(*c["ll"], c["st"]); assert abs(x - c["xy"][0]) < 0.5 and abs(y - c["xy"][1]) < 0.5, c
for c in G.CHECKS["world"].values():
    x, y = M.project_world(*c["ll"]); assert abs(x - c["xy"][0]) < 0.5 and abs(y - c["xy"][1]) < 0.5, c
ks = {"ip": "203.0.113.7", "cc": "US", "country": "美国", "region": "堪萨斯州", "city": "Kansas City",
      "region_code": "KS", "lat": 39.1139, "lon": -94.6254}
sp = M.map_spec(ks)
assert sp["kind"] == "us" and sp["on"] == "20" and sp["dot"] and sp["label"] == "堪萨斯州 Kansas"
assert sp["sub"] == "Kansas City · 39.11°N 94.63°W" and len(sp["shapes"]) == 51
jp = M.map_spec({"cc": "JP", "country": "日本", "region": "东京都", "city": "Minamishinagawa", "lat": 35.6, "lon": 139.7})
assert jp["kind"] == "world" and jp["on"] == "392" and jp["title"] == "出口位置 · 日本"
assert M.map_spec({"cc": "HK", "country": "香港", "lat": 22.3, "lon": 114.2})["on"] == "344"
assert M.map_spec({"cc": "SG", "country": "新加坡"})["on"] == "702"
old = M.map_spec({"ip": "1.1.1.1", "cc": "US", "country": "美国", "region": "加州", "city": "洛杉矶"})  # 旧记录无代码坐标
assert old["on"] is None and old["dot"] is None and old["sub"] == "洛杉矶"
assert M.map_spec({"cc": "US", "region": "加州"})["sub"] == "旧记录，无坐标"
assert M.map_spec(None) is None and M.map_spec({"cc": None}) is None
assert M.map_spec({"cc": "AQ"})["on"] is None             # 不在图里的地区：不点亮，不报错

print("ALL PASS")
