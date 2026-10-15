# -*- coding: utf-8 -*-
"""
NetCheck — Windows 系统托盘图标（移植自 CC Pulse）（纯 ctypes 调 Shell_NotifyIcon，零第三方依赖）
==============================================================================

最小化时把主面板 withdraw 隐藏，在系统通知区域放一个托盘图标：
  · 左键单击 / 双击 → 触发 on_show（恢复主面板）
  · 右键          → 弹出菜单「显示主面板 / 退出」

实现要点：
  · 独立 daemon 线程里创建一个隐藏窗口 + 跑 GetMessage 消息循环，
    托盘回调消息（WM_TRAY）在该线程的 WndProc 中处理。
  · on_show / on_exit 在托盘线程被调用——调用方应把它们接到线程安全队列，
    再由 tkinter 主线程消费（tkinter 非线程安全）。
  · 托盘图标由调用方传入像素列表 icon=(pixels, gw, gh)，程序内生成 HICON（无图片资源）。
  · 非 Windows 平台：TrayIcon.supported=False，所有方法 no-op（便于 WSL 下静态检查）。
"""
import sys
import threading

IS_WIN = sys.platform == "win32"

# ── Win32 消息 / 常量 ──
WM_NULL = 0x0000
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04

TPM_RIGHTBUTTON = 0x0002
IDC_ARROW = 32512

ID_SHOW = 1
ID_EXIT = 2


if IS_WIN:
    import ctypes
    import ctypes.wintypes as wintypes
    from ctypes import (Structure, POINTER, byref, sizeof, memmove,
                        c_int, c_void_p, c_uint32, c_byte, c_size_t, c_wchar,
                        WINFUNCTYPE)

    LRESULT = ctypes.c_ssize_t
    DWORD = wintypes.DWORD
    WORD = wintypes.WORD
    BYTE = wintypes.BYTE
    LONG = wintypes.LONG
    UINT = wintypes.UINT
    BOOL = wintypes.BOOL
    HWND = wintypes.HWND
    HICON = wintypes.HICON
    HMENU = wintypes.HMENU
    HDC = wintypes.HDC
    HBITMAP = wintypes.HBITMAP
    HANDLE = wintypes.HANDLE
    HINSTANCE = wintypes.HINSTANCE
    HMODULE = wintypes.HMODULE
    LPCWSTR = wintypes.LPCWSTR
    WPARAM = wintypes.WPARAM
    LPARAM = wintypes.LPARAM
    ATOM = wintypes.ATOM

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    shell32 = ctypes.windll.shell32
    gdi32 = ctypes.windll.gdi32

    WNDPROC = WINFUNCTYPE(LRESULT, HWND, UINT, WPARAM, LPARAM)

    class WNDCLASS(Structure):
        _fields_ = [
            ("style", UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", c_int),
            ("cbWndExtra", c_int),
            ("hInstance", HINSTANCE),
            ("hIcon", HICON),
            ("hCursor", HANDLE),
            ("hbrBackground", HANDLE),
            ("lpszMenuName", LPCWSTR),
            ("lpszClassName", LPCWSTR),
        ]

    class POINT(Structure):
        _fields_ = [("x", LONG), ("y", LONG)]

    class MSG(Structure):
        _fields_ = [
            ("hwnd", HWND), ("message", UINT),
            ("wParam", WPARAM), ("lParam", LPARAM),
            ("time", DWORD), ("pt", POINT),
        ]

    class GUID(Structure):
        _fields_ = [("Data1", DWORD), ("Data2", WORD),
                    ("Data3", WORD), ("Data4", BYTE * 8)]

    class NOTIFYICONDATA(Structure):
        _fields_ = [
            ("cbSize", DWORD),
            ("hWnd", HWND),
            ("uID", UINT),
            ("uFlags", UINT),
            ("uCallbackMessage", UINT),
            ("hIcon", HICON),
            ("szTip", c_wchar * 128),
            ("dwState", DWORD),
            ("dwStateMask", DWORD),
            ("szInfo", c_wchar * 256),
            ("uVersion", UINT),
            ("szInfoTitle", c_wchar * 64),
            ("dwInfoFlags", DWORD),
            ("guidItem", GUID),
            ("hBalloonIcon", HICON),
        ]

    class BITMAPINFOHEADER(Structure):
        _fields_ = [
            ("biSize", DWORD), ("biWidth", LONG), ("biHeight", LONG),
            ("biPlanes", WORD), ("biBitCount", WORD), ("biCompression", DWORD),
            ("biSizeImage", DWORD), ("biXPelsPerMeter", LONG),
            ("biYPelsPerMeter", LONG), ("biClrUsed", DWORD),
            ("biClrImportant", DWORD),
        ]

    class ICONINFO(Structure):
        _fields_ = [("fIcon", BOOL), ("xHotspot", DWORD), ("yHotspot", DWORD),
                    ("hbmMask", HBITMAP), ("hbmColor", HBITMAP)]

    # ── 函数原型（务必设 restype/argtypes，否则 64 位下句柄会被截断）──
    user32.DefWindowProcW.restype = LRESULT
    user32.DefWindowProcW.argtypes = [HWND, UINT, WPARAM, LPARAM]
    user32.RegisterClassW.restype = ATOM
    user32.RegisterClassW.argtypes = [POINTER(WNDCLASS)]
    user32.CreateWindowExW.restype = HWND
    user32.CreateWindowExW.argtypes = [DWORD, LPCWSTR, LPCWSTR, DWORD,
                                       c_int, c_int, c_int, c_int,
                                       HWND, HMENU, HINSTANCE, c_void_p]
    user32.DestroyWindow.argtypes = [HWND]
    user32.GetMessageW.restype = c_int
    user32.GetMessageW.argtypes = [POINTER(MSG), HWND, UINT, UINT]
    user32.TranslateMessage.argtypes = [POINTER(MSG)]
    user32.DispatchMessageW.restype = LRESULT
    user32.DispatchMessageW.argtypes = [POINTER(MSG)]
    user32.PostMessageW.restype = BOOL
    user32.PostMessageW.argtypes = [HWND, UINT, WPARAM, LPARAM]
    user32.PostQuitMessage.argtypes = [c_int]
    user32.LoadCursorW.restype = HANDLE
    user32.LoadCursorW.argtypes = [HINSTANCE, LPCWSTR]
    user32.CreatePopupMenu.restype = HMENU
    user32.AppendMenuW.restype = BOOL
    user32.AppendMenuW.argtypes = [HMENU, UINT, c_size_t, LPCWSTR]
    user32.TrackPopupMenu.restype = BOOL
    user32.TrackPopupMenu.argtypes = [HMENU, UINT, c_int, c_int, c_int,
                                      HWND, c_void_p]
    user32.DestroyMenu.argtypes = [HMENU]
    user32.GetCursorPos.restype = BOOL
    user32.GetCursorPos.argtypes = [POINTER(POINT)]
    user32.SetForegroundWindow.restype = BOOL
    user32.SetForegroundWindow.argtypes = [HWND]
    user32.GetDC.restype = HDC
    user32.GetDC.argtypes = [HWND]
    user32.ReleaseDC.argtypes = [HWND, HDC]
    user32.CreateIconIndirect.restype = HICON
    user32.CreateIconIndirect.argtypes = [POINTER(ICONINFO)]
    user32.DestroyIcon.restype = BOOL
    user32.DestroyIcon.argtypes = [HICON]

    kernel32.GetModuleHandleW.restype = HMODULE
    kernel32.GetModuleHandleW.argtypes = [LPCWSTR]

    shell32.Shell_NotifyIconW.restype = BOOL
    shell32.Shell_NotifyIconW.argtypes = [DWORD, POINTER(NOTIFYICONDATA)]

    gdi32.CreateDIBSection.restype = HBITMAP
    gdi32.CreateDIBSection.argtypes = [HDC, POINTER(BITMAPINFOHEADER), UINT,
                                       POINTER(c_void_p), HANDLE, DWORD]
    gdi32.CreateBitmap.restype = HBITMAP
    gdi32.CreateBitmap.argtypes = [c_int, c_int, UINT, UINT, c_void_p]
    gdi32.DeleteObject.restype = BOOL
    gdi32.DeleteObject.argtypes = [HANDLE]

    def _MAKEINTRESOURCE(i):
        return ctypes.cast(ctypes.c_void_p(i & 0xFFFF), LPCWSTR)

    def _make_hicon(pixels, gw, gh, size=32):
        """把 (gx,gy,'#RRGGBB') 像素列表渲染成 size×size 的 32bpp ARGB HICON。
        透明处 alpha=0，宠物像素 alpha=255；整数缩放居中。"""
        buf = (c_uint32 * (size * size))()        # 全 0 = 透明
        scale = max(1, min(size // gw, size // gh))
        dw, dh = gw * scale, gh * scale
        ox, oy = (size - dw) // 2, (size - dh) // 2
        for (gx, gy, col) in pixels:
            try:
                r = int(col[1:3], 16); g = int(col[3:5], 16); b = int(col[5:7], 16)
            except (ValueError, IndexError):
                continue
            argb = (0xFF << 24) | (r << 16) | (g << 8) | b   # 内存序 B,G,R,A
            bx, by = ox + gx * scale, oy + gy * scale
            for sy in range(scale):
                yy = by + sy
                if not (0 <= yy < size):
                    continue
                for sx in range(scale):
                    xx = bx + sx
                    if 0 <= xx < size:
                        buf[yy * size + xx] = argb

        hdc = user32.GetDC(None)
        bmi = BITMAPINFOHEADER()
        bmi.biSize = sizeof(BITMAPINFOHEADER)
        bmi.biWidth = size
        bmi.biHeight = -size            # 负 = top-down
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0           # BI_RGB
        ppv = c_void_p()
        hbm_color = gdi32.CreateDIBSection(hdc, byref(bmi), 0, byref(ppv), None, 0)
        user32.ReleaseDC(None, hdc)
        if not hbm_color or not ppv:
            raise OSError("CreateDIBSection failed")
        memmove(ppv, buf, size * size * 4)

        mask_stride = ((size + 15) // 16) * 2      # 1bpp 行按 WORD 对齐
        mask_bits = (c_byte * (mask_stride * size))()   # 全 0 = 全不透明
        hbm_mask = gdi32.CreateBitmap(size, size, 1, 1, mask_bits)

        ii = ICONINFO()
        ii.fIcon = 1
        ii.hbmMask = hbm_mask
        ii.hbmColor = hbm_color
        hicon = user32.CreateIconIndirect(byref(ii))
        gdi32.DeleteObject(hbm_color)
        gdi32.DeleteObject(hbm_mask)
        if not hicon:
            raise OSError("CreateIconIndirect failed")
        return hicon


class TrayIcon:
    """系统托盘图标控制器。start() 一次（建窗口+消息循环线程）；
    add()/remove() 在最小化/恢复时显隐图标；stop() 退出时收尾。"""

    def __init__(self, tip="NetCheck", on_show=None, on_exit=None, icon=None):
        self.supported = IS_WIN
        self.tip = tip
        self.on_show = on_show or (lambda: None)
        self.on_exit = on_exit or (lambda: None)
        self._hwnd = None
        self._hicon = None
        self._nid = None
        self._added = False
        self._thread = None
        self._ready = threading.Event()
        self._wndproc_cb = None
        self._icon_src = icon
        self._class_name = "NetCheckTrayWnd"
        self._hinst = None

    # ── 生命周期 ──────────────────────────────────────────────────────────────
    def start(self):
        if not self.supported:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(3.0)

    def _run(self):
        try:
            self._hinst = kernel32.GetModuleHandleW(None)
            self._wndproc_cb = WNDPROC(self._wndproc)
            wc = WNDCLASS()
            wc.lpfnWndProc = self._wndproc_cb
            wc.hInstance = self._hinst
            wc.lpszClassName = self._class_name
            wc.hCursor = user32.LoadCursorW(None, _MAKEINTRESOURCE(IDC_ARROW))
            user32.RegisterClassW(byref(wc))     # 已注册则返回 0，忽略
            self._hwnd = user32.CreateWindowExW(
                0, self._class_name, "NetCheck", 0, 0, 0, 0, 0,
                None, None, self._hinst, None)
            try:
                pixels, gw, gh = self._icon_src
                self._hicon = _make_hicon(pixels, gw, gh, 32)
            except Exception:
                self._hicon = user32.LoadCursorW(None, _MAKEINTRESOURCE(32512))
        except Exception:
            self._ready.set()
            return
        self._ready.set()
        msg = MSG()
        while user32.GetMessageW(byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(byref(msg))
            user32.DispatchMessageW(byref(msg))

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAY:
                ev = lparam & 0xFFFF
                if ev in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self.on_show()
                elif ev == WM_RBUTTONUP:
                    self._popup_menu(hwnd)
                return 0
            if msg == WM_COMMAND:
                cid = wparam & 0xFFFF
                if cid == ID_SHOW:
                    self.on_show()
                elif cid == ID_EXIT:
                    self.on_exit()
                return 0
            if msg == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            pass
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _popup_menu(self, hwnd):
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        user32.AppendMenuW(menu, 0, ID_SHOW, "显示主面板")
        user32.AppendMenuW(menu, 0, ID_EXIT, "退出")
        pt = POINT()
        user32.GetCursorPos(byref(pt))
        user32.SetForegroundWindow(hwnd)         # 否则菜单点外面不消失
        user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON, pt.x, pt.y, 0, hwnd, None)
        user32.PostMessageW(hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)

    # ── 显隐图标 ──────────────────────────────────────────────────────────────
    def add(self):
        if not self.supported or self._added or not self._hwnd:
            return
        nid = NOTIFYICONDATA()
        nid.cbSize = sizeof(NOTIFYICONDATA)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self._hicon
        nid.szTip = self.tip
        self._nid = nid
        if shell32.Shell_NotifyIconW(NIM_ADD, byref(nid)):
            self._added = True

    def remove(self):
        if not self.supported or not self._added or not self._nid:
            return
        shell32.Shell_NotifyIconW(NIM_DELETE, byref(self._nid))
        self._added = False

    def stop(self):
        if not self.supported:
            return
        try:
            self.remove()
        except Exception:
            pass
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
        if self._thread:
            self._thread.join(2.0)
        if self._hicon:
            try:
                user32.DestroyIcon(self._hicon)
            except Exception:
                pass
