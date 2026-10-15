# -*- coding: utf-8 -*-
"""生成 assets/app.ico（与托盘图标同一图形），纯标准库写 ICO（32bpp BMP 条目）。打包前运行一次即可。"""
import struct
from netcheck_app import tray_icon_pixels


def bmp_entry(size):
    pixels, gw, gh = tray_icon_pixels(16)
    scale = size // gw
    buf = [[(0, 0, 0, 0)] * size for _ in range(size)]
    for x, y, col in pixels:
        r, g, b = (int(col[i:i + 2], 16) for i in (1, 3, 5))
        for dy in range(scale):
            for dx in range(scale):
                buf[y * scale + dy][x * scale + dx] = (b, g, r, 255)
    hdr = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    rows = b"".join(bytes(c for px in buf[y] for c in px) for y in range(size - 1, -1, -1))
    mask = b"\0" * (((size + 31) // 32) * 4 * size)
    return hdr + rows + mask


sizes = (16, 32, 48, 64)
entries = [bmp_entry(s) for s in sizes]
out = struct.pack("<HHH", 0, 1, len(sizes))
off = 6 + 16 * len(sizes)
for s, e in zip(sizes, entries):
    out += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(e), off)
    off += len(e)
out += b"".join(entries)
with open("assets/app.ico", "wb") as f:
    f.write(out)
print("assets/app.ico", len(out), "bytes")
