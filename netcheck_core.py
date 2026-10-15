"""NetCheck 核心：连通性 / 出口 IP 定位 / 测速。纯标准库。

urllib 在 Windows 上会自动读取注册表里的系统代理（Clash、v2rayN 等代理软件设置的 127.0.0.1:xxxx），
所以默认 opener 的流量和浏览器一样走代理；DIRECT 是不走代理的对照组。
"""
import json
import socket
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# 用常见浏览器 UA：部分站点对非浏览器 UA 直接 403，会被误判为"被拒绝"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"}
def via_proxy():
    """每次调用都重新读系统代理。不能在 import 时建一次缓存：
    NetCheck 先于代理软件启动时会缓存到"无代理"，之后连通性/IP 一直直连（实测踩坑）。"""
    return urllib.request.build_opener()
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# (显示名, URL, 是否境外)
TARGETS = [
    ("Google", "https://www.google.com/generate_204", True),
    ("YouTube", "https://www.youtube.com/generate_204", True),
    ("GitHub", "https://github.com/", True),
    ("OpenAI", "https://openai.com/", True),
    ("Claude", "https://code.claude.com/docs/en/agents", True),
    ("百度", "https://www.baidu.com/", False),
]

CC_ZH = {"US": "美国", "JP": "日本", "HK": "香港", "TW": "台湾", "SG": "新加坡", "KR": "韩国",
         "GB": "英国", "DE": "德国", "FR": "法国", "NL": "荷兰", "CA": "加拿大", "AU": "澳大利亚",
         "CN": "中国", "MO": "澳门", "RU": "俄罗斯", "IN": "印度", "MY": "马来西亚", "TH": "泰国",
         "VN": "越南", "PH": "菲律宾", "ID": "印尼", "TR": "土耳其", "AR": "阿根廷", "BR": "巴西"}


def _get(opener, url, timeout=8, limit=65536):
    req = urllib.request.Request(url, headers=UA)
    with opener.open(req, timeout=timeout) as r:
        return r.status, r.read(limit)


# ---------- 系统代理 ----------
def proxy_info():
    """读系统代理设置，并探测代理端口是否在监听（代理软件是否在跑）。"""
    info = {"enabled": False, "server": None, "alive": None}
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        info["enabled"] = bool(winreg.QueryValueEx(k, "ProxyEnable")[0])
        info["server"] = winreg.QueryValueEx(k, "ProxyServer")[0]
    except (ImportError, OSError):  # 非 Windows 没有 winreg
        pass
    if info["enabled"] and info["server"]:
        hostport = info["server"].split(";")[0].split("=")[-1]
        host, _, port = hostport.rpartition(":")
        try:
            socket.create_connection((host, int(port)), timeout=1).close()
            info["alive"] = True
        except (OSError, ValueError):
            info["alive"] = False
    return info


# ---------- 连通性 ----------
def check_one(name, url, foreign):
    """任何 HTTP 响应（含 403 等）都算通；只有超时/连接失败才算不通。"""
    t = time.perf_counter()
    try:
        status, _ = _get(via_proxy(), url, timeout=10, limit=1024)
    except urllib.error.HTTPError as e:
        # Cloudflare 人机验证页（403 + cf-mitigated: challenge）说明网络是通的，不算被拒
        status = 200 if (e.headers.get("cf-mitigated") or "").lower() == "challenge" else e.code
    except Exception as e:  # noqa: BLE001
        reason = getattr(e, "reason", e)  # URLError 包着真正原因（超时/连接重置等）
        return {"name": name, "ok": False, "ms": None, "err": type(reason).__name__,
                "foreign": foreign}
    return {"name": name, "ok": True, "ms": int((time.perf_counter() - t) * 1000),
            "status": status, "foreign": foreign}


def connectivity():
    with ThreadPoolExecutor(len(TARGETS)) as ex:
        return list(ex.map(lambda a: check_one(*a), TARGETS))


# ---------- 出口 IP ----------
def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ipinfo(opener):
    d = json.loads(_get(opener, "https://ipinfo.io/json")[1])
    lat, _, lon = (d.get("loc") or "").partition(",")
    return {"ip": d["ip"], "cc": d.get("country"), "region": d.get("region"),
            "city": d.get("city"), "org": d.get("org", "").split(" ", 1)[-1], "src": "ipinfo",
            "region_code": None, "lat": _num(lat), "lon": _num(lon)}


def _ipapi(opener):
    d = json.loads(_get(opener, "http://ip-api.com/json/?lang=zh-CN")[1])
    if d.get("status") != "success":
        raise RuntimeError(d.get("message"))
    return {"ip": d["query"], "cc": d.get("countryCode"), "region": d.get("regionName"),
            "city": d.get("city"), "org": d.get("isp"), "src": "ip-api",
            "region_code": d.get("region") or None, "lat": _num(d.get("lat")), "lon": _num(d.get("lon"))}


def _ipwho(opener):
    d = json.loads(_get(opener, "https://ipwho.is/")[1])
    if not d.get("success"):
        raise RuntimeError(d.get("message"))
    return {"ip": d["ip"], "cc": d.get("country_code"), "region": d.get("region"),
            "city": d.get("city"), "org": (d.get("connection") or {}).get("isp"), "src": "ipwho",
            "region_code": d.get("region_code") or None,
            "lat": _num(d.get("latitude")), "lon": _num(d.get("longitude"))}


def exit_ip(opener):
    """多个定位源并发查，返回第一个成功的；ip-api 有中文城市名，优先采用。"""
    with ThreadPoolExecutor(3) as ex:
        futs = {f.__name__: ex.submit(f, opener) for f in (_ipapi, _ipinfo, _ipwho)}
        results = {}
        for name, fu in futs.items():
            try:
                results[name] = fu.result(timeout=10)
            except Exception:  # noqa: BLE001
                pass
    if not results:
        return None
    best = results.get("_ipapi") or results.get("_ipinfo") or results.get("_ipwho")
    best["country"] = CC_ZH.get(best["cc"], best["cc"])
    # 首选源缺州代码/坐标时，从同一 IP 的其他源补（地图浮窗要用）
    for r in results.values():
        if r is best or r.get("ip") != best["ip"]:
            continue
        for k in ("region_code", "lat", "lon"):
            if best.get(k) is None and r.get(k) is not None:
                best[k] = r[k]
    # 各源城市不一致时记下来，提示定位可能不准
    en = {r["city"] for k, r in results.items() if k != "_ipapi" and r.get("city")}
    best["sources"] = len(results)
    best["alt_cities"] = sorted(en) if len(en) > 1 else []
    return best


def _domestic_ip():
    """经系统代理访问国内站，看代理软件对国内流量是直连还是也走了代理。"""
    body = _get(via_proxy(), "https://myip.ipip.net/json")[1]
    d = json.loads(body)["data"]
    loc = [x for x in d["location"] if x]
    return {"ip": d["ip"], "desc": " ".join(loc[1:4]) if len(loc) > 1 else " ".join(loc)}


def ip_report():
    with ThreadPoolExecutor(2) as ex:
        f_out = ex.submit(exit_ip, via_proxy())
        f_cn = ex.submit(_domestic_ip)
        out = f_out.result()
        try:
            cn = f_cn.result()
        except Exception:  # noqa: BLE001
            cn = None
    return {"foreign": out, "domestic": cn}


# ---------- 测速 ----------
SPEED_HOST = "speed.cloudflare.com"


def _speed_conn(timeout):
    """经系统代理（CONNECT 隧道）连 Cloudflare 测速节点；无代理时直连。"""
    import http.client
    p = proxy_info()
    if p["enabled"] and p["server"]:
        host, _, port = p["server"].split(";")[0].split("=")[-1].rpartition(":")
        c = http.client.HTTPSConnection(host, int(port), timeout=timeout)
        c.set_tunnel(SPEED_HOST)
    else:
        c = http.client.HTTPSConnection(SPEED_HOST, timeout=timeout)
    return c


class Cancelled(Exception):
    """测速被用户点「停止」打断。"""


def _check(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled


def speed_down(seconds=5.0, chunk=25_000_000, cancel=None):
    """限时下载：同一连接上连续请求 25MB 块，跑满 seconds 秒即停。
    Cloudflare 单次超过约 50MB 直接 403（实测 100MB/200MB 均 403），所以分块而不是一次要大文件。
    节点慢或中途断开时按已收字节计算；计时从首个响应头开始，排除握手耗时。
    cancel（threading.Event）置位后最多再收一块 64KB 即抛 Cancelled 并断开连接，不再占带宽。"""
    c = _speed_conn(timeout=8)
    got, t0 = 0, None
    try:
        while t0 is None or time.perf_counter() - t0 < seconds:
            _check(cancel)
            c.request("GET", f"/__down?bytes={chunk}", headers=UA)
            r = c.getresponse()
            if r.status != 200:
                raise OSError(f"HTTP {r.status}")
            if t0 is None:
                t0 = time.perf_counter()
            while time.perf_counter() - t0 < seconds:
                _check(cancel)
                buf = r.read1(65536)
                if not buf:
                    break
                got += len(buf)
            else:
                break  # 时间到，丢弃本块剩余部分
    except OSError:
        if t0 is None or got == 0:
            raise
    finally:
        c.close()
    dt = time.perf_counter() - t0
    return got * 8 / dt / 1e6


def speed_up(budget=8.0, cancel=None):
    """上传测速：以 Cloudflare 回响应（确认收齐）为计时终点。
    只看本地发送速度会被代理的本地缓冲骗过（实测下行 0.03 时"上行"显示 5 Mbps）。
    复用一条连接、从小到大发 256KB→16MB，单次耗时超过 2 秒或总预算用完即停，取最后一次结果。
    请求体按 64KB 分块由生成器送出，cancel 置位后下一块即抛 Cancelled，中断正在发送的请求。"""
    def body(size):
        block = b"0" * 65536
        left = size
        while left > 0:
            _check(cancel)
            n = min(left, len(block))
            yield block[:n]
            left -= n

    c = _speed_conn(timeout=budget)
    c.connect()  # 握手在计时外
    rate, t_start = None, time.perf_counter()
    try:
        for size in (256_000, 1_000_000, 4_000_000, 16_000_000):
            t0 = time.perf_counter()
            _check(cancel)
            c.request("POST", "/__up", body=body(size),  # 显式 Content-Length，否则生成器会走 chunked
                      headers={**UA, "Content-Type": "application/octet-stream",
                               "Content-Length": str(size)})
            c.getresponse().read()
            dt = time.perf_counter() - t0
            rate = size * 8 / dt / 1e6
            if dt > 2.0 or time.perf_counter() - t_start + dt * 4 > budget:
                break
    except OSError:
        if rate is None:
            raise
    finally:
        c.close()
    return rate


def speed():
    res = {}
    for k, fn in (("down", speed_down), ("up", speed_up)):
        try:
            res[k] = round(fn(), 2)
        except Exception as e:  # noqa: BLE001
            res[k] = None
            res[k + "_err"] = type(e).__name__
    return res


# ---------- 命令行 ----------
def main():
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    t = time.perf_counter()
    p = proxy_info()
    print(f"系统代理: {'开' if p['enabled'] else '关'} {p['server'] or ''}"
          f"  端口{'在监听' if p['alive'] else '无响应' if p['alive'] is False else '-'}")
    with ThreadPoolExecutor(2) as ex:
        fc, fi = ex.submit(connectivity), ex.submit(ip_report)
        conn, ips = fc.result(), fi.result()
    for c in conn:
        print(f"  {'✅' if c['ok'] else '❌'} {c['name']:<8} "
              f"{str(c['ms']) + 'ms' if c['ok'] else c['err']}")
    f = ips["foreign"]
    if f:
        alt = f"（其他源: {', '.join(f['alt_cities'])}）" if f["alt_cities"] else ""
        print(f"境外出口: {f['ip']}  {f['country']} {f['region']} {f['city']} · {f['org']}"
              f"  [{f['sources']}源]{alt}")
    else:
        print("境外出口: 查询失败")
    d = ips["domestic"]
    print(f"国内出口: {d['ip']}  {d['desc']}" if d else "国内出口: 查询失败")
    print(f"--- 连通性+IP 用时 {time.perf_counter() - t:.1f}s，开始测速 ---")
    s = speed()
    fmt = lambda k: f"{s[k]} Mbps" if s[k] is not None else s[k + '_err']  # noqa: E731
    print(f"下行 {fmt('down')}   上行 {fmt('up')}")
    print(f"总用时 {time.perf_counter() - t:.1f}s")


if __name__ == "__main__":
    main()
