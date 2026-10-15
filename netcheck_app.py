# -*- coding: utf-8 -*-
"""NetCheck 浮窗：点「测试」一次测出连通性、出口城市、网速。

窗口行为照搬 CC Pulse：无边框、📌 置顶开关、拖标题栏移动并记住位置、— 收进托盘、✕ 退出。
测试在后台线程跑，结果经队列交回 tkinter 主线程（tkinter 非线程安全）。
"""
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor, as_completed

import geomap
import netcheck_core as core

IS_WINDOWS = sys.platform == "win32"

# 配色取自 CC Pulse 深色主题，另加三个语义色
T = {
    "bg": "#1E1E1E", "title_bg": "#2A2A2A", "fg": "#FFFFFF", "fg_dim": "#9A9A9A",
    "sep": "#383838", "accent": "#46C28E", "btn_bg": "#333333", "muted": "#525252",
    "ok": "#3FB950", "warn": "#E3B341", "bad": "#F2555A",
    "bad_bg": "#3A1F21", "warn_bg": "#3A3220",
    "map_land": "#2B2B2B", "map_edge": "#4B4B4B", "map_outline": "#6E6E6E", "map_on_edge": "#9BE8C6",
    "cc_held": "#24352D",
}
UI = "Microsoft YaHei UI"
MONO = "Consolas"
MARQUEE_STEP_MS = 30    # 出口文字过长时横向滚动：每 30ms 左移 1px
MARQUEE_PAUSE_MS = 1500 # 每轮回到开头停 1.5s，方便看清开头
MARQUEE_GAP = "　　　"   # 首尾衔接的空白
WIDTH = 300
TITLEBAR_H = 26
SLOW_MS = 2000          # 站点响应 ≥2s 标黄
SPEED_FULL = 60.0       # 速度条满格对应的 Mbps
POLL_MS = 100
PULSE_S = 1.1           # 等待动效周期（与设计稿 @keyframes pulse 1.1s 一致）
PULSE_MIN = 0.35        # 最暗时的不透明度（设计稿 opacity .35）
MAP_GAP = 8             # 地图浮窗与主窗口间隔（设计稿 px）
MAP_ON_ALPHA = 0.82     # 点亮色透明度（设计稿 fill-opacity .82）
RING_S = 1.6            # 城市白点扩散圈周期（设计稿 @keyframes ring 1.6s）
UI_SCALE = 0.866        # 整体缩放：宽高各 ×0.866 ≈ 面积 75%（2026-10-14 用户定：相对 375×302 缩小 25%）


def P(px):
    """设计稿像素 → Tk 点单位（1px@96dpi = 0.75pt），由 Tk 按屏幕 DPI 换算，高分屏下尺寸与设计稿一致。"""
    return f"{px * 0.75:g}p"

CONFIG_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "NetCheck")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
DEFAULT_CFG = {"pos_x": None, "pos_y": None, "always_on_top": True, "last_exit": None,
               "exit_history": []}
HISTORY_N = 10          # 出口历史保留条数（新→旧）；「历史」按钮只用到上一条
EXIT_KEYS = ("ip", "cc", "country", "region", "city", "org", "region_code", "lat", "lon")


# ---------- 纯逻辑（test_logic.py 覆盖） ----------
def site_level(r):
    """单个站点结果 → (颜色级别, 右侧文字)。"""
    if r is None:
        return "muted", "…"
    if not r["ok"]:
        timeout = r.get("err") in ("TimeoutError", "timeout", "socket.timeout")
        return "bad", "超时" if timeout else "失败"
    if r.get("status", 200) >= 400:
        return "bad", str(r["status"])
    return ("ok" if r["ms"] < SLOW_MS else "warn"), str(r["ms"])


def speed_level(v):
    if v is None:
        return "bad"
    return "ok" if v >= 10 else "warn" if v >= 1 else "bad"


def exit_changed(last, now):
    """上次与本次出口的国家或城市不同 → True；任一缺失不算变化。"""
    if not last or not now:
        return False
    return (last.get("cc"), last.get("city")) != (now.get("cc"), now.get("city"))


def foreign_all_down(sites):
    """境外站点全部测完且全部连不上。还有没测完的不下结论，避免第一个失败就误报。"""
    foreign = [r for r, t in zip(sites, core.TARGETS) if t[2]]
    return bool(foreign) and all(r is not None and not r["ok"] for r in foreign)


def should_skip_speed(proxy, sites):
    """代理没开/端口不通/境外全挂时测速没有意义，跳过省约 10 秒。"""
    if proxy is not None and (not proxy["enabled"] or proxy["alive"] is False):
        return True
    return foreign_all_down(sites)


def diagnose(proxy, sites, ip):
    """按严重程度给出顶部提示条：(级别, 文字) 或 None。sites 为已完成的站点结果列表。"""
    if proxy is not None:
        if not proxy["enabled"]:
            return "bad", "系统代理已关闭，代理软件没有接管网络"
        if proxy["alive"] is False:
            return "bad", f"代理端口 {proxy['server']} 无响应，代理软件可能已退出"
    if foreign_all_down(sites):
        return "bad", "境外全部不通：代理在运行，但当前节点连不上，请在代理软件里换一个节点"
    if ip and ip.get("cc") == "CN":
        return "bad", "境外出口是中国 IP，代理没有生效"
    return None


def blend(c1, c2, k):
    """c1→c2 按 k∈[0,1] 线性混色，用来在 tkinter 里模拟不透明度。"""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02X%02X%02X" % tuple(round(x + (y - x) * k) for x, y in zip(a, b))


def push_history(history, ip, when):
    """本次出口插到最前，截到 HISTORY_N 条。"""
    entry = {k: ip.get(k) for k in EXIT_KEYS}
    entry["time"] = when
    return [entry] + list(history or [])[:HISTORY_N - 1]


def prev_exit(history, shown_is_latest):
    """「历史」按钮要显示的上一次测试。
    当前画面就是 history[0]（启动后的上次结果 / 刚测完）→ 取 history[1]；
    当前画面不是（测试中、或本次没查到 IP）→ 取 history[0]。"""
    i = 1 if shown_is_latest else 0
    return history[i] if history and len(history) > i else None


def short_time(when, today):
    """'2026-10-14 10:05' → 同一天显示 '10:05'，否则 '10-13 10:05'。"""
    if not when:
        return "?"
    d, _, t = when.partition(" ")
    return t if d == today else f"{d[5:]} {t}"


def hero_text(ip):
    """出口区大字：「州/省 · 城市」，国家看左边国家码。两者基本重复（新北市/新北）只留较长的。"""
    r = (ip.get("region") or "").strip()
    c = (ip.get("city") or "").strip()
    if r and c:
        if c in r or r in c:
            return max(r, c, key=len)
        return f"{r} · {c}"
    return r or c or "?"


def city_text(ip):
    return f"{ip['country']} · {ip['city'] or ip['region'] or '?'}"


# ---------- 配置 ----------
def load_config():
    cfg = dict(DEFAULT_CFG)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    if not cfg.get("exit_history") and cfg.get("last_exit"):
        cfg["exit_history"] = [dict(cfg["last_exit"], time=None)]
    return cfg


def save_config(cfg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except OSError:
        pass


def tray_icon_pixels(size=16):
    """托盘图标：绿色圆环 + 赤道/经线，程序内生成，无图片资源。"""
    px, c, r = [], (size - 1) / 2, size / 2 - 1
    for y in range(size):
        for x in range(size):
            d = ((x - c) ** 2 + (y - c) ** 2) ** 0.5
            ring = r - 1.6 <= d <= r
            inner = d < r - 1.6 and (abs(y - c) < 0.6 or abs(x - c) < 0.6
                                     or abs(((x - c) / 0.45) ** 2 + (y - c) ** 2 - r * r) < r * 1.4)
            if ring or inner:
                px.append((x, y, T["accent"]))
    return px, size, size


# ---------- 后台测试 ----------
def run_test(q, cancel=None):
    """依次投递事件：proxy / site(i) / ip / cn / down / up / done。
    cancel 置位后不再开始测速、正在跑的测速立即断开；界面那边换了新队列，这里再投递也没人收。"""
    cancel = cancel or threading.Event()
    proxy = core.proxy_info()
    q.put(("proxy", None, proxy))
    sites = [None] * len(core.TARGETS)
    with ThreadPoolExecutor(len(core.TARGETS) + 2) as ex:
        futs = {ex.submit(core.check_one, *t): ("site", i) for i, t in enumerate(core.TARGETS)}
        futs[ex.submit(core.exit_ip, core.via_proxy())] = ("ip", None)
        futs[ex.submit(core._domestic_ip)] = ("cn", None)
        for f in as_completed(futs):
            kind, i = futs[f]
            try:
                res = f.result()
            except Exception:  # noqa: BLE001
                res = None
            if kind == "site":
                sites[i] = res
            q.put((kind, i, res))
    if cancel.is_set():
        return
    if should_skip_speed(proxy, sites):
        q.put(("skip_speed", None, None))
    else:
        for kind, fn in (("down", core.speed_down), ("up", core.speed_up)):
            q.put((kind, None, "run"))
            try:
                v = round(fn(cancel=cancel), 2)
            except core.Cancelled:
                return
            except Exception:  # noqa: BLE001
                v = None
            q.put((kind, None, v))
    q.put(("done", None, None))


# ---------- 界面 ----------
class App:
    def __init__(self):
        self.cfg = load_config()
        self.root = tk.Tk()
        # 所有尺寸和字号都以点（pt）为单位，改 tk scaling 即可整体等比缩放
        self.root.tk.call("tk", "scaling", float(self.root.tk.call("tk", "scaling")) * UI_SCALE)
        self.root.title("NetCheck")
        self.root.configure(bg=T["bg"])
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", self.cfg["always_on_top"])
        self.q = queue.Queue()
        self.running = False
        self.t0 = 0.0
        self.state = {}
        self._drag = (0, 0)

        self._build()
        self._place()
        self._render_idle()

        self._tray_q = queue.Queue()
        self.tray = None
        if IS_WINDOWS:
            try:
                import tray as _tray
                self.tray = _tray.TrayIcon(
                    tip="NetCheck", icon=tray_icon_pixels(),
                    on_show=lambda: self._tray_q.put("show"),
                    on_exit=lambda: self._tray_q.put("exit"))
                self.tray.start()
                self.tray.add()
            except Exception:  # noqa: BLE001
                self.tray = None
        self.root.after(POLL_MS, self._poll)

    # ----- 构建 -----
    def _lbl(self, parent, text="", fg=None, font=(UI, 9), bg=None, **kw):
        return tk.Label(parent, text=text, fg=fg or T["fg"], bg=bg or T["bg"], font=font,
                        bd=0, padx=P(0), pady=P(0), **kw)

    def _sep(self):
        tk.Frame(self.root, bg=T["sep"], height=1).pack(fill="x", padx=P(10))

    def _build(self):
        r = self.root
        # 标题栏
        bar = tk.Frame(r, bg=T["title_bg"], height=P(TITLEBAR_H), width=P(WIDTH))
        bar.pack(fill="x")
        bar.pack_propagate(False)
        name = self._lbl(bar, "NetCheck", font=(UI, 9, "bold"), bg=T["title_bg"])
        name.pack(side="left", padx=(P(10), P(6)))
        self.px_lbl = self._lbl(bar, "", fg=T["fg_dim"], font=(MONO, 8), bg=T["title_bg"])
        self.px_lbl.pack(side="left")
        self.btn_close = self._tbtn(bar, "✕", self.close)
        self.btn_min = self._tbtn(bar, "—", self.minimize)
        self.btn_pin = self._tbtn(bar, "📌", self.toggle_pin)
        self.btn_go = tk.Label(bar, text="测试", bg=T["accent"], fg="#FFFFFF", font=(UI, 8, "bold"),
                               padx=P(10), pady=P(1), cursor="hand2")
        self.btn_go.pack(side="right", padx=(P(0), P(4)))
        self.btn_go.bind("<Button-1>", lambda e: self.stop_test() if self.running else self.start_test())
        self._refresh_pin()
        for w in (bar, name, self.px_lbl):
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_end)

        # 提示条（无提示时不 pack）
        self.alert = tk.Label(r, text="", font=(UI, 8), justify="left", anchor="w",
                              wraplength=P(WIDTH - 40), padx=P(9), pady=P(5))

        # 出口城市
        hero = tk.Frame(r, bg=T["bg"])
        hero.pack(fill="x", padx=P(12), pady=(P(10), P(9)))
        self.hero = hero
        # 国家码方块兼作按钮：按住在左侧弹出出口地图，松开收起。描边常驻（同底色不可见），按住时变绿
        self.cc_lbl = tk.Label(hero, text="··", bg=T["title_bg"], fg=T["muted"],
                               font=(MONO, 10, "bold"), width=4, height=2, cursor="hand2",
                               highlightthickness=1, highlightbackground=T["title_bg"])
        self.cc_lbl.grid(row=0, column=0, rowspan=2, sticky="w", padx=(P(0), P(10)))
        self.cc_lbl.bind("<ButtonPress-1>", self._map_show)
        self.cc_lbl.bind("<ButtonRelease-1>", self._map_hide)
        self._hero_ip = None    # 出口区当前显示的出口（地图浮窗用）
        self.map_win = None
        self._ring_job = None
        line = tk.Frame(hero, bg=T["bg"])
        line.grid(row=0, column=1, sticky="ew")
        self.was_lbl = self._lbl(line, "", fg=T["fg_dim"], font=(UI, 8, "overstrike"))
        # 城市文字放在裁剪框里：放得下静止，放不下由 _marquee_step 横向滚动
        self.city_box = tk.Frame(line, bg=T["bg"], height=1)
        self.city_box.pack(side="left")
        self.city_box.pack_propagate(False)
        self.city_lbl = self._lbl(self.city_box, "", font=(UI, 13, "bold"))
        self.city_lbl.place(x=0, rely=0.5, anchor="w")
        self._city_text, self._marq_job, self._marq_x = "", None, 0
        self.ipl_lbl = self._lbl(hero, "", fg=T["fg_dim"], font=(MONO, 8))
        self.ipl_lbl.grid(row=1, column=1, sticky="w")
        self.src_lbl = tk.Label(hero, text="", bg=T["bg"], fg=T["fg_dim"], font=(UI, 7),
                                highlightthickness=1, highlightbackground=T["sep"], padx=P(4))
        self.src_lbl.grid(row=0, column=2, sticky="ne")
        # 「历史」：按住显示上一次测试的出口，松开恢复
        self.hist_btn = tk.Label(hero, text="历史", bg=T["bg"], fg=T["fg_dim"], font=(UI, 7),
                                 highlightthickness=1, highlightbackground=T["sep"], padx=P(4))
        # 叠放在右下角、不参与 grid 排版：按钮比 IP 行高，放进 grid 会把整窗撑高约 10px
        self.hist_btn.place(relx=1.0, rely=1.0, anchor="se")
        self.hist_btn.bind("<ButtonPress-1>", self._peek_start)
        self.hist_btn.bind("<ButtonRelease-1>", self._peek_end)
        self._peek = None          # 按住期间保存的当前画面
        self._shown_latest = False # 当前画面是否就是 exit_history[0]
        hero.grid_columnconfigure(1, weight=1)
        # 右侧「N/3 源」框和「历史」按钮上下叠放，出口区高度至少要放得下两者（不重叠），
        # 不够的差额补给 IP 行；按钮本身用 place 叠放，不参与 grid 撑高
        r.update_idletasks()
        gap = int(r.winfo_fpixels(P(3)))
        need = self.src_lbl.winfo_reqheight() + self.hist_btn.winfo_reqheight() + gap
        short = need - hero.winfo_reqheight()
        if short > 0:
            hero.grid_rowconfigure(1, minsize=self.ipl_lbl.winfo_reqheight() + short)

        self._sep()
        self._lbl(r, "连通", fg=T["muted"], font=(UI, 7)).pack(anchor="w", padx=P(12), pady=(P(6), P(1)))
        grid = tk.Frame(r, bg=T["bg"])
        grid.pack(fill="x", padx=P(12), pady=(P(0), P(7)))
        self.site_w = []
        for i, (name_, _url, foreign) in enumerate(core.TARGETS):
            cell = tk.Frame(grid, bg=T["bg"], height=P(22))
            cell.grid(row=i // 2, column=i % 2, sticky="ew", padx=(P(0), P(14)) if i % 2 == 0 else 0)
            dot = self._lbl(cell, "●", fg=T["muted"], font=(UI, 7))
            dot.pack(side="left", padx=(P(0), P(6)))
            self._lbl(cell, name_, font=(UI, 9)).pack(side="left")
            if not foreign:
                self._lbl(cell, "直连", fg=T["fg_dim"], font=(UI, 7)).pack(side="left", padx=(P(3), P(0)))
            ms = self._lbl(cell, "", fg=T["fg_dim"], font=(MONO, 8))
            ms.pack(side="right")
            self.site_w.append((dot, ms))
        grid.grid_columnconfigure(0, weight=1, uniform="c")
        grid.grid_columnconfigure(1, weight=1, uniform="c")

        self._sep()
        sp = tk.Frame(r, bg=T["bg"])
        sp.pack(fill="x", padx=P(12), pady=(P(4), P(10)))
        self.speed_w = {}
        self._speed_state = {}
        for col, (key, title) in enumerate((("down", "↓ 下载"), ("up", "↑ 上传"))):
            box = tk.Frame(sp, bg=T["bg"])
            box.grid(row=0, column=col, sticky="ew", padx=(P(0), P(14)) if col == 0 else 0)
            head = tk.Frame(box, bg=T["bg"])
            head.pack(fill="x")
            self._lbl(head, title, fg=T["fg_dim"], font=(UI, 8)).pack(side="left")
            note = self._lbl(head, "", fg=T["fg_dim"], font=(UI, 8))
            note.pack(side="right")
            vrow = tk.Frame(box, bg=T["bg"])
            vrow.pack(fill="x")
            val = self._lbl(vrow, "—", fg=T["muted"], font=(MONO, 13, "bold"))
            val.pack(side="left")
            unit = self._lbl(vrow, "", fg=T["fg_dim"], font=(UI, 8))
            unit.pack(side="left", padx=(P(3), P(0)), pady=(P(4), P(0)))
            bar_c = tk.Canvas(box, width=1, height=P(3), bg=T["sep"], highlightthickness=0, bd=0)
            bar_c.pack(fill="x", pady=(P(3), P(0)))
            self.speed_w[key] = (note, val, unit, bar_c)
        sp.grid_columnconfigure(0, weight=1, uniform="s")
        sp.grid_columnconfigure(1, weight=1, uniform="s")

        tk.Frame(r, bg=T["sep"], height=1).pack(fill="x")
        foot = tk.Frame(r, bg=T["bg"])
        foot.pack(fill="x", padx=P(12), pady=(P(4), P(5)))
        self.foot_l = self._lbl(foot, "", fg=T["fg_dim"], font=(UI, 8))
        self.foot_l.pack(side="left")
        self.foot_r = self._lbl(foot, "", fg=T["fg_dim"], font=(UI, 8))
        self.foot_r.pack(side="right")
        r.update_idletasks()

    def _tbtn(self, parent, glyph, cmd):
        lbl = tk.Label(parent, text=glyph, bg=T["title_bg"], fg=T["fg_dim"],
                       font=("Segoe UI Emoji", 9), padx=P(5), cursor="hand2")
        lbl.pack(side="right")
        lbl.bind("<Button-1>", lambda e: cmd())
        lbl.bind("<Enter>", lambda e: lbl.config(fg=T["fg"]))
        lbl.bind("<Leave>", lambda e: self._restore_btn(lbl))
        return lbl

    def _restore_btn(self, lbl):
        on = lbl is self.btn_pin and self.cfg["always_on_top"]
        lbl.config(fg=T["accent"] if on else T["fg_dim"])

    def _refresh_pin(self):
        self.btn_pin.config(fg=T["accent"] if self.cfg["always_on_top"] else T["muted"])

    def _place(self):
        self.W = int(self.root.winfo_fpixels(P(WIDTH)))
        self._size = None
        sw = self.root.winfo_screenwidth()
        x, y = self.cfg.get("pos_x"), self.cfg.get("pos_y")
        if x is None or y is None:
            x, y = sw - self.W - 40, 80
        self.root.geometry(f"+{int(x)}+{int(y)}")
        self._fit_size()

    def _fit_size(self):
        """宽度锁死为设计宽度（内容再长也不撑宽），高度跟随内容。"""
        self.root.update_idletasks()
        size = (self.W, self.root.winfo_reqheight())
        if size != self._size:
            self._size = size
            self.root.geometry("%dx%d" % size)

    def _fit_text(self, lbl, text, max_px):
        """文字超出 max_px 时截断加省略号。"""
        import tkinter.font as tkfont
        f = tkfont.Font(font=lbl.cget("font"))
        if f.measure(text) > max_px:
            while text and f.measure(text + "…") > max_px:
                text = text[:-1]
            text += "…"
        lbl.config(text=text)

    def _hero_text_px(self):
        """出口城市/IP 行可用宽度 = 窗口宽 - 左右边距 - 国家码方块 - 右侧来源框。"""
        self.root.update_idletasks()
        px = self.root.winfo_fpixels
        right = max(self.src_lbl.winfo_reqwidth(), self.hist_btn.winfo_reqwidth())
        return int(self.W - px(P(24)) - self.cc_lbl.winfo_reqwidth() - px(P(10)) - right - px(P(8)))

    # ----- 渲染 -----
    def _set_alert(self, al):
        if not al:
            self.alert.pack_forget()
            return
        lvl, text = al
        self.alert.config(text=text, fg=T[lvl], bg=T[lvl + "_bg"])
        self.alert.pack(fill="x", padx=P(10), pady=(P(8), P(0)), before=self.hero)

    def _render_idle(self):
        p = core.proxy_info()
        self.px_lbl.config(text=self._proxy_text(p))
        last = self.cfg.get("last_exit")
        self._hero_ip = last
        if last:
            self.cc_lbl.config(text=last.get("cc") or "··", fg=T["fg_dim"])
            self._set_city(hero_text(last), fg=T["fg_dim"])
            self.ipl_lbl.config(text=f"上次结果 · {last.get('ip', '')}")
        else:
            self._set_city("点「测试」开始", fg=T["fg_dim"], font=(UI, 10))
        self.src_lbl.config(text="—")
        for dot, ms in self.site_w:
            dot.config(fg=T["muted"])
            ms.config(text="")
        self.foot_r.config(text="尚未测试")
        self._shown_latest = bool(last)
        self._refresh_hist_btn()

    @staticmethod
    def _proxy_text(p):
        if not p["enabled"] or not p["server"]:
            return "无代理"
        return ":" + p["server"].split(";")[0].rsplit(":", 1)[-1]

    def start_test(self):
        if self.running:
            return
        self.running = True
        self.t0 = time.perf_counter()
        self.state = {"proxy": None, "sites": [None] * len(core.TARGETS), "ip": None, "ip_done": False}
        self._hero_ip = None
        self._shown_latest = False
        self._refresh_hist_btn()
        self._running_bars = set()
        self.btn_go.config(bg=T["btn_bg"], fg=T["bad"], cursor="hand2", text="■ 停止 0s")
        self._set_alert(None)
        self.was_lbl.pack_forget()
        self.cc_lbl.config(text="··", fg=T["muted"])
        self._set_city("█" * 9, fg=T["sep"], font=(MONO, 11))   # 占位条，_pulse 里呼吸
        self.ipl_lbl.config(text="█" * 22, fg=T["sep"])
        self.src_lbl.config(text="0/3 源", fg=T["fg_dim"], highlightbackground=T["sep"])
        for dot, ms in self.site_w:
            dot.config(fg=T["muted"])
            ms.config(text="…", fg=T["fg_dim"])
        for key in self.speed_w:
            self._set_speed(key, "wait")
        self.foot_l.config(text="国内 —")
        self.foot_r.config(text="测试中…")
        self._fit_size()
        # 每次测试用新队列：停止后旧线程的迟到结果投进旧队列，没人消费，不会污染界面
        self.q = queue.Queue()
        self.cancel = threading.Event()
        threading.Thread(target=run_test, args=(self.q, self.cancel), daemon=True).start()

    def _set_speed(self, key, v):
        note, val, unit, bar = self.speed_w[key]
        bar.delete("all")
        self._speed_state[key] = v
        running_bars = getattr(self, "_running_bars", set())
        running_bars.discard(key)
        if v == "run":
            running_bars.add(key)
        note.config(text="")
        if v == "wait":
            val.config(text="排队中", fg=T["muted"], font=(UI, 10))
            unit.config(text="")
        elif v == "run":
            val.config(text="…", fg=T["muted"], font=(MONO, 13, "bold"))
            note.config(text="测量中")
            unit.config(text="")
        elif v == "stopped":
            val.config(text="—", fg=T["muted"], font=(MONO, 13, "bold"))
            note.config(text="已停止")
            unit.config(text="")
        elif v == "skip":
            val.config(text="—", fg=T["bad"], font=(MONO, 13, "bold"))
            note.config(text="已跳过")
            unit.config(text="")
        else:
            lvl = speed_level(v)
            val.config(text="失败" if v is None else f"{v:g}", fg=T[lvl] if lvl != "ok" else T["fg"],
                       font=(MONO, 13, "bold"))
            unit.config(text="" if v is None else "Mbps")
            if v:
                bar.update_idletasks()
                w = bar.winfo_width() * min(1.0, v / SPEED_FULL)
                bar.create_rectangle(0, 0, w, bar.winfo_height(), fill=T["accent"] if lvl == "ok" else T[lvl], width=0)

    def _on_event(self, kind, i, res):
        s = self.state
        if kind == "proxy":
            s["proxy"] = res
            self.px_lbl.config(text=self._proxy_text(res))
        elif kind == "site":
            s["sites"][i] = res
            lvl, text = site_level(res)
            dot, ms = self.site_w[i]
            dot.config(fg=T[lvl])
            ms.config(text=text, fg=T["fg_dim"] if lvl == "ok" else T[lvl])
        elif kind == "ip":
            s["ip"] = res
            s["ip_done"] = True
            self.city_lbl.config(font=(UI, 13, "bold"))
            self.ipl_lbl.config(fg=T["fg_dim"])   # 占位条呼吸色复位
            self._render_ip(res)
        elif kind == "cn":
            self.foot_l.config(text=f"国内 {res['desc']} · {res['ip']}" if res else "国内 查询失败")
        elif kind in ("down", "up"):
            self._set_speed(kind, res)
        elif kind == "skip_speed":
            self._set_speed("down", "skip")
            self._set_speed("up", "skip")
        elif kind == "done":
            self._finish()
            return
        if kind in ("proxy", "site", "ip"):
            self._update_alert()

    def _render_ip(self, ip):
        self._hero_ip = ip
        if not ip:
            self.cc_lbl.config(text="!", fg=T["bad"])
            self._set_city("境外出口查不到", fg=T["bad"])
            self.ipl_lbl.config(text="3 个定位接口都失败")
            self.src_lbl.config(text="0/3 源")
            return
        last = self.cfg.get("last_exit")
        changed = exit_changed(last, ip)
        self.cc_lbl.config(text=ip["cc"] or "?", fg=T["fg"])
        if changed:
            self.was_lbl.config(text=last.get("city") or last.get("country") or "")
            self.was_lbl.pack(side="left", padx=(P(0), P(4)), pady=(P(3), P(0)), before=self.city_box)
        disagree = bool(ip.get("alt_cities"))
        self.src_lbl.config(text="已切换" if changed else ("源不一致" if disagree else f"{ip['sources']}/3 源"),
                            fg=T["warn"] if changed or disagree else T["fg_dim"],
                            highlightbackground=T["warn"] if changed or disagree else T["sep"])
        self._fit_text(self.ipl_lbl, f"{ip['ip']} · {ip['org'] or ''}".rstrip(" ·"), self._hero_text_px())
        self._set_city(hero_text(ip), fg=T["bad"] if ip["cc"] == "CN" else T["fg"])
        self.state["changed_from"] = last if changed else None

    def _update_alert(self):
        s = self.state
        al = diagnose(s["proxy"], s["sites"], s["ip"])
        if not al and s.get("changed_from"):
            last, ip = s["changed_from"], s["ip"]
            al = ("warn", f"出口变了　上次 {city_text(last)} → 这次 {city_text(ip)}")
        self._set_alert(al)

    def _finish(self, stopped=False):
        """正常结束或被停止：已查到的出口照常记入历史，按钮复原。"""
        self.running = False
        ip = self.state["ip"]
        if ip and ip.get("cc"):
            self.cfg["last_exit"] = {k: ip.get(k) for k in EXIT_KEYS}
            self.cfg["exit_history"] = push_history(self.cfg.get("exit_history"), ip,
                                                    time.strftime("%Y-%m-%d %H:%M"))
            save_config(self.cfg)
            self._shown_latest = True
        self._refresh_hist_btn()
        sec = time.perf_counter() - self.t0
        tail = "已停止" if stopped else f"{sec:.0f}s"
        self.foot_r.config(text=f"{time.strftime('%H:%M')} · {tail}")
        self.btn_go.config(bg=T["accent"], fg="#FFFFFF", cursor="hand2", text="测试")

    def stop_test(self):
        """点「停止」：通知后台断开测速，没出结果的项标「已停止」，已有结果保留。"""
        if not self.running:
            return
        self.cancel.set()
        self.q = queue.Queue()
        if self._peek is not None:      # 正按着「历史」时不会走到这里（按钮不同），保险起见先复原
            self._peek_end(None)
        for i, (dot, ms) in enumerate(self.site_w):
            if self.state["sites"][i] is None:
                dot.config(fg=T["muted"])
                ms.config(text="—", fg=T["muted"])
        if not self.state["ip_done"]:
            self.cc_lbl.config(text="··", fg=T["muted"])
            self._set_city("已停止", fg=T["fg_dim"], font=(UI, 10))
            self.ipl_lbl.config(text="", fg=T["fg_dim"])
            self.src_lbl.config(text="—")
        for key, v in self._speed_state.items():
            if v in ("wait", "run"):
                self._set_speed(key, "stopped")
        if self.foot_l.cget("text") == "国内 —":
            self.foot_l.config(text="国内 —（已停止）")
        self._finish(stopped=True)
        self._fit_size()

    # ----- 出口文字：放不下就横向滚动 -----
    def _set_city(self, text, **kw):
        self._city_text = text
        self.city_lbl.config(text=text, **kw)
        self._marquee_reset()

    def _marquee_reset(self):
        import tkinter.font as tkfont
        if self._marq_job:
            self.root.after_cancel(self._marq_job)
            self._marq_job = None
        self._marq_x = 0
        # 宽度显式算出（与 IP 行截断同一口径），不能交给 grid 自动分配：
        # 实测自动分配会让裁剪框伸到右侧「N/3 源」框底下，滚动文字从框下穿过
        box_w = self._hero_text_px()
        if self.was_lbl.winfo_ismapped() or self.was_lbl.winfo_manager():
            box_w -= self.was_lbl.winfo_reqwidth() + int(self.root.winfo_fpixels(P(4)))
        box_w = max(box_w, 1)
        self.city_box.config(width=box_w, height=self.city_lbl.winfo_reqheight())
        f = tkfont.Font(font=self.city_lbl.cget("font"))
        if f.measure(self._city_text) > box_w:
            self.city_lbl.config(text=self._city_text + MARQUEE_GAP + self._city_text)
            self._marq_period = f.measure(self._city_text + MARQUEE_GAP)
            self._marq_job = self.root.after(MARQUEE_PAUSE_MS, self._marquee_step)
        else:
            self.city_lbl.config(text=self._city_text)
        self.city_lbl.place(x=0, rely=0.5, anchor="w")

    def _marquee_step(self):
        self._marq_x -= 1
        delay = MARQUEE_STEP_MS
        if -self._marq_x >= self._marq_period:   # 第二份文字移到开头位置 → 无缝回到 0
            self._marq_x = 0
            delay = MARQUEE_PAUSE_MS
        self.city_lbl.place(x=self._marq_x, rely=0.5, anchor="w")
        self._marq_job = self.root.after(delay, self._marquee_step)

    def _refresh_hist_btn(self):
        has = prev_exit(self.cfg.get("exit_history"), self._shown_latest) is not None
        self.hist_btn.config(fg=T["fg_dim"] if has else T["muted"], cursor="hand2" if has else "arrow")

    def _peek_start(self, _e):
        prev = prev_exit(self.cfg.get("exit_history"), self._shown_latest)
        if prev is None or self._peek is not None:
            return
        snap = {}
        for w in (self.cc_lbl, self.ipl_lbl, self.src_lbl):
            snap[w] = {k: w.cget(k) for k in ("text", "fg", "font", "highlightbackground")}
        snap["city"] = (self._city_text, self.city_lbl.cget("fg"), self.city_lbl.cget("font"))
        snap["was"] = self.was_lbl.winfo_ismapped()
        self._peek = snap
        self.was_lbl.pack_forget()
        dim = T["fg_dim"]
        self.cc_lbl.config(text=prev.get("cc") or "?", fg=dim)
        self.src_lbl.config(text=short_time(prev.get("time"), time.strftime("%Y-%m-%d")),
                            fg=dim, highlightbackground=T["sep"])
        self.hist_btn.config(fg=T["fg"], highlightbackground=T["fg_dim"])
        avail = self._hero_text_px()
        self._set_city(hero_text(prev), fg=dim, font=(UI, 13, "bold"))
        self._fit_text(self.ipl_lbl, f"{prev.get('ip') or ''} · {prev.get('org') or ''}".strip(" ·"), avail)
        self.ipl_lbl.config(fg=dim)

    def _peek_end(self, _e):
        snap, self._peek = self._peek, None
        if snap is None:
            return
        was = snap.pop("was")
        text, fg, font = snap.pop("city")
        self._set_city(text, fg=fg, font=font)
        for w, cfg in snap.items():
            w.config(**cfg)
        if was:
            self.was_lbl.pack(side="left", padx=(0, P(4)), pady=(P(3), 0), before=self.city_box)
            self._marquee_reset()   # 旧城市占了宽度，重算裁剪框
        self.hist_btn.config(highlightbackground=T["sep"])
        self._refresh_hist_btn()

    def _pulse(self):
        """等待动效：没出结果的圆点、城市/IP 占位条、测量中的速度条按 1.1s 周期呼吸。"""
        import math
        k = (1 + math.cos(2 * math.pi * (time.perf_counter() - self.t0) / PULSE_S)) / 2
        op = PULSE_MIN + (1 - PULSE_MIN) * k
        dot_c = blend(T["bg"], T["muted"], op)
        sk_c = blend(T["bg"], T["sep"], op)
        for i, (dot, _ms) in enumerate(self.site_w):
            if self.state["sites"][i] is None:
                dot.config(fg=dot_c)
        if not self.state["ip_done"]:
            self.city_lbl.config(fg=sk_c)
            self.ipl_lbl.config(fg=sk_c)
        for key in self._running_bars:
            bar = self.speed_w[key][3]
            bar.delete("all")
            # 底色就是 sep，用 sep 呼吸看不出来（设计稿同样问题），改为暗绿色呼吸
            bar.create_rectangle(0, 0, bar.winfo_width(), bar.winfo_height(),
                                 fill=blend(T["sep"], T["accent"], 0.55 * op), width=0)

    def _poll(self):
        got = False
        try:
            while self._peek is None:  # 按住「历史」时结果先排队，松开再显示，避免画面跳
                self._on_event(*self.q.get_nowait())
                got = True
        except queue.Empty:
            pass
        if got:
            self._fit_size()
        if self.running:
            self.btn_go.config(text=f"■ 停止 {int(time.perf_counter() - self.t0)}s")
            if self._peek is None:
                self._pulse()
        try:
            while True:
                act = self._tray_q.get_nowait()
                if act == "show":
                    self.restore()
                elif act == "exit":
                    self.close()
                    return
        except queue.Empty:
            pass
        self.root.after(POLL_MS, self._poll)

    # ----- 窗口动作 -----
    def _drag_start(self, e):
        self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def _drag_move(self, e):
        self.root.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def _drag_end(self, _e):
        self.cfg["pos_x"], self.cfg["pos_y"] = self.root.winfo_x(), self.root.winfo_y()
        save_config(self.cfg)

    def toggle_pin(self):
        self.cfg["always_on_top"] = not self.cfg["always_on_top"]
        self.root.attributes("-topmost", self.cfg["always_on_top"])
        self._refresh_pin()
        save_config(self.cfg)

    def minimize(self):
        if self.tray is None:
            return
        self.root.withdraw()

    def restore(self):
        self.root.deiconify()
        self.root.overrideredirect(True)  # 部分环境 deiconify 会丢无边框，重置
        self.root.attributes("-topmost", self.cfg["always_on_top"])
        self.root.lift()

    # ----- 出口地图浮窗 -----
    def _build_map_win(self):
        w = tk.Toplevel(self.root, bg=T["bg"])
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.withdraw()
        bar = tk.Frame(w, bg=T["title_bg"], height=P(TITLEBAR_H))
        bar.pack(fill="x")
        bar.pack_propagate(False)
        self.map_title = self._lbl(bar, "", font=(UI, 9, "bold"), bg=T["title_bg"])
        self.map_title.pack(side="left", padx=(P(10), 0))
        self._lbl(bar, "松开收起", fg=T["fg_dim"], font=(UI, 8), bg=T["title_bg"]).pack(side="right", padx=(0, P(10)))
        cap = tk.Frame(w, bg=T["bg"])
        cap.pack(side="bottom", fill="x", padx=P(12), pady=(P(4), P(10)))
        # 两行：州/国家名 + 城市·坐标。设计稿是一行左右分，实际 300px 宽放不下会叠在一起
        self.map_label = self._lbl(cap, "", font=(UI, 10, "bold"), anchor="w")
        self.map_label.pack(fill="x")
        self.map_sub = self._lbl(cap, "", fg=T["fg_dim"], font=(MONO, 8), anchor="w")
        self.map_sub.pack(fill="x")
        self.map_cv = tk.Canvas(w, bg=T["bg"], highlightthickness=0, bd=0)
        self.map_cv.pack(fill="both", expand=True, padx=P(10), pady=(P(10), 0))
        self.map_win = w

    def _map_show(self, _e):
        spec = geomap.map_spec(self._hero_ip)
        if spec is None or self._peek is not None:
            return
        if self.map_win is None:
            self._build_map_win()
        self.cc_lbl.config(highlightbackground=T["accent"], bg=T["cc_held"])
        r, w = self.root, self.map_win
        r.update_idletasks()
        ww, wh = r.winfo_width(), r.winfo_height()
        gap = int(r.winfo_fpixels(P(MAP_GAP)))
        x = r.winfo_rootx() - ww - gap
        if x < r.winfo_vrootx():                 # 左边放不下（贴屏幕左缘）→ 放右边
            x = r.winfo_rootx() + ww + gap
        self.map_title.config(text=spec["title"])
        self.map_label.config(text=spec["label"])
        self.map_sub.config(text=spec["sub"])
        w.geometry(f"{ww}x{wh}+{x}+{r.winfo_rooty()}")
        w.deiconify()
        w.lift()
        w.update_idletasks()
        self._draw_map(spec)

    def _draw_map(self, spec):
        cv = self.map_cv
        cv.delete("all")
        cw, ch = cv.winfo_width(), cv.winfo_height()
        sw, sh = spec["size"]
        k = min(cw / sw, ch / sh)
        ox, oy = (cw - sw * k) / 2, (ch - sh * k) / 2

        def tf(ring):
            return [v * k + (ox if i % 2 == 0 else oy) for i, v in enumerate(ring)]
        on_fill = blend(T["bg"], T["accent"], MAP_ON_ALPHA)
        for sid, rings in spec["shapes"].items():
            if sid == spec["on"]:
                continue
            for ring in rings:
                cv.create_polygon(tf(ring), fill=T["map_land"], outline=T["map_edge"], width=1)
        for ring in spec["outline"]:
            cv.create_line(tf(ring), fill=T["map_outline"], width=1)
        # 点亮的画在最上层。世界图里多数国家只有几像素（日本、香港），描边加粗到 2px 才看得出
        on_w = 2 if spec["kind"] == "world" else 1
        for ring in spec["shapes"].get(spec["on"], []) if spec["on"] else []:
            cv.create_polygon(tf(ring), fill=on_fill, outline=T["accent"] if on_w == 2 else T["map_on_edge"],
                              width=on_w)
        self._ring_item = None
        if spec["dot"]:
            x, y = spec["dot"][0] * k + ox, spec["dot"][1] * k + oy
            self._dot_xy = (x, y)
            self._ring_item = cv.create_oval(x, y, x, y, outline="#FFFFFF", width=1)
            d = int(self.root.winfo_fpixels(P(4)))
            cv.create_oval(x - d, y - d, x + d, y + d, fill="#FFFFFF", outline=T["bg"], width=1)
            self._ring_t0 = time.perf_counter()
            self._ring_step()

    def _ring_step(self):
        """白点扩散圈：半径 0.4→2.2 倍、颜色由白渐隐到底色，1.6s 一轮（同设计稿）。"""
        if self._ring_item is None:
            return
        p = ((time.perf_counter() - self._ring_t0) % RING_S) / RING_S
        base = self.root.winfo_fpixels(P(8))
        rr = base * (0.4 + 1.8 * p)
        x, y = self._dot_xy
        self.map_cv.coords(self._ring_item, x - rr, y - rr, x + rr, y + rr)
        self.map_cv.itemconfig(self._ring_item, outline=blend("#FFFFFF", T["bg"], p))
        self._ring_job = self.root.after(40, self._ring_step)

    def _map_hide(self, _e=None):
        if self._ring_job:
            self.root.after_cancel(self._ring_job)
            self._ring_job = None
        self._ring_item = None
        self.cc_lbl.config(highlightbackground=T["title_bg"], bg=T["title_bg"])
        if self.map_win is not None:
            self.map_win.withdraw()

    def close(self):
        self._map_hide()
        self.cfg["pos_x"], self.cfg["pos_y"] = self.root.winfo_x(), self.root.winfo_y()
        save_config(self.cfg)
        if self.tray:
            self.tray.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if IS_WINDOWS:
        try:  # 高 DPI 下不模糊
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            pass
    app = App()
    if "--autotest" in sys.argv:  # 启动即测一次，并把窗口矩形写到 _geom.txt（调试截图用）
        app.root.after(500, app.start_test)
        app.root.attributes("-topmost", True)  # 截图时不被遮挡；不写配置

        def _dump_geom():
            r = app.root
            with open("_geom.txt", "w") as f:
                f.write(f"{r.winfo_rootx()} {r.winfo_rooty()} {r.winfo_width()} {r.winfo_height()}")
            r.after(1000, _dump_geom)
        app.root.after(800, _dump_geom)
    app.run()
