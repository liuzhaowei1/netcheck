# -*- coding: utf-8 -*-
"""生成 assets/app.ico + assets/app.svg（Pulse 家族图标，方向 A「釉光」）。打包前运行一次即可。

单一源＝下面的 SVG（同时写出 assets/app.svg 便于查看）。
用 Edge 无头逐尺寸渲染成透明 PNG（≤32px 换粗线、去细节的小尺寸版），再打包成 PNG 条目的 ICO。
纯标准库；需要本机装有 Edge。Edge 用临时 user-data-dir，不碰日常配置。
CC Pulse / NetCheck 同族图标，两个仓库的 make_icon.py 除 KIND 外逐字相同。
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile

KIND = "net"                     # cc / net / sys
SIZES = (16, 20, 24, 30, 32, 40, 48, 64, 96, 128, 256)
SMALL_MAX = 32                   # ≤ 此尺寸用小尺寸版
EDGE = (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")

TILE = {"cc": ("#FFB68C", "#DC5F34"), "net": ("#6FE3F4", "#2563EB"), "sys": ("#C6B3FF", "#6A3BF5")}
SQUIRCLE = "M128 8C214 8 248 42 248 128S214 248 128 248 8 214 8 128 42 8 128 8Z"
RIM = "M128 9.5C213 9.5 246.5 43 246.5 128S213 246.5 128 246.5 9.5 213 9.5 128 43 9.5 128 9.5Z"


def glyph(kind, small):
    """返回 (描线部分, 其余部分)。描线部分会额外画一层投影。"""
    sw = 30 if small else 20
    if kind == "cc":
        r = 17 if small else 13
        line = f'<path d="M58 92L98 128L58 164" stroke-width="{sw}"/>'
        rest = "".join(f'<circle cx="{x}" cy="158" r="{r}" fill="{c}"/>'
                       for x, c in ((132, "#FFC34D"), (164, "#FF6B5E"), (196, "#4ADE80")))
    elif kind == "net":
        line = f'<circle cx="120" cy="136" r="62" stroke-width="{sw}"/>'
        rest = "" if small else (
            '<g fill="none" stroke="#FFF" stroke-width="10">'
            '<ellipse cx="120" cy="136" rx="26" ry="62"/><path d="M58 136H182"/></g>'
            '<circle cx="180" cy="78" r="30" fill="none" stroke="#FFF" stroke-width="6" opacity="0.4"/>')
        rest += f'<circle cx="180" cy="78" r="{22 if small else 15}" fill="#FFF"/>'
    else:
        line = f'<path d="M84 178V142M128 178V78M172 178V116" stroke-width="{36 if small else 28}"/>'
        rest = f'<circle cx="172" cy="74" r="{22 if small else 15}" fill="#FFF"/>'
    return line, rest


def svg(kind, small, px=256):
    a, b = TILE[kind]
    line, rest = glyph(kind, small)
    cap = 'fill="none" stroke-linecap="round" stroke-linejoin="round"'
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{px}" height="{px}" viewBox="0 0 256 256">'
        '<defs>'
        f'<linearGradient id="t" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{a}"/>'
        f'<stop offset="1" stop-color="{b}"/></linearGradient>'
        '<linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#FFF" stop-opacity="0.38"/>'
        '<stop offset="0.5" stop-color="#FFF" stop-opacity="0"/></linearGradient>'
        '<filter id="s" filterUnits="userSpaceOnUse" x="0" y="0" width="256" height="256"><feGaussianBlur stdDeviation="6"/></filter>'
        '</defs>'
        f'<path d="{SQUIRCLE}" fill="url(#t)"/><path d="{SQUIRCLE}" fill="url(#g)"/>'
        f'<path d="{RIM}" fill="none" stroke="rgba(255,255,255,0.30)" stroke-width="3"/>'
        f'<g {cap} stroke="#1A0A00" opacity="0.22" filter="url(#s)" transform="translate(0 6)">{line}</g>'
        f'<g {cap} stroke="#FFF">{line}</g>{rest}</svg>')


def png_size(data):
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError("不是 PNG")
    return struct.unpack(">II", data[16:24])


def render(edge, tmp, size):
    html = os.path.join(tmp, f"i{size}.html")
    out = os.path.join(tmp, f"i{size}.png")
    with open(html, "w", encoding="utf-8") as f:
        f.write('<!doctype html><html><body style="margin:0;background:transparent">'
                + svg(KIND, size <= SMALL_MAX, size) + "</body></html>")
    subprocess.run([edge, "--headless", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                    f"--user-data-dir={os.path.join(tmp, 'prof')}", "--force-device-scale-factor=1",
                    "--default-background-color=00000000", f"--window-size={size},{size}",
                    f"--screenshot={out}", "file:///" + html.replace("\\", "/")],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, check=False)
    with open(out, "rb") as f:
        data = f.read()
    if png_size(data) != (size, size):
        raise RuntimeError(f"{size}px 渲染尺寸不对：{png_size(data)}")
    return data


def main():
    edge = next((p for p in EDGE if os.path.exists(p)), None)
    if not edge:
        sys.exit("找不到 Edge")
    here = os.path.dirname(os.path.abspath(__file__))
    assets = os.path.join(here, "assets")
    if not os.path.isdir(assets):                       # CC Pulse 的脚本在 tools/ 下
        assets = os.path.join(os.path.dirname(here), "assets")
    tmp = tempfile.mkdtemp(prefix="pulseicon_")
    try:
        pngs = [render(edge, tmp, s) for s in SIZES]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    out = struct.pack("<HHH", 0, 1, len(SIZES))
    off = 6 + 16 * len(SIZES)
    for s, d in zip(SIZES, pngs):
        out += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(d), off)
        off += len(d)
    out += b"".join(pngs)
    with open(os.path.join(assets, "app.ico"), "wb") as f:
        f.write(out)
    with open(os.path.join(assets, "app.svg"), "w", encoding="utf-8") as f:
        f.write(svg(KIND, False))
    print(os.path.join(assets, "app.ico"), len(out), "bytes,", len(SIZES), "sizes")


if __name__ == "__main__":
    main()
