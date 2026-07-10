import ctypes
import csv
import os
import queue
import re
import shutil
import socket
import statistics
import struct
import subprocess
import sys
import threading
import time
import traceback
from ctypes import wintypes
from datetime import datetime

if getattr(sys, "frozen", False):
    base_dir = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    os.environ.setdefault("TCL_LIBRARY", os.path.join(base_dir, "tcl", "tcl8.6"))
    os.environ.setdefault("TK_LIBRARY", os.path.join(base_dir, "tcl", "tk8.6"))

import tkinter as tk
from tkinter import filedialog, ttk


# Same-region cloud to WeChat is often only a few ms RTT. This profile is tuned
# for edge-running: late by a few ms can be costly, but a small guard remains to
# avoid obvious early sends.
DEFAULT_CLIENT_PACK_DELAY_MS = 6.63
DEFAULT_RUSH_GUARD_MS = 1.2
DEFAULT_AUTO_REDETECT_BEFORE_S = 5.0
DEFAULT_ADVANCE_CAP_MS = 50.0
DEFAULT_EDGE_TRIM_MS = 2.0
CALIBRATION_LOG = "wechat_send_calibration.csv"
CALIBRATION_DEBUG_LOG = "wechat_send_calibration_debug.log"
SEND_RECORD_LOG = "wechat_send_records.csv"
MAX_AUTO_CLIENT_PACK_MS = 80.0
MIN_AUTO_CLIENT_PACK_MS = 3.0
MESSAGE_PAYLOAD_MIN_LEN = 20
CREATE_NO_WINDOW = 0x08000000

NTP_APPLE = "time.apple.com"
NTP_MS = "time.windows.com"
INTERNAL_NTP_STR = "time1.tencentyun.com,time2.tencentyun.com,time3.tencentyun.com"
TENCENT_CLOUD_INTRANET_NTP = (
    "time1.tencentyun.com,time2.tencentyun.com,time3.tencentyun.com,"
    "time4.tencentyun.com,time5.tencentyun.com"
)
TENCENT_CLOUD_PUBLIC_NTP = (
    "ntp.tencent.com,ntp1.tencent.com,ntp2.tencent.com,"
    "ntp3.tencent.com,ntp4.tencent.com,ntp5.tencent.com"
)

SEND_RECORD_HEADERS = [
    "record_id",
    "created_at",
    "app_version",
    "target_time",
    "target_epoch",
    "input_advance_ms",
    "final_advance_ms",
    "fire_epoch",
    "actual_epoch",
    "local_error_ms",
    "ntp_source_input",
    "tshark_path_input",
    "capture_iface_input",
    "client_pack_ms",
    "rush_guard_ms",
    "advance_cap_ms",
    "edge_trim_ms",
    "lead_ms",
    "auto_detect",
    "auto_before_s",
    "trigger_offset_ms",
    "link_host",
    "link_port",
    "link_best_ms",
    "link_basis_ms",
    "link_basis_sample_count",
    "link_total_samples",
    "link_raw_ms",
    "link_cap_ms",
    "auto_detect_result",
    "ntp_info",
    "wechat_link",
    "outcome",
    "phone_display_time",
    "rank_or_position",
    "note",
]

SEND_RECORD_LABELS = {
    "record_id": "记录ID",
    "created_at": "记录时间",
    "app_version": "软件版本",
    "target_time": "目标时刻",
    "target_epoch": "目标时间戳",
    "input_advance_ms": "启动时提前量(ms)",
    "final_advance_ms": "最终提前量(ms)",
    "fire_epoch": "计划触发时间戳",
    "actual_epoch": "实际触发时间戳",
    "local_error_ms": "本机触发误差(ms)",
    "ntp_source_input": "NTP源输入",
    "tshark_path_input": "TShark路径输入",
    "capture_iface_input": "抓包网卡输入",
    "client_pack_ms": "客户端补偿(ms)",
    "rush_guard_ms": "抢跑保护(ms)",
    "advance_cap_ms": "提前上限(ms)",
    "edge_trim_ms": "贴边修正(ms)",
    "lead_ms": "贴线微调(ms)",
    "auto_detect": "自动临近测向",
    "auto_before_s": "重测提前(s)",
    "trigger_offset_ms": "NTP偏置(ms)",
    "link_host": "测向目标IP",
    "link_port": "测向目标端口",
    "link_best_ms": "TCP最低(ms)",
    "link_basis_ms": "测向基准(ms)",
    "link_basis_sample_count": "基准样本数",
    "link_total_samples": "总样本数",
    "link_raw_ms": "原始提前量(ms)",
    "link_cap_ms": "提前上限生效值(ms)",
    "auto_detect_result": "临近测向结果",
    "ntp_info": "NTP状态",
    "wechat_link": "微信链路",
    "outcome": "复盘结果",
    "phone_display_time": "手机显示时间",
    "rank_or_position": "名次/位置",
    "note": "备注",
}

user32 = ctypes.WinDLL("user32", use_last_error=True)
winmm = ctypes.WinDLL("winmm", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

VK_RETURN = 0x0D
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
SW_RESTORE = 9
HIGH_PRIORITY_CLASS = 0x00000080
THREAD_PRIORITY_HIGHEST = 2
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUTUNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", INPUTUNION)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT


def set_timer_res(ms=1):
    try:
        winmm.timeBeginPeriod(ms)
    except Exception:
        pass


def reset_timer_res(ms=1):
    try:
        winmm.timeEndPeriod(ms)
    except Exception:
        pass


def press_enter_sendinput():
    inputs = (INPUT * 2)()
    inputs[0].type = INPUT_KEYBOARD
    inputs[0].union.ki = KEYBDINPUT(VK_RETURN, 0, 0, 0, 0)
    inputs[1].type = INPUT_KEYBOARD
    inputs[1].union.ki = KEYBDINPUT(VK_RETURN, 0, KEYEVENTF_KEYUP, 0, 0)
    sent = user32.SendInput(2, inputs, ctypes.sizeof(INPUT))
    if sent != 2:
        raise ctypes.WinError(ctypes.get_last_error())


def press_enter_keybd_event():
    user32.keybd_event(VK_RETURN, 0, 0, 0)
    user32.keybd_event(VK_RETURN, 0, KEYEVENTF_KEYUP, 0)


def press_enter():
    try:
        press_enter_sendinput()
    except (OSError, TypeError):
        press_enter_keybd_event()


def corrected_now(offset):
    return time.time() + offset


def output_path(filename):
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), filename)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)


def ntp_once(host, timeout=1.5):
    ip = socket.gethostbyname(host)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        pkt = bytearray(48)
        pkt[0] = 0x23
        t1 = time.time()
        struct.pack_into(
            "!II",
            pkt,
            40,
            int(t1) + 2208988800,
            int((t1 - int(t1)) * (2**32)),
        )
        s.sendto(pkt, (ip, 123))
        data, _ = s.recvfrom(48)
        t4 = time.time()
        t2 = (
            struct.unpack("!I", data[32:36])[0] - 2208988800
        ) + (struct.unpack("!I", data[36:40])[0] / 2**32)
        t3 = (
            struct.unpack("!I", data[40:44])[0] - 2208988800
        ) + (struct.unpack("!I", data[44:48])[0] / 2**32)
        offset = ((t2 - t1) + (t3 - t4)) / 2.0
        rtt = t4 - t1
        return offset, rtt, ip
    finally:
        s.close()


def ntp_sync_fast(server_str, samples=10):
    servers = [s.strip() for s in server_str.split(",") if s.strip()]
    rows = []
    for server in servers:
        for _ in range(samples):
            try:
                offset, rtt, ip = ntp_once(server)
                rows.append({"server": server, "ip": ip, "offset": offset, "rtt": rtt})
            except Exception:
                pass
            time.sleep(0.04)

    if not rows:
        raise RuntimeError("NTP同步超时")

    rows.sort(key=lambda x: x["rtt"])
    best = rows[: max(3, min(6, len(rows)))]
    return {
        "offset": statistics.median(x["offset"] for x in best),
        "rtt_ms": statistics.median(x["rtt"] * 1000 for x in best),
        "server": best[0]["server"],
        "ip": best[0]["ip"],
        "samples": len(rows),
    }


def run_text(cmd):
    return subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL).decode(
        "gbk", "ignore"
    )


def find_wechat_pids():
    pids = []
    for proc_name in ("Weixin.exe", "WeChat.exe"):
        try:
            out = run_text(f'tasklist /FI "IMAGENAME eq {proc_name}" /NH')
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[0].lower() == proc_name.lower():
                    pids.append((proc_name, parts[1]))
        except Exception:
            pass
    return pids


def parse_wechat_endpoints():
    endpoints = []
    for proc_name, pid in find_wechat_pids():
        try:
            out = run_text(f"netstat -ano | findstr {pid}")
        except Exception:
            continue
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 5 or parts[0].upper() != "TCP" or "ESTABLISHED" not in line:
                continue
            remote = parts[2]
            if remote.startswith(("127.", "0.", "[::1]")):
                continue
            if remote.startswith("["):
                continue
            if ":" not in remote:
                continue
            host, port_s = remote.rsplit(":", 1)
            try:
                endpoints.append((host, int(port_s), proc_name, pid))
            except ValueError:
                pass
    seen = set()
    unique = []
    for item in endpoints:
        key = (item[0], item[1])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def tcp_connect_ms(host, port, timeout=0.8):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    s.settimeout(timeout)
    t0 = time.perf_counter()
    try:
        s.connect((host, port))
        return (time.perf_counter() - t0) * 1000
    finally:
        s.close()


def find_tshark():
    for name in ("tshark.exe", "tshark"):
        path = shutil.which(name)
        if path:
            return path
    for path in (
        r"C:\Program Files\Wireshark\tshark.exe",
        r"C:\Program Files (x86)\Wireshark\tshark.exe",
    ):
        if os.path.exists(path):
            return path
    return None


def tshark_interfaces(tshark_path):
    try:
        out = subprocess.check_output(
            [tshark_path, "-D"],
            stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW,
        ).decode("utf-8", "ignore")
    except Exception:
        return []

    indexes = []
    skip_words = ("loopback", "usbpcap", "randpkt", "sshdump", "ciscodump", "udpdump")
    for line in out.splitlines():
        match = re.match(r"\s*(\d+)\.", line)
        if not match:
            continue
        lower = line.lower()
        if any(word in lower for word in skip_words):
            continue
        indexes.append(match.group(1))
    return indexes[:4]


def tshark_interface_listing(tshark_path):
    out = subprocess.check_output(
        [tshark_path, "-D"],
        stderr=subprocess.STDOUT,
        creationflags=CREATE_NO_WINDOW,
    ).decode("utf-8", "ignore")
    return [line.strip() for line in out.splitlines() if line.strip()]


def local_ip_for(host, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, int(port)))
        return s.getsockname()[0]
    finally:
        s.close()


def build_capture_filter(local_ip, endpoints):
    pairs = []
    for host, port, _, _ in endpoints[:12]:
        if ":" in host:
            continue
        pair = (host, int(port))
        if pair not in pairs:
            pairs.append(pair)
    if not pairs:
        return None

    pair_expr = " or ".join(
        f"(dst host {host} and dst port {port})" for host, port in pairs
    )
    return f"tcp and src host {local_ip} and ({pair_expr})"


def build_wide_capture_filter(local_ip):
    return f"tcp and src host {local_ip}"


def endpoint_sets(endpoints):
    hosts = set()
    ports = set()
    for host, port, _, _ in endpoints[:12]:
        hosts.add(host)
        ports.add(str(port))
    return hosts, ports


def find_wechat_window():
    return (
        user32.FindWindowW("WeChatMainWndForPC", None)
        or user32.FindWindowW(None, "微信")
        or user32.FindWindowW(None, "Weixin")
    )


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("微信定时发送校准版")
        self.root.geometry("900x720")
        self.root.minsize(820, 600)
        self.ui_q = queue.Queue()
        self.cancel_ev = threading.Event()
        self.worker_lock = threading.Lock()
        self.worker_running = False
        self.trigger_offset = 0.0
        self.wechat_endpoints = []
        self.packet_delay_samples = []
        self.endpoint_lock = threading.Lock()
        self.record_lock = threading.Lock()
        self.last_send_record_id = None
        self._build_ui()
        self._loop_ui()
        self._tick_clock()
        self._track_wechat_net()

    def _build_ui(self):
        main_f = ttk.Frame(self.root, padding=15)
        main_f.pack(fill="both", expand=True)

        header_f = ttk.Frame(main_f)
        header_f.pack(fill="x", pady=(0, 2))
        ttk.Label(header_f, text="◤ 微信定时发送 · NETRUNNER ◢", style="Banner.TLabel").pack(side="left")
        ttk.Label(header_f, text="// PRECISION STRIKE", style="Sub.TLabel").pack(side="left", padx=10)
        tk.Frame(main_f, height=2, bg=CP_CYAN, bd=0, highlightthickness=0).pack(fill="x", pady=(2, 10))

        self.time_v = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d %H:%M:%S.000"))
        self.adv_v = tk.StringVar(value="9.5")
        self.ntp_v = tk.StringVar(value=TENCENT_CLOUD_PUBLIC_NTP)
        self.auto_detect_v = tk.StringVar(value="1")
        self.auto_before_s_v = tk.StringVar(value=str(int(DEFAULT_AUTO_REDETECT_BEFORE_S)))
        self.client_pack_ms_v = tk.StringVar(value=f"{DEFAULT_CLIENT_PACK_DELAY_MS:.1f}")
        self.rush_guard_ms_v = tk.StringVar(value=f"{DEFAULT_RUSH_GUARD_MS:.1f}")
        self.advance_cap_ms_v = tk.StringVar(value=f"{DEFAULT_ADVANCE_CAP_MS:.1f}")
        self.edge_trim_ms_v = tk.StringVar(value=f"{DEFAULT_EDGE_TRIM_MS:.1f}")
        self.lead_ms_v = tk.StringVar(value="0")
        self.tshark_path_v = tk.StringVar(value=find_tshark() or "")
        self.capture_iface_v = tk.StringVar(value="4")
        self._adv_win = None

        cfg_f = ttk.LabelFrame(main_f, text="核心设置", padding=12)
        cfg_f.pack(fill="x", pady=5)

        r0 = ttk.Frame(cfg_f)
        r0.pack(fill="x", pady=4)
        ttk.Label(r0, text="目标时刻:", width=12).pack(side="left")
        ttk.Entry(r0, textvariable=self.time_v, width=26).pack(side="left", padx=5)
        ttk.Label(r0, text="格式 2026-05-29 12:00:00.000", style="Hint.TLabel").pack(side="left", padx=6)

        r1 = ttk.Frame(cfg_f)
        r1.pack(fill="x", pady=4)
        ttk.Label(r1, text="贴线微调(ms):", width=12).pack(side="left")
        ttk.Entry(r1, textvariable=self.lead_ms_v, width=10).pack(side="left", padx=5)
        ttk.Label(r1, text="★关键：让到达更早。从0每次+3~5ms，弃票就退回", style="Hint.TLabel").pack(side="left", padx=6)

        r2 = ttk.Frame(cfg_f)
        r2.pack(fill="x", pady=4)
        ttk.Label(r2, text="提前量(ms):", width=12).pack(side="left")
        ttk.Entry(r2, textvariable=self.adv_v, width=10).pack(side="left", padx=5)
        ttk.Label(r2, text="自动测向:", width=8).pack(side="left", padx=(16, 0))
        ttk.Entry(r2, textvariable=self.auto_detect_v, width=6).pack(side="left", padx=5)
        ttk.Label(r2, text="1开/0关，建议开（开后提前量自动算）", style="Hint.TLabel").pack(side="left", padx=6)

        op_f = ttk.LabelFrame(main_f, text="操作", padding=12)
        op_f.pack(fill="x", pady=5)
        top_btn = ttk.Frame(op_f)
        top_btn.pack(fill="x")
        ttk.Button(top_btn, text="🛰 时间同步", command=lambda: self.sys_sync_task(self.ntp_v.get())).pack(side="left", padx=3)
        ttk.Button(top_btn, text="微信链路测向", command=self.detect).pack(side="left", padx=3)
        ttk.Button(top_btn, text="实战记录", command=self.show_send_records_window).pack(side="left", padx=3)
        ttk.Button(top_btn, text="⚙ 高级参数", command=self.show_advanced_window).pack(side="left", padx=3)

        go_btn = ttk.Frame(op_f)
        go_btn.pack(fill="x", pady=(10, 0))
        tk.Button(go_btn, text="▶ 执行发送", command=self.arm, bg=CP_YELLOW, fg="#0a0e16",
                  activebackground="#fff740", activeforeground="#0a0e16", relief="flat", bd=0,
                  cursor="hand2", font=("Microsoft YaHei UI", 13, "bold"), padx=28, pady=8).pack(side="left", padx=3)
        ttk.Button(go_btn, text="终止", command=self.cancel).pack(side="left", padx=10)

        status_f = ttk.LabelFrame(main_f, text="状态", padding=10)
        status_f.pack(fill="x", pady=5)
        self.ntp_clock_v = tk.StringVar(value="NTP 实时: --:--:--.---")
        self.wechat_net_v = tk.StringVar(value="微信链路: 搜索中...")
        self.status_v = tk.StringVar(value="状态：等待指令")
        self.info_v = tk.StringVar(value="基准同步：尚未手动同步")
        ttk.Label(status_f, textvariable=self.ntp_clock_v, font=("Consolas", 13, "bold"), foreground=CP_CYAN).pack(side="right", padx=15)
        ttk.Label(status_f, textvariable=self.wechat_net_v, font=("Consolas", 10), foreground=CP_TERM).pack(side="bottom", anchor="w", padx=15)
        ttk.Label(status_f, textvariable=self.status_v, font=("Microsoft YaHei UI", 10, "bold"), foreground=CP_YELLOW).pack(side="left", padx=15)
        ttk.Label(status_f, textvariable=self.info_v, foreground=CP_DIM).pack(side="left", padx=15)

        log_f = ttk.LabelFrame(main_f, text="日志", padding=5)
        log_f.pack(fill="both", expand=True, pady=5)
        self.log_t = tk.Text(log_f, bg=CP_INPUT, fg=CP_TERM, font=("Consolas", 10), height=14,
                             insertbackground=CP_CYAN, selectbackground=CP_MAG,
                             relief="flat", bd=0, padx=8, pady=6, highlightthickness=0)
        self.log_t.pack(fill="both", expand=True)
        self.log_t.config(state="disabled")

    def show_advanced_window(self):
        if getattr(self, "_adv_win", None) and self._adv_win.winfo_exists():
            self._adv_win.deiconify()
            self._adv_win.lift()
            self._adv_win.focus_force()
            return
        win = tk.Toplevel(self.root)
        self._adv_win = win
        win.title("高级参数 / 校准")
        win.geometry("620x540")
        win.configure(bg=CP_BG)

        wrap = ttk.Frame(win, padding=14)
        wrap.pack(fill="both", expand=True)
        ttk.Label(wrap, text="◤ 高级参数 ◢", style="Banner.TLabel").pack(anchor="w")
        tk.Frame(wrap, height=2, bg=CP_MAG, bd=0, highlightthickness=0).pack(fill="x", pady=(2, 10))

        sync_f = ttk.LabelFrame(wrap, text="时间同步源", padding=8)
        sync_f.pack(fill="x", pady=4)
        ttk.Button(sync_f, text="苹果", command=lambda: self.sys_sync_task(NTP_APPLE)).pack(side="left", padx=3)
        ttk.Button(sync_f, text="微软", command=lambda: self.sys_sync_task(NTP_MS)).pack(side="left", padx=3)
        ttk.Button(sync_f, text="腾讯云内网", command=lambda: self.sys_sync_task(TENCENT_CLOUD_INTRANET_NTP)).pack(side="left", padx=3)
        ttk.Button(sync_f, text="腾讯云外网", command=lambda: self.sys_sync_task(TENCENT_CLOUD_PUBLIC_NTP)).pack(side="left", padx=3)

        par_f = ttk.LabelFrame(wrap, text="提前量参数（一般用默认）", padding=8)
        par_f.pack(fill="x", pady=4)
        adv_params = [
            ("重测提前(s):", self.auto_before_s_v, "贴线建议 5"),
            ("客户端补偿(ms):", self.client_pack_ms_v, "同城 5-7"),
            ("抢跑保护(ms):", self.rush_guard_ms_v, "1-1.5"),
            ("提前上限(ms):", self.advance_cap_ms_v, "0 不限"),
            ("贴边修正(ms):", self.edge_trim_ms_v, "每次 ±0.5"),
        ]
        for i, (lab, var, hint) in enumerate(adv_params):
            r, c = i // 2, (i % 2) * 3
            ttk.Label(par_f, text=lab, width=14, anchor="e").grid(row=r, column=c, sticky="e", padx=(6, 2), pady=4)
            ttk.Entry(par_f, textvariable=var, width=8).grid(row=r, column=c + 1, sticky="w", pady=4)
            ttk.Label(par_f, text=hint, style="Hint.TLabel").grid(row=r, column=c + 2, sticky="w", padx=(4, 10))
        ttk.Button(par_f, text="抢跑了：贴边 -0.5", command=lambda: self.adjust_edge_trim(-0.5)).grid(row=3, column=0, columnspan=2, sticky="w", padx=6, pady=8)
        ttk.Button(par_f, text="慢了：贴边 +0.5", command=lambda: self.adjust_edge_trim(0.5)).grid(row=3, column=3, columnspan=2, sticky="w", padx=6, pady=8)

        net_f = ttk.LabelFrame(wrap, text="抓包校准（可选，进阶）", padding=8)
        net_f.pack(fill="x", pady=4)
        nr0 = ttk.Frame(net_f)
        nr0.pack(fill="x", pady=3)
        ttk.Label(nr0, text="NTP源:", width=10).pack(side="left")
        ttk.Entry(nr0, textvariable=self.ntp_v, width=44).pack(side="left", padx=4)
        nr1 = ttk.Frame(net_f)
        nr1.pack(fill="x", pady=3)
        ttk.Label(nr1, text="TShark路径:", width=10).pack(side="left")
        ttk.Entry(nr1, textvariable=self.tshark_path_v, width=38).pack(side="left", padx=4)
        ttk.Button(nr1, text="选择", command=self.browse_tshark).pack(side="left", padx=3)
        nr2 = ttk.Frame(net_f)
        nr2.pack(fill="x", pady=3)
        ttk.Label(nr2, text="抓包网卡:", width=10).pack(side="left")
        ttk.Entry(nr2, textvariable=self.capture_iface_v, width=12).pack(side="left", padx=4)
        ttk.Button(nr2, text="列出网卡", command=self.list_capture_interfaces).pack(side="left", padx=3)
        ttk.Button(nr2, text="抓包校准出包延迟", command=self.calibrate_packet_delay).pack(side="left", padx=3)

        ttk.Button(wrap, text="关闭", command=win.destroy).pack(anchor="e", pady=10)

    def append_log(self, msg):
        now = corrected_now(self.trigger_offset)
        stamp = datetime.fromtimestamp(now).strftime("%H:%M:%S.%f")[:-3]
        self.ui_q.put(("log", f"[{stamp}] {msg}"))

    def _loop_ui(self):
        while not self.ui_q.empty():
            t, data = self.ui_q.get_nowait()
            if t == "log":
                self.log_t.config(state="normal")
                self.log_t.insert("end", data + "\n")
                self.log_t.see("end")
                self.log_t.config(state="disabled")
            elif t == "status":
                self.status_v.set(f"状态：{data}")
            elif t == "info":
                self.info_v.set(data)
            elif t == "advance":
                self.adv_v.set(data)
            elif t == "wechat_net":
                self.wechat_net_v.set(data)
        self.root.after(50, self._loop_ui)

    def _tick_clock(self):
        now = corrected_now(self.trigger_offset)
        self.ntp_clock_v.set(f"NTP 实时: {datetime.fromtimestamp(now).strftime('%H:%M:%S.%f')[:-3]}")
        self.root.after(40, self._tick_clock)

    def sys_sync_task(self, server_str):
        self.ntp_v.set(server_str)

        def task():
            self.ui_q.put(("status", "正在同步时间..."))
            try:
                result = ntp_sync_fast(server_str)
                self.trigger_offset = result["offset"]
                self.ui_q.put(
                    (
                        "info",
                        f"基准同步：{result['server']}({result['ip']}) | 偏置 {result['offset']*1000:.3f}ms | NTP RTT {result['rtt_ms']:.2f}ms | 样本 {result['samples']}",
                    )
                )
                self.append_log("时间基准已更新。")
            except Exception as exc:
                self.append_log(f"时间同步失败: {exc}")
                self.ui_q.put(("info", "基准同步：失败，请换源或检查网络"))
            self.ui_q.put(("status", "就绪"))

        threading.Thread(target=task, daemon=True).start()

    def _track_wechat_net(self):
        def task():
            while True:
                endpoints = parse_wechat_endpoints()
                with self.endpoint_lock:
                    self.wechat_endpoints = endpoints
                if endpoints:
                    preview = " / ".join(f"{h}:{p}" for h, p, _, _ in endpoints[:3])
                    name, pid = endpoints[0][2], endpoints[0][3]
                    self.ui_q.put(("wechat_net", f"微信({name}) PID:{pid} | 链路: {preview}"))
                else:
                    self.ui_q.put(("wechat_net", "未检测到微信 TCP 连接"))
                time.sleep(3)

        threading.Thread(target=task, daemon=True).start()

    def read_float(self, var, default, min_value=None):
        try:
            value = float(var.get())
        except (TypeError, ValueError):
            value = default
        if min_value is not None:
            value = max(min_value, value)
        return value

    def auto_detect_enabled(self):
        text = self.auto_detect_v.get().strip().lower()
        return text not in ("0", "false", "no", "off", "关", "否")

    def calc_advance_from_link(self, basis_ms):
        client_pack = self.read_float(self.client_pack_ms_v, DEFAULT_CLIENT_PACK_DELAY_MS, 0.0)
        rush_guard = self.read_float(self.rush_guard_ms_v, DEFAULT_RUSH_GUARD_MS, 0.0)
        cap = self.read_float(self.advance_cap_ms_v, DEFAULT_ADVANCE_CAP_MS, 0.0)
        edge_trim = self.read_float(self.edge_trim_ms_v, DEFAULT_EDGE_TRIM_MS)
        raw = basis_ms / 2.0 + client_pack
        guarded = max(0.0, raw - rush_guard + edge_trim)
        if cap > 0:
            guarded = min(guarded, cap)
        return round(guarded, 2), raw, client_pack, rush_guard, cap, edge_trim

    def measure_wechat_link(self, rounds=5, timeout=0.35, cancel_ev=None):
        with self.endpoint_lock:
            endpoints = list(self.wechat_endpoints)

        if not endpoints:
            endpoints = parse_wechat_endpoints()

        samples = []
        rounds = max(1, int(rounds))
        for _ in range(rounds):
            if cancel_ev and cancel_ev.is_set():
                return None
            for host, port, _, _ in endpoints[:8]:
                if cancel_ev and cancel_ev.is_set():
                    return None
                try_ports = []
                for try_port in (port, 443):
                    if try_port not in try_ports:
                        try_ports.append(try_port)
                for try_port in try_ports:
                    try:
                        ms = tcp_connect_ms(host, try_port, timeout=timeout)
                        samples.append((ms, host, try_port))
                        break
                    except Exception:
                        pass
            if rounds > 1:
                time.sleep(0.05)

        if not samples:
            return None

        samples.sort(key=lambda x: x[0])
        basis_sample_count = max(1, min(5, len(samples)))
        low = samples[:basis_sample_count]
        basis_ms = statistics.mean(x[0] for x in low)
        best_ms, host, port = samples[0]
        advance_ms, raw_ms, client_pack, rush_guard, cap, edge_trim = self.calc_advance_from_link(basis_ms)
        return {
            "advance_ms": advance_ms,
            "raw_ms": raw_ms,
            "client_pack_ms": client_pack,
            "rush_guard_ms": rush_guard,
            "cap_ms": cap,
            "edge_trim_ms": edge_trim,
            "basis_ms": basis_ms,
            "best_ms": best_ms,
            "host": host,
            "port": port,
            "samples": len(samples),
            "basis_sample_count": basis_sample_count,
        }

    def detect(self):
        def task():
            self.ui_q.put(("status", "微信链路测向中..."))
            result = self.measure_wechat_link(rounds=5, timeout=0.45)
            if not result:
                self.append_log("未能对微信当前 TCP 目的地址建立测向连接，保留原提前量。")
                self.ui_q.put(("status", "就绪"))
                return

            self.append_log(
                "微信链路测向: "
                f"{result['host']}:{result['port']} TCP最低 {result['best_ms']:.2f}ms | "
                f"最快{result['basis_sample_count']}次均值 {result['basis_ms']:.2f}ms | "
                f"补偿 {result['client_pack_ms']:.2f}ms - 抢跑保护 {result['rush_guard_ms']:.2f}ms | "
                f"贴边修正 {result['edge_trim_ms']:+.2f}ms | "
                f"建议提前量 {result['advance_ms']:.2f}ms"
            )
            self.ui_q.put(("advance", f"{result['advance_ms']:.2f}"))
            self.ui_q.put(("status", "就绪"))

        threading.Thread(target=task, daemon=True).start()

    def browse_tshark(self):
        current = self.tshark_path_v.get().strip().strip('"')
        initial_dir = r"C:\Program Files\Wireshark"
        if current and os.path.isdir(os.path.dirname(current)):
            initial_dir = os.path.dirname(current)
        elif not os.path.isdir(initial_dir):
            initial_dir = os.path.expanduser("~")

        path = filedialog.askopenfilename(
            title="选择 tshark.exe",
            initialdir=initial_dir,
            filetypes=[
                ("TShark", "tshark.exe"),
                ("EXE files", "*.exe"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.tshark_path_v.set(path)
            self.append_log(f"TShark路径已设置: {path}")

    def resolve_tshark_path(self):
        typed = self.tshark_path_v.get().strip().strip('"')
        if typed:
            typed = os.path.expandvars(os.path.expanduser(typed))
            if os.path.isfile(typed):
                return typed
            found = shutil.which(typed)
            if found:
                self.tshark_path_v.set(found)
                return found
            raise RuntimeError(f"TShark路径无效: {typed}")

        auto = find_tshark()
        if auto:
            self.tshark_path_v.set(auto)
            return auto
        raise RuntimeError("未找到 tshark.exe。请在 TShark路径 中填写完整路径，例如 C:\\Program Files\\Wireshark\\tshark.exe")

    def list_capture_interfaces(self):
        def task():
            try:
                tshark = self.resolve_tshark_path()
                lines = tshark_interface_listing(tshark)
                if not lines:
                    self.append_log("TShark 没有返回网卡列表。")
                    return
                self.append_log("TShark 网卡列表如下，请把实际联网网卡编号填到“抓包网卡”：")
                for line in lines:
                    self.append_log("  " + line)
            except Exception as exc:
                self.append_log(f"列出网卡失败: {exc}")

        threading.Thread(target=task, daemon=True).start()

    def selected_capture_interfaces(self, tshark):
        typed = self.capture_iface_v.get().strip()
        if typed:
            items = [x.strip() for x in re.split(r"[,，;；\s]+", typed) if x.strip()]
            if not items:
                raise RuntimeError("抓包网卡填写无效。")
            return items

        interfaces = tshark_interfaces(tshark)
        if interfaces:
            self.append_log(f"抓包网卡未填写，自动尝试: {','.join(interfaces)}")
            return interfaces
        self.append_log("抓包网卡未填写且自动识别失败，默认尝试 1。")
        return ["1"]

    def adjust_edge_trim(self, delta_ms):
        value = self.read_float(self.edge_trim_ms_v, DEFAULT_EDGE_TRIM_MS) + float(delta_ms)
        value = round(value, 2)
        self.edge_trim_ms_v.set(f"{value:.2f}")
        direction = "更早" if delta_ms > 0 else "更晚"
        self.append_log(f"贴边修正已调整为 {value:+.2f}ms，本次调整方向：{direction}。")

    def calibration_log_path(self):
        return output_path(CALIBRATION_LOG)

    def calibration_debug_log_path(self):
        return output_path(CALIBRATION_DEBUG_LOG)

    def send_record_log_path(self):
        return output_path(SEND_RECORD_LOG)

    def write_calibration_row(self, row):
        path = self.calibration_log_path()
        exists = os.path.exists(path)
        headers = [
            "time",
            "delay_ms",
            "recommended_client_pack_ms",
            "dst_host",
            "dst_port",
            "tcp_len",
            "trigger_epoch",
            "trigger_done_epoch",
            "packet_epoch",
            "delay_from_done_ms",
            "sendinput_cost_ms",
            "current_advance_ms",
            "rush_guard_ms",
            "edge_trim_ms",
            "note",
        ]
        with open(path, "a", encoding="utf-8", newline="") as f:
            if not exists:
                f.write(",".join(headers) + "\n")
            f.write(",".join(str(row.get(h, "")) for h in headers) + "\n")
        return path

    def write_calibration_debug(self, title, stdout, stderr, trigger_epoch, trigger_done_epoch=None):
        path = self.calibration_debug_log_path()
        with open(path, "a", encoding="utf-8", newline="") as f:
            f.write(f"\n===== {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} {title} =====\n")
            f.write(f"trigger_epoch={trigger_epoch:.6f}\n")
            if trigger_done_epoch is not None:
                f.write(f"trigger_done_epoch={trigger_done_epoch:.6f}\n")
                f.write(f"sendinput_cost_ms={(trigger_done_epoch - trigger_epoch) * 1000.0:.3f}\n")
            if stderr:
                f.write("--- stderr ---\n")
                f.write(stderr[-2000:] + "\n")
            f.write("--- stdout first 80 lines ---\n")
            for line in stdout.splitlines()[:80]:
                f.write(line + "\n")
        return path

    def read_send_records(self):
        path = self.send_record_log_path()
        if not os.path.exists(path):
            return []
        with self.record_lock:
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                return list(csv.DictReader(f))

    def write_send_records(self, rows):
        path = self.send_record_log_path()
        with self.record_lock:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=SEND_RECORD_HEADERS)
                writer.writeheader()
                for row in rows:
                    writer.writerow({h: row.get(h, "") for h in SEND_RECORD_HEADERS})
        return path

    def append_send_record(self, row):
        path = self.send_record_log_path()
        with self.record_lock:
            exists = os.path.exists(path)
            with open(path, "a", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=SEND_RECORD_HEADERS)
                if not exists:
                    writer.writeheader()
                writer.writerow({h: row.get(h, "") for h in SEND_RECORD_HEADERS})
        self.last_send_record_id = row.get("record_id")
        return path

    def update_send_record_outcome(self, record_id, outcome, phone_display_time="", rank_or_position="", note=""):
        rows = self.read_send_records()
        updated = False
        for row in rows:
            if row.get("record_id") == record_id:
                row["outcome"] = outcome
                row["phone_display_time"] = phone_display_time
                row["rank_or_position"] = rank_or_position
                row["note"] = note
                updated = True
                break
        if not updated:
            raise RuntimeError("没有找到要标记的记录。")
        return self.write_send_records(rows)

    def show_send_records_window(self):
        rows = self.read_send_records()
        win = tk.Toplevel(self.root)
        win.title("实战记录")
        win.geometry("1180x720")
        win.configure(bg=CP_BG)

        top_f = ttk.Frame(win, padding=8)
        top_f.pack(fill="both", expand=True)

        columns = (
            "created_at",
            "target_time",
            "final_advance_ms",
            "edge_trim_ms",
            "lead_ms",
            "local_error_ms",
            "link_host",
            "link_basis_ms",
            "outcome",
            "phone_display_time",
            "rank_or_position",
        )
        tree = ttk.Treeview(top_f, columns=columns, show="headings", height=14)
        headings = {
            "created_at": "记录时间",
            "target_time": "目标时刻",
            "final_advance_ms": "最终提前",
            "edge_trim_ms": "贴边",
            "lead_ms": "贴线微调",
            "local_error_ms": "本机误差",
            "link_host": "链路目标",
            "link_basis_ms": "链路基准",
            "outcome": "结果",
            "phone_display_time": "手机显示",
            "rank_or_position": "名次/位置",
        }
        widths = {
            "created_at": 150,
            "target_time": 180,
            "final_advance_ms": 80,
            "edge_trim_ms": 70,
            "lead_ms": 80,
            "local_error_ms": 80,
            "link_host": 150,
            "link_basis_ms": 80,
            "outcome": 80,
            "phone_display_time": 100,
            "rank_or_position": 90,
        }
        for col in columns:
            tree.heading(col, text=headings[col])
            tree.column(col, width=widths[col], anchor="center")
        tree.pack(fill="both", expand=True, side="left")

        scroll = ttk.Scrollbar(top_f, orient="vertical", command=tree.yview)
        scroll.pack(side="right", fill="y")
        tree.configure(yscrollcommand=scroll.set)

        row_by_item = {}
        for row in rows[-80:]:
            item = tree.insert("", "end", values=[row.get(col, "") for col in columns])
            row_by_item[item] = row
            if row.get("record_id") == self.last_send_record_id:
                tree.selection_set(item)
                tree.see(item)

        detail_f = ttk.LabelFrame(win, text="选中记录完整参数", padding=6)
        detail_f.pack(fill="both", expand=True, padx=8, pady=4)
        detail_t = tk.Text(detail_f, height=8, font=("Consolas", 9), wrap="none",
                           bg=CP_INPUT, fg=CP_TERM, insertbackground=CP_CYAN,
                           selectbackground=CP_MAG, relief="flat", bd=0,
                           padx=8, pady=6, highlightthickness=0)
        detail_t.pack(fill="both", expand=True)
        detail_t.config(state="disabled")

        def show_detail(_event=None):
            sel = tree.selection()
            row = row_by_item.get(sel[0], {}) if sel else {}
            detail_t.config(state="normal")
            detail_t.delete("1.0", "end")
            for key in SEND_RECORD_HEADERS:
                label = SEND_RECORD_LABELS.get(key, key)
                detail_t.insert("end", f"{label} ({key}): {row.get(key, '')}\n")
            detail_t.config(state="disabled")

        tree.bind("<<TreeviewSelect>>", show_detail)
        show_detail()

        edit_f = ttk.LabelFrame(win, text="标记选中记录", padding=8)
        edit_f.pack(fill="x", padx=8, pady=6)
        phone_v = tk.StringVar()
        rank_v = tk.StringVar()
        note_v = tk.StringVar()

        ttk.Label(edit_f, text="手机显示:").pack(side="left")
        ttk.Entry(edit_f, textvariable=phone_v, width=12).pack(side="left", padx=4)
        ttk.Label(edit_f, text="名次/位置:").pack(side="left")
        ttk.Entry(edit_f, textvariable=rank_v, width=12).pack(side="left", padx=4)
        ttk.Label(edit_f, text="备注:").pack(side="left")
        ttk.Entry(edit_f, textvariable=note_v, width=35).pack(side="left", padx=4)

        def selected_record_id():
            sel = tree.selection()
            if not sel:
                return None
            return row_by_item.get(sel[0], {}).get("record_id")

        def mark(outcome):
            record_id = selected_record_id()
            if not record_id:
                self.append_log("请先在实战记录窗口选中一条记录。")
                return
            try:
                path = self.update_send_record_outcome(
                    record_id,
                    outcome,
                    phone_v.get().strip(),
                    rank_v.get().strip(),
                    note_v.get().strip(),
                )
                self.append_log(f"实战记录已标记为 {outcome}，记录文件: {path}")
                win.destroy()
            except Exception as exc:
                self.append_log(f"标记实战记录失败: {exc}")

        for text in ("抢跑", "卡点", "慢了", "无效"):
            ttk.Button(edit_f, text=text, command=lambda t=text: mark(t)).pack(side="left", padx=3)

        ttk.Button(edit_f, text="刷新", command=lambda: (win.destroy(), self.show_send_records_window())).pack(side="right", padx=3)

    def calibrate_packet_delay(self):
        if self.worker_running:
            self.append_log("正式发送任务运行中，不能同时做抓包校准。")
            return
        threading.Thread(target=self._calibrate_packet_delay, daemon=True).start()

    def _calibrate_packet_delay(self):
        self.ui_q.put(("status", "抓包校准中"))
        try:
            tshark = self.resolve_tshark_path()
            self.append_log(f"使用TShark: {tshark}")

            endpoints_before = parse_wechat_endpoints()
            endpoints = endpoints_before
            if not endpoints:
                raise RuntimeError("未检测到微信 TCP 连接，请先让微信保持在线。")
            with self.endpoint_lock:
                self.wechat_endpoints = endpoints

            local_ip = local_ip_for(endpoints[0][0], endpoints[0][1])
            capture_filter = build_wide_capture_filter(local_ip)
            if not capture_filter:
                raise RuntimeError("无法生成微信抓包过滤器。")

            hwnd = find_wechat_window()
            if not hwnd:
                raise RuntimeError("无法找到微信窗口")
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
            self.append_log("已先将微信置顶，请确认测试消息在输入框且光标未丢。")
            time.sleep(0.2)

            interfaces = self.selected_capture_interfaces(tshark)
            self.append_log(f"本次抓包网卡: {','.join(interfaces)}")

            args = [tshark, "-l", "-n"]
            for iface in interfaces:
                args.extend(["-i", iface])
            args.extend(
                [
                    "-f",
                    capture_filter,
                    "-a",
                    "duration:5",
                    "-T",
                    "fields",
                    "-e",
                    "frame.time_epoch",
                    "-e",
                    "ip.dst",
                    "-e",
                    "tcp.dstport",
                    "-e",
                    "tcp.len",
                    "-e",
                    "frame.len",
                ]
            )

            self.append_log(f"抓包校准已启动，过滤器: {capture_filter}")
            proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=CREATE_NO_WINDOW,
            )
            time.sleep(0.70)
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.08)

            self.append_log("准备触发测试消息：下一步立即提交 Enter。")
            trigger_epoch = time.time()
            press_enter()
            trigger_done_epoch = time.time()
            sendinput_cost_ms = (trigger_done_epoch - trigger_epoch) * 1000.0
            self.append_log(f"测试消息 Enter 已提交，用时 {sendinput_cost_ms:.3f}ms；等待分析微信出包。")

            try:
                stdout, stderr = proc.communicate(timeout=4.5)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()

            endpoints_after = parse_wechat_endpoints()
            all_endpoints = endpoints_before + [
                item for item in endpoints_after
                if (item[0], item[1]) not in {(x[0], x[1]) for x in endpoints_before}
            ]
            preferred_hosts, preferred_ports = endpoint_sets(all_endpoints)

            first = None
            payload_rows = []
            for line in stdout.splitlines():
                parts = line.strip().split("\t")
                if len(parts) < 5 or not parts[0]:
                    continue
                try:
                    packet_epoch = float(parts[0])
                    tcp_len = int(parts[3] or "0")
                except ValueError:
                    continue
                if tcp_len <= 0:
                    continue
                if packet_epoch < trigger_epoch - 0.002:
                    continue
                item = {
                    "packet_epoch": packet_epoch,
                    "dst_host": parts[1],
                    "dst_port": parts[2],
                    "tcp_len": tcp_len,
                    "frame_len": parts[4],
                }
                payload_rows.append(item)
                if (
                    first is None
                    and item["dst_host"] in preferred_hosts
                    and item["dst_port"] in preferred_ports
                    and tcp_len >= MESSAGE_PAYLOAD_MIN_LEN
                ):
                    first = item

            if not first:
                detail = (stderr or "").strip().splitlines()
                suffix = f" tshark: {detail[-1]}" if detail else ""
                debug_path = self.write_calibration_debug("no_payload_after_trigger", stdout, stderr, trigger_epoch, trigger_done_epoch)
                raise RuntimeError(f"未捕获到按键后的出站 TCP 数据包。已写入调试日志 {debug_path}。{suffix}")

            delay_ms = max(0.0, (first["packet_epoch"] - trigger_epoch) * 1000.0)
            delay_from_done_ms = max(0.0, (first["packet_epoch"] - trigger_done_epoch) * 1000.0)
            matched_note = "pid_endpoint_before_after"
            candidates = []
            for item in payload_rows:
                if item["dst_host"] in preferred_hosts and item["dst_port"] in preferred_ports:
                    item_delay = max(0.0, (item["packet_epoch"] - trigger_epoch) * 1000.0)
                    item_delay_done = max(0.0, (item["packet_epoch"] - trigger_done_epoch) * 1000.0)
                    candidates.append(
                        f"{item_delay:.1f}ms({item_delay_done:.1f}ms提交后)/{item['dst_host']}:{item['dst_port']}/len{item['tcp_len']}"
                    )

            recommended = self.read_float(self.client_pack_ms_v, DEFAULT_CLIENT_PACK_DELAY_MS, 0.0)
            auto_delay_ms = delay_from_done_ms
            if MIN_AUTO_CLIENT_PACK_MS <= auto_delay_ms <= MAX_AUTO_CLIENT_PACK_MS:
                self.packet_delay_samples.append(auto_delay_ms)
                self.packet_delay_samples = self.packet_delay_samples[-20:]
                low = sorted(self.packet_delay_samples)[: max(1, min(3, len(self.packet_delay_samples)))]
                recommended = statistics.median(low)
                self.client_pack_ms_v.set(f"{recommended:.2f}")
                update_note = "auto_updated"
            else:
                update_note = "suspicious_not_auto_updated"

            row = {
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                "delay_ms": f"{delay_ms:.3f}",
                "recommended_client_pack_ms": f"{recommended:.3f}",
                "dst_host": first["dst_host"],
                "dst_port": first["dst_port"],
                "tcp_len": first["tcp_len"],
                "trigger_epoch": f"{trigger_epoch:.6f}",
                "trigger_done_epoch": f"{trigger_done_epoch:.6f}",
                "packet_epoch": f"{first['packet_epoch']:.6f}",
                "delay_from_done_ms": f"{delay_from_done_ms:.3f}",
                "sendinput_cost_ms": f"{sendinput_cost_ms:.3f}",
                "current_advance_ms": self.adv_v.get(),
                "rush_guard_ms": self.rush_guard_ms_v.get(),
                "edge_trim_ms": self.edge_trim_ms_v.get(),
                "note": f"{matched_note};{update_note}",
            }
            log_path = self.write_calibration_row(row)
            self.append_log(
                f"出包校准完成：调用开始->出包 {delay_ms:.3f}ms | 提交完成->出包 {delay_from_done_ms:.3f}ms | "
                f"目标 {first['dst_host']}:{first['dst_port']} | {matched_note}"
            )
            if candidates:
                self.append_log("微信候选出包: " + " | ".join(candidates[:6]))
            if update_note == "auto_updated":
                self.append_log(f"低位样本建议客户端补偿 {recommended:.3f}ms，已写入客户端补偿。")
            else:
                debug_path = self.write_calibration_debug("suspicious_large_delay", stdout, stderr, trigger_epoch, trigger_done_epoch)
                self.append_log(
                    f"该样本 {delay_ms:.3f}ms 超过 {MAX_AUTO_CLIENT_PACK_MS:.0f}ms，疑似非关键发送包，已记录但不自动改补偿。调试日志: {debug_path}"
                )
            self.append_log(f"校准记录已写入: {log_path}")
        except Exception as exc:
            self.append_log(f"抓包校准失败: {exc}")
        finally:
            self.ui_q.put(("status", "就绪"))

    def cancel(self):
        self.cancel_ev.set()
        self.ui_q.put(("status", "已终止"))

    def arm(self):
        with self.worker_lock:
            if self.worker_running:
                self.append_log("已有发送任务在运行，已拦截重复启动。")
                self.ui_q.put(("status", "任务运行中"))
                return
            self.worker_running = True
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        self.cancel_ev.clear()
        set_timer_res(1)
        priority_set = False
        try:
            target_ts = datetime.strptime(self.time_v.get().strip(), "%Y-%m-%d %H:%M:%S.%f").timestamp()
            advance_ms = float(self.adv_v.get())
            input_advance_ms = advance_ms
            lead_ms = self.read_float(self.lead_ms_v, 0.0, 0.0)
            fire_ts = target_ts - (advance_ms + lead_ms) / 1000.0
            auto_detect_done = not self.auto_detect_enabled()
            auto_before_s = self.read_float(
                self.auto_before_s_v, DEFAULT_AUTO_REDETECT_BEFORE_S, 1.5
            )
            auto_detect_ts = target_ts - auto_before_s
            last_link_result = None
            auto_detect_result = "off" if auto_detect_done else "pending"

            if target_ts <= corrected_now(self.trigger_offset):
                self.append_log("目标时间已过期。")
                self.ui_q.put(("status", "任务拦截"))
                return

            hwnd = find_wechat_window()
            if not hwnd:
                raise RuntimeError("无法找到微信窗口")

            self.append_log("已锁定微信窗口。请提前把消息放入正确聊天输入框，并保持光标在输入框内。")
            self.ui_q.put(("status", "等待发送点..."))

            foreground_done = False
            while not self.cancel_ev.is_set():
                now_c = corrected_now(self.trigger_offset)

                if not auto_detect_done and now_c >= auto_detect_ts:
                    self.ui_q.put(("status", "临近自动测向中"))
                    self.append_log(f"进入临近自动测向，旧提前量 {advance_ms:.2f}ms。")
                    result = self.measure_wechat_link(rounds=5, timeout=0.35, cancel_ev=self.cancel_ev)
                    if self.cancel_ev.is_set():
                        return
                    if result:
                        last_link_result = result
                        old_advance = advance_ms
                        advance_ms = result["advance_ms"]
                        fire_ts = target_ts - (advance_ms + lead_ms) / 1000.0
                        auto_detect_result = "ok"
                        self.ui_q.put(("advance", f"{advance_ms:.2f}"))
                        self.append_log(
                            "临近测向更新提前量: "
                            f"{old_advance:.2f}ms -> {advance_ms:.2f}ms | "
                            f"{result['host']}:{result['port']} 最低 {result['best_ms']:.2f}ms / "
                            f"最快{result['basis_sample_count']}次均值 {result['basis_ms']:.2f}ms | "
                            f"补偿 {result['client_pack_ms']:.2f} - 保护 {result['rush_guard_ms']:.2f} "
                            f"+ 贴边 {result['edge_trim_ms']:+.2f}"
                        )
                    else:
                        auto_detect_result = "failed"
                        self.append_log(f"临近自动测向失败，继续沿用当前提前量 {advance_ms:.2f}ms。")
                    auto_detect_done = True
                    self.ui_q.put(("status", "等待发送点..."))
                    continue

                left = fire_ts - now_c

                if left <= 2.0 and not priority_set:
                    kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), HIGH_PRIORITY_CLASS)
                    kernel32.SetThreadPriority(kernel32.GetCurrentThread(), THREAD_PRIORITY_HIGHEST)
                    priority_set = True
                    self.append_log("进入高优先级等待。")

                if left <= 5.0 and not foreground_done:
                    user32.ShowWindow(hwnd, SW_RESTORE)
                    user32.SetForegroundWindow(hwnd)
                    foreground_done = True
                    self.append_log("已提前将微信置前。")

                if left <= 0.02:
                    break
                if left > 5:
                    time.sleep(0.1)
                elif left > 0.5:
                    time.sleep(0.01)
                else:
                    time.sleep(0.001)

            if self.cancel_ev.is_set():
                return

            deadline = time.perf_counter() + max(0.0, fire_ts - corrected_now(self.trigger_offset))
            while time.perf_counter() < deadline:
                if deadline - time.perf_counter() > 0.0007:
                    time.sleep(0)

            press_enter()
            actual = corrected_now(self.trigger_offset)
            delta_ms = (actual - fire_ts) * 1000.0
            self.append_log(f"发送按键已触发，本机触发误差 {delta_ms:+.3f}ms。")
            record = {
                "record_id": datetime.now().strftime("%Y%m%d%H%M%S%f"),
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                "app_version": "wechat_timer_v10",
                "target_time": self.time_v.get().strip(),
                "target_epoch": f"{target_ts:.6f}",
                "input_advance_ms": f"{input_advance_ms:.3f}",
                "final_advance_ms": f"{advance_ms:.3f}",
                "fire_epoch": f"{fire_ts:.6f}",
                "actual_epoch": f"{actual:.6f}",
                "local_error_ms": f"{delta_ms:.3f}",
                "ntp_source_input": self.ntp_v.get().strip(),
                "tshark_path_input": self.tshark_path_v.get().strip(),
                "capture_iface_input": self.capture_iface_v.get().strip(),
                "client_pack_ms": self.client_pack_ms_v.get().strip(),
                "rush_guard_ms": self.rush_guard_ms_v.get().strip(),
                "advance_cap_ms": self.advance_cap_ms_v.get().strip(),
                "edge_trim_ms": self.edge_trim_ms_v.get().strip(),
                "lead_ms": f"{lead_ms:.1f}",
                "auto_detect": self.auto_detect_v.get().strip(),
                "auto_before_s": self.auto_before_s_v.get().strip(),
                "trigger_offset_ms": f"{self.trigger_offset * 1000.0:.3f}",
                "link_host": last_link_result.get("host", "") if last_link_result else "",
                "link_port": last_link_result.get("port", "") if last_link_result else "",
                "link_best_ms": f"{last_link_result['best_ms']:.3f}" if last_link_result else "",
                "link_basis_ms": f"{last_link_result['basis_ms']:.3f}" if last_link_result else "",
                "link_basis_sample_count": last_link_result.get("basis_sample_count", "") if last_link_result else "",
                "link_total_samples": last_link_result.get("samples", "") if last_link_result else "",
                "link_raw_ms": f"{last_link_result['raw_ms']:.3f}" if last_link_result else "",
                "link_cap_ms": f"{last_link_result['cap_ms']:.3f}" if last_link_result else "",
                "auto_detect_result": auto_detect_result,
                "ntp_info": self.info_v.get(),
                "wechat_link": self.wechat_net_v.get(),
                "outcome": "",
                "phone_display_time": "",
                "rank_or_position": "",
                "note": "",
            }
            record_path = self.append_send_record(record)
            self.append_log(f"实战记录已写入: {record_path}")
            self.ui_q.put(("status", "已触发"))

        except Exception as exc:
            self.append_log(f"错误: {exc}")
            self.ui_q.put(("status", "错误"))
        finally:
            reset_timer_res(1)
            with self.worker_lock:
                self.worker_running = False


def show_fatal_error(exc):
    log_path = "ntp_key_timer_wechat_revised_error.log"
    detail = traceback.format_exc()
    try:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(detail)
    except Exception:
        pass
    try:
        ctypes.windll.user32.MessageBoxW(
            None,
            f"程序启动失败：\n{exc}\n\n详细错误已写入：{log_path}",
            "微信定时发送校准版",
            0x10,
        )
    except Exception:
        print(detail)
        input("按回车退出...")


def enable_dpi_awareness():
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


# ===================== 赛博朋克主题 (Cyberpunk 2077 / Edgerunners) =====================
CP_BG     = "#0a0e16"   # 近黑蓝底
CP_PANEL  = "#0f1622"   # 面板
CP_INPUT  = "#070b12"   # 输入框
CP_BORDER = "#1c3a4a"   # 青调描边
CP_FG     = "#d6f2ff"   # 冷白文字
CP_DIM    = "#5a7a8c"   # 暗提示
CP_YELLOW = "#fcee0a"   # 2077 标志黄
CP_CYAN   = "#00f0ff"   # 电光青
CP_MAG    = "#ff2a6d"   # 边缘行者品红
CP_RED    = "#ff003c"   # 危险红
CP_TERM   = "#7df9e8"   # 终端青绿


def apply_cyberpunk_theme(root):
    try:
        root.configure(bg=CP_BG)
    except Exception:
        pass
    try:
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        val = ctypes.c_int(1)
        for attr in (20, 19):
            try:
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(val), ctypes.sizeof(val))
            except Exception:
                pass
    except Exception:
        pass
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    label_font = ("Microsoft YaHei UI", 9)
    style.configure(".", background=CP_BG, foreground=CP_FG,
                    fieldbackground=CP_INPUT, bordercolor=CP_BORDER,
                    lightcolor=CP_BORDER, darkcolor=CP_BORDER,
                    troughcolor=CP_PANEL, font=label_font,
                    focuscolor=CP_CYAN, insertcolor=CP_CYAN,
                    selectbackground=CP_MAG, selectforeground="#ffffff")
    style.configure("TFrame", background=CP_BG)
    style.configure("TLabel", background=CP_BG, foreground=CP_FG)
    style.configure("Hint.TLabel", background=CP_BG, foreground=CP_DIM)
    style.configure("Banner.TLabel", background=CP_BG, foreground=CP_YELLOW,
                    font=("Consolas", 17, "bold"))
    style.configure("Sub.TLabel", background=CP_BG, foreground=CP_CYAN,
                    font=("Consolas", 9))
    style.configure("TLabelframe", background=CP_BG, bordercolor=CP_CYAN,
                    relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=CP_BG, foreground=CP_YELLOW,
                    font=("Consolas", 10, "bold"))
    style.configure("TButton", background=CP_PANEL, foreground=CP_CYAN,
                    bordercolor=CP_BORDER, relief="flat", padding=(10, 5),
                    font=("Microsoft YaHei UI", 9, "bold"))
    style.map("TButton",
              background=[("pressed", "#0a121c"), ("active", "#14202e")],
              foreground=[("active", CP_YELLOW)],
              bordercolor=[("active", CP_CYAN)])
    style.configure("TEntry", fieldbackground=CP_INPUT, foreground=CP_FG,
                    bordercolor=CP_BORDER, insertcolor=CP_CYAN, padding=5)
    style.map("TEntry", bordercolor=[("focus", CP_CYAN)],
              lightcolor=[("focus", CP_CYAN)], darkcolor=[("focus", CP_CYAN)])
    style.configure("TCombobox", fieldbackground=CP_INPUT, background=CP_PANEL,
                    foreground=CP_FG, arrowcolor=CP_YELLOW, bordercolor=CP_BORDER)
    style.configure("Treeview", background=CP_PANEL, fieldbackground=CP_PANEL,
                    foreground=CP_FG, bordercolor=CP_BORDER, rowheight=26)
    style.map("Treeview", background=[("selected", CP_MAG)],
              foreground=[("selected", "#ffffff")])
    style.configure("Treeview.Heading", background=CP_BG, foreground=CP_YELLOW,
                    relief="flat", font=("Consolas", 9, "bold"))
    style.map("Treeview.Heading", background=[("active", CP_PANEL)],
              foreground=[("active", CP_CYAN)])
    style.configure("TScrollbar", background=CP_PANEL, troughcolor=CP_BG,
                    arrowcolor=CP_CYAN, bordercolor=CP_BORDER)
    return style


def main():
    enable_dpi_awareness()
    root = tk.Tk()
    apply_cyberpunk_theme(root)
    App(root)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        show_fatal_error(exc)
