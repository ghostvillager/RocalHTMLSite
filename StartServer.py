# -*- coding: utf-8 -*-
"""
Shelf + Eroge 統合ランチャー
- シングルインスタンスロック（mutex）
- システムトレイ（インジケーター）収納
- トレイアイコンクリックで表示/非表示トグル
- ×ボタン・最小化は必ずインジケーターへ収納（終了はトレイメニューのみ）
- ログ表示付きGUI（ログコピー）
"""

from __future__ import annotations

import os
import sys
import socket
import threading
import subprocess
import queue
import time
import webbrowser
import re
from pathlib import Path
from datetime import datetime

# ---------------------------------------------------------------------------
# パス・設定
# ---------------------------------------------------------------------------
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent

ICON_PATH = APP_DIR / "icon.ico"
EROGE_DIR = APP_DIR / "エロゲをウェブで実行"
NODE_DIR = Path(r"C:\RocalHTMLSite\サイト内ページ操作")

PORT_SHELF = 8000
PORT_EROGE = 8765
BIND_IP = "0.0.0.0"

LOG_MAX_LINES = 800

# ---------------------------------------------------------------------------
# 依存チェック
# ---------------------------------------------------------------------------
def _ensure_deps():
    missing = []
    try:
        import pystray
    except ImportError:
        missing.append("pystray")
    try:
        from PIL import Image
    except ImportError:
        missing.append("Pillow")
    try:
        import RangeHTTPServer
    except ImportError:
        missing.append("rangehttpserver")
    try:
        import win32event
    except ImportError:
        missing.append("pywin32")

    if missing:
        print("不足パッケージを自動インストールします:", ", ".join(missing))
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", *missing, "-q"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print("インストール完了")
        except Exception as e:
            print(f"自動インストールに失敗しました: {e}")
            sys.exit(1)

_ensure_deps()

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from PIL import Image, ImageDraw, ImageTk
import pystray
from pystray import MenuItem as item

try:
    import win32event
    import win32api
    import winerror
except ImportError:
    win32event = None
    win32api = None
    winerror = None

# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------
def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def port_open(host: str, port: int, timeout: float = 0.8) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def free_port(port: int) -> list[int]:
    """指定ポートを使用中のプロセスを終了する（Windows）。終了した PID のリストを返す。"""
    killed: list[int] = []
    if sys.platform != "win32":
        return killed
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"],
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        pids: set[int] = set()
        for line in out.splitlines():
            # TCP  0.0.0.0:8000  ...  LISTENING  1234
            if f":{port}" not in line or "LISTENING" not in line:
                continue
            parts = line.split()
            if not parts:
                continue
            try:
                pid = int(parts[-1])
            except ValueError:
                continue
            if pid > 0:
                pids.add(pid)
        for pid in pids:
            try:
                subprocess.check_call(
                    ["taskkill", "/PID", str(pid), "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
                killed.append(pid)
            except Exception:
                pass
    except Exception:
        pass
    return killed


def find_npm() -> list[str] | None:
    """Windows で npm を確実に見つける。見つからなければ None"""
    import shutil

    # 1. PATH から探す
    npm_path = shutil.which("npm") or shutil.which("npm.cmd")
    if npm_path:
        return [npm_path, "start"]

    # 2. よくあるインストール場所を探す
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs" / "npm.cmd",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "nodejs" / "npm.cmd",
        Path(os.environ.get("APPDATA", "")) / "npm" / "npm.cmd",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "node" / "npm.cmd",
    ]
    for p in candidates:
        if p.is_file():
            return [str(p), "start"]

    return None


def load_icon_image(size: int | None = None) -> Image.Image:
    if ICON_PATH.is_file():
        img = Image.open(ICON_PATH)
        if size:
            img = img.resize((size, size), Image.Resampling.LANCZOS)
        return img.convert("RGBA")
    s = size or 64
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([2, 2, s - 3, s - 3], fill=(120, 80, 200, 255))
    d.ellipse([s // 4, s // 4, s * 3 // 4, s * 3 // 4], fill=(40, 30, 60, 255))
    return img


# ---------------------------------------------------------------------------
# シングルインスタンス防止（ハンドル保持 + Global\\ 名前空間）
# ---------------------------------------------------------------------------
class SingleInstance:
    """名前付きミューテックスで1インスタンスのみ許可。ハンドルを保持し続けること。"""

    def __init__(self, name: str = "Global\\ShelfErogeLauncherSingleInstanceMutex"):
        self.mutex = None
        self.already_running = False
        if win32event is None:
            return
        try:
            # bInitialOwner=True で作成と同時に所有。既にあれば ERROR_ALREADY_EXISTS
            self.mutex = win32event.CreateMutex(None, True, name)
            if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
                self.already_running = True
                try:
                    win32api.CloseHandle(self.mutex)
                except Exception:
                    pass
                self.mutex = None
        except Exception:
            pass

    def __del__(self):
        if self.mutex is not None:
            try:
                win32api.CloseHandle(self.mutex)
            except Exception:
                pass
            self.mutex = None


# モジュールレベルで保持（GCで消えないようにする）
_single_instance: SingleInstance | None = None


def is_already_running() -> bool:
    """Windowsミューテックスで重複起動防止（1つだけ）"""
    global _single_instance
    if _single_instance is None:
        _single_instance = SingleInstance()
    if _single_instance.already_running:
        print("既に起動しています。")
        return True
    return False


def get_python_executable() -> str:
    """
    PyInstaller で exe 化した後も、本物の python.exe を返す。
    sys.executable をそのまま使うと exe 自身が再起動して増殖する。
    """
    if not getattr(sys, "frozen", False):
        return sys.executable

    import shutil

    # 1. PATH から python / python3
    for name in ("python", "python3"):
        found = shutil.which(name)
        if found:
            return found

    # 2. よくあるインストール場所
    search_roots = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        Path(r"C:\Python312"),
        Path(r"C:\Python311"),
        Path(r"C:\Python310"),
        Path(r"C:\Python39"),
        Path(sys.executable).resolve().parent,  # onedir 同梱の可能性
    ]
    for base in search_roots:
        if not base.is_dir():
            continue
        # 直下の python.exe
        exe = base / "python.exe"
        if exe.is_file():
            return str(exe)
        # Python3x サブフォルダ
        for p in sorted(base.glob("Python*"), reverse=True):
            exe = p / "python.exe"
            if exe.is_file():
                return str(exe)

    raise RuntimeError(
        "python.exe が見つかりません。"
        " Node/Eroge/HTTP サービスを起動するには Python をインストールし、PATH に追加してください。"
    )


# ---------------------------------------------------------------------------
# サービス管理
# ---------------------------------------------------------------------------
class Service:
    def __init__(self, name: str, prefix: str, color: str, cmd: list, cwd: Path | None = None):
        self.name = name
        self.prefix = prefix
        self.color = color
        self.cmd = cmd
        self.cwd = str(cwd) if cwd else None
        self.proc: subprocess.Popen | None = None
        self.thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None


class LauncherApp:
    def __init__(self):
        if is_already_running():
            sys.exit(0)

        self.log_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.services: dict[str, Service] = {}
        self.cf_url: str | None = None
        self.cf_eroge_url: str | None = None
        self.cf_stream_url: str | None = None
        self.local_ip = get_local_ip()
        self.tray_icon: pystray.Icon | None = None
        self._stop_event = threading.Event()
        self._window_visible = True

        self._build_services()
        self._build_ui()
        self._start_log_pump()

    # ----- サービス定義 -----
    def _build_services(self):
        try:
            python = get_python_executable()
            if getattr(sys, "frozen", False):
                self._log("sys", f"子プロセス用 Python: {python}")
        except RuntimeError as e:
            self._log("sys", f"[ERROR] {e}")
            python = None

        if python and (EROGE_DIR / "launch.py").is_file():
            self.services["eroge"] = Service(
                "Eroge",
                "eroge",
                "#e879f9",
                [python, "launch.py"],
                cwd=EROGE_DIR,
            )
        elif not (EROGE_DIR / "launch.py").is_file():
            self._log("sys", f"[WARN] launch.py が見つかりません: {EROGE_DIR}")
        else:
            self._log("sys", "[WARN] python.exe が無いため Eroge をスキップします")

        if python:
            self.services["http"] = Service(
                "Shelf HTTP",
                "http",
                "#38bdf8",
                [python, "-m", "RangeHTTPServer", str(PORT_SHELF), "--bind", BIND_IP],
                cwd=APP_DIR,
            )
        else:
            self._log("sys", "[WARN] python.exe が無いため Shelf HTTP をスキップします")

        if NODE_DIR.is_dir():
            pkg = NODE_DIR / "package.json"
            if not pkg.is_file():
                self._log("sys", f"[WARN] package.json が見つかりません: {NODE_DIR}")
            npm_cmd = find_npm()
            if npm_cmd:
                self.services["node"] = Service(
                    "Node",
                    "node",
                    "#4ade80",
                    npm_cmd,
                    cwd=NODE_DIR,
                )
                self._log("sys", f"Node コマンド: {npm_cmd[0]}")
            else:
                self._log(
                    "sys",
                    "[WARN] npm が見つかりません。Node サービスをスキップします。"
                    " Node.js をインストールし、PATH に追加してから再起動してください。",
                )
        else:
            self._log("sys", f"[WARN] Node ディレクトリが見つかりません: {NODE_DIR}")

        self.services["cf"] = Service(
            "Cloudflare",
            "cf",
            "#fbbf24",
            ["cloudflared", "tunnel", "--url", f"http://localhost:{PORT_SHELF}"],
            cwd=APP_DIR,
        )
        self.services["cf_eroge"] = Service(
            "Cloudflare Eroge",
            "cf_eroge",
            "#c084fc",
            ["cloudflared", "tunnel", "--url", "http://127.0.0.1:8765"],
            cwd=APP_DIR,
        )
        self.services["cf_stream"] = Service(
            "Cloudflare Stream",
            "cf_stream",
            "#f472b6",
            ["cloudflared", "tunnel", "--url", "http://127.0.0.1:8090"],
            cwd=APP_DIR,
        )

    # ----- UI -----
    def _build_ui(self):
        self.root = tk.Tk()
        self.root.title("Shelf Launcher")
        self.root.geometry("780x520")
        self.root.minsize(560, 360)
        self.root.configure(bg="#0f0f12")

        try:
            if ICON_PATH.is_file():
                self.root.iconbitmap(str(ICON_PATH))
        except Exception:
            pass

        # 通常ウィンドウ（タイトルバーにアイコン表示）。× / 閉じるは必ずトレイへ収納
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background="#0f0f12", foreground="#e4e4e7", fieldbackground="#18181b")
        style.configure("TFrame", background="#0f0f12")
        style.configure("TLabel", background="#0f0f12", foreground="#e4e4e7", font=("Segoe UI", 10))
        style.configure("TButton", font=("Segoe UI", 9), padding=6)
        style.map("TButton", background=[("active", "#27272a")])
        style.configure("Header.TLabel", font=("Segoe UI Semibold", 12), foreground="#c4b5fd")
        style.configure("Status.TLabel", font=("Consolas", 9), foreground="#a1a1aa")

        # --- ヘッダー ---
        header = ttk.Frame(self.root)
        header.pack(fill="x", padx=12, pady=(10, 4))

        ttk.Label(header, text="Shelf + Eroge Launcher", style="Header.TLabel").pack(side="left")

        self.status_var = tk.StringVar(value="待機中")
        ttk.Label(header, textvariable=self.status_var, style="Status.TLabel").pack(side="right")

        # --- URLパネル ---
        url_frame = ttk.Frame(self.root)
        url_frame.pack(fill="x", padx=12, pady=4)

        self.url_local_var = tk.StringVar(value=f"http://{self.local_ip}:{PORT_SHELF}")
        self.url_public_var = tk.StringVar(value="http://14.8.155.32:4288")  # Shelf用公開ポート（要設定）
        self.url_eroge_var = tk.StringVar(value="http://14.8.155.32:4295")

        ttk.Label(url_frame, text="Local:").grid(row=0, column=0, sticky="w", padx=(0, 4))
        ttk.Label(url_frame, textvariable=self.url_local_var, foreground="#38bdf8").grid(
            row=0, column=1, sticky="w"
        )
        ttk.Button(url_frame, text="開く", width=6, command=self.open_shelf).grid(
            row=0, column=2, padx=6
        )

        ttk.Label(url_frame, text="Public:").grid(row=1, column=0, sticky="w", padx=(0, 4), pady=(2, 0))
        ttk.Label(url_frame, textvariable=self.url_public_var, foreground="#fbbf24").grid(
            row=1, column=1, sticky="w", pady=(2, 0)
        )
        ttk.Button(url_frame, text="開く", width=6, command=self.open_public).grid(
            row=1, column=2, padx=6, pady=(2, 0)
        )

        ttk.Label(url_frame, text="Eroge:").grid(row=2, column=0, sticky="w", padx=(0, 4), pady=(2, 0))
        ttk.Label(url_frame, textvariable=self.url_eroge_var, foreground="#c084fc").grid(
            row=2, column=1, sticky="w", pady=(2, 0)
        )
        ttk.Button(url_frame, text="開く", width=6, command=self.open_eroge).grid(
            row=2, column=2, padx=6, pady=(2, 0)
        )

        # --- ボタン群（ログをコピーのみ。終了は×またはトレイメニュー）---
        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(fill="x", padx=12, pady=6)

        ttk.Button(btn_frame, text="ログをコピー", command=self.copy_log).pack(side="left", padx=(0, 4))

        # --- ログ ---
        log_frame = ttk.Frame(self.root)
        log_frame.pack(fill="both", expand=True, padx=12, pady=(0, 10))

        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            wrap="word",
            bg="#18181b",
            fg="#e4e4e7",
            insertbackground="#e4e4e7",
            font=("Consolas", 9),
            state="disabled",
            relief="flat",
            borderwidth=0,
        )
        self.log_text.pack(fill="both", expand=True)

        # タグ色
        self.log_text.tag_configure("sys", foreground="#a1a1aa")
        self.log_text.tag_configure("eroge", foreground="#e879f9")
        self.log_text.tag_configure("http", foreground="#38bdf8")
        self.log_text.tag_configure("node", foreground="#4ade80")
        self.log_text.tag_configure("cf", foreground="#fbbf24")
        self.log_text.tag_configure("cf_eroge", foreground="#c084fc")
        self.log_text.tag_configure("error", foreground="#f87171")

        self._update_url_panel()

    # ----- ログ -----
    def _log(self, prefix: str, msg: str):
        self.log_queue.put((prefix, msg))

    def _append_log(self, prefix: str, msg: str):
        self.log_text.configure(state="normal")
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] [{prefix}] {msg}\n"
        tag = prefix if prefix in ("sys", "eroge", "http", "node", "cf") else "sys"
        if "error" in msg.lower() or "warn" in msg.lower() or "[WARN]" in msg:
            tag = "error" if "error" in msg.lower() else tag
        self.log_text.insert("end", line, tag)
        # 行数制限
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > LOG_MAX_LINES:
            self.log_text.delete("1.0", f"{lines - LOG_MAX_LINES}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _start_log_pump(self):
        def pump():
            try:
                while True:
                    prefix, msg = self.log_queue.get_nowait()
                    self._append_log(prefix, msg)
            except queue.Empty:
                pass
            if not self._stop_event.is_set():
                self.root.after(80, pump)

        self.root.after(80, pump)

    def _update_url_panel(self):
        self.url_local_var.set(f"http://{self.local_ip}:{PORT_SHELF}")
        # Public / Eroge は固定値（ポートフォワーディング設定に合わせて変更）

    def copy_log(self):
        """ログ全文をクリップボードにコピー"""
        content = self.log_text.get("1.0", "end-1c")
        if not content.strip():
            messagebox.showinfo("情報", "ログが空です。")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self.root.update()
        self._log("sys", "ログをクリップボードにコピーしました")

    # ----- プロセス起動・停止 -----
    def start_service(self, key: str):
        svc = self.services.get(key)
        if not svc:
            return
        if svc.running:
            self._log("sys", f"{svc.name} は既に起動中です")
            return

        self._log(svc.prefix, f"{svc.name} を起動します…")
        try:
            creationflags = 0
            use_shell = False
            cmd = svc.cmd

            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NO_WINDOW
                # npm / npm.cmd は shell=True が必要な場合がある
                if key == "node":
                    use_shell = True
                    cmd = subprocess.list2cmdline(svc.cmd)

            # HTTPサーバーはアクセスログが大量に出るためパイプしない
            # （パイプが満杯になるとサーバーが応答しなくなり、漫画/ASMRが開けなくなる）
            capture_output = key != "http"

            popen_kwargs = {
                "cwd": svc.cwd,
                "creationflags": creationflags,
                "shell": use_shell,
            }
            if capture_output:
                # バイナリで受け取り、utf-8 / cp932 を順に試して文字化けを防ぐ
                popen_kwargs.update({
                    "stdout": subprocess.PIPE,
                    "stderr": subprocess.STDOUT,
                    "bufsize": 0,
                })
            else:
                popen_kwargs.update({
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL,
                })

            svc.proc = subprocess.Popen(cmd, **popen_kwargs)

            if capture_output:
                def _decode_line(raw: bytes) -> str:
                    for enc in ("utf-8", "cp932", "shift_jis", "euc-jp"):
                        try:
                            return raw.decode(enc)
                        except UnicodeDecodeError:
                            continue
                    return raw.decode("utf-8", errors="replace")

                def reader():
                    if svc.proc is None or svc.proc.stdout is None:
                        return
                    try:
                        for raw in iter(svc.proc.stdout.readline, b""):
                            line = _decode_line(raw).rstrip("\r\n")
                            if not line:
                                continue
                            self._log(svc.prefix, line)
                            # Cloudflare URL 抽出（成功行のみ。ERR / dest= 行は無視）
                            if "trycloudflare.com" in line and key in ("cf", "cf_eroge", "cf_stream"):
                                if any(x in line for x in ("ERR", "error=", "dest=")):
                                    continue
                                m = re.search(
                                    r"(https://[a-zA-Z0-9\-]+\.trycloudflare\.com)",
                                    line,
                                )
                                if m:
                                    url = m.group(1)
                                    if key == "cf":
                                        self.cf_url = url
                                        self._log("sys", f"Cloudflare Shelf URL: {self.cf_url}")
                                    elif key == "cf_eroge":
                                        self.cf_eroge_url = url
                                        self._log("sys", f"Cloudflare Eroge URL: {self.cf_eroge_url}")
                                        try:
                                            import json
                                            json_path = APP_DIR / "cf_eroge_url.json"
                                            json_path.write_text(
                                                json.dumps({"url": url}, ensure_ascii=False),
                                                encoding="utf-8",
                                            )
                                            self._log("sys", "cf_eroge_url.json を書き出しました")
                                        except Exception as e:
                                            self._log("sys", f"[WARN] cf_eroge_url.json 書き出し失敗: {e}")
                                    elif key == "cf_stream":
                                        self.cf_stream_url = url
                                        self._log("sys", f"Cloudflare Stream URL: {self.cf_stream_url}")
                                        try:
                                            import json
                                            json_path = APP_DIR / "cf_stream_url.json"
                                            json_path.write_text(
                                                json.dumps({"url": url}, ensure_ascii=False),
                                                encoding="utf-8",
                                            )
                                            self._log("sys", "cf_stream_url.json を書き出しました")
                                        except Exception as e:
                                            self._log("sys", f"[WARN] cf_stream_url.json 書き出し失敗: {e}")
                                    self.root.after(0, self._update_url_panel)
                    except Exception as e:
                        self._log("sys", f"[WARN] {svc.name} ログ読み取り終了: {e}")

                svc.thread = threading.Thread(target=reader, daemon=True)
                svc.thread.start()

            self._log(svc.prefix, f"{svc.name} 起動完了 (pid={svc.proc.pid})")
        except FileNotFoundError:
            self._log("sys", f"[ERROR] {svc.name} 起動失敗: コマンドが見つかりません → {svc.cmd[0]}")
            self._log("sys", "        PATH に追加されているか、インストール済みか確認してください。")
        except Exception as e:
            self._log("sys", f"[ERROR] {svc.name} 起動失敗: {e}")

    def stop_service(self, key: str):
        svc = self.services.get(key)
        if not svc or not svc.running:
            return
        self._log(svc.prefix, f"{svc.name} を停止します…")
        try:
            svc.proc.terminate()
            try:
                svc.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                svc.proc.kill()
            self._log(svc.prefix, f"{svc.name} 停止完了")
        except Exception as e:
            self._log("sys", f"[ERROR] {svc.name} 停止失敗: {e}")
        finally:
            svc.proc = None

    def _wait_port(self, name: str, port: int, timeout_sec: int = 60, interval: float = 1.0) -> bool:
        """指定ポートが開くまで待機。開始・成功・失敗・プロセス死亡時のみログ（途中の「まだ待機中」は出さない）。"""
        self._log("sys", f"{name}: port {port} の開放を待機中… (最大 {timeout_sec}s)")
        steps = max(1, int(timeout_sec / interval))
        for i in range(steps):
            if self._stop_event.is_set():
                return False
            if port_open("127.0.0.1", port, timeout=0.4):
                self._log("sys", f"{name}: port {port} 準備完了 ({(i + 1) * interval:.0f}s)")
                return True
            # プロセスが死んでいないか確認
            key_map = {PORT_EROGE: "eroge", PORT_SHELF: "http", 3000: "node"}
            key = key_map.get(port)
            if key:
                svc = self.services.get(key)
                if svc and svc.proc is not None and svc.proc.poll() is not None:
                    code = svc.proc.returncode
                    self._log("sys", f"[ERROR] {name} プロセスが終了しました (exit={code})。port {port} は開きません。")
                    return False
            time.sleep(interval)
        self._log("sys", f"[ERROR] {name}: port {port} が {timeout_sec}s 以内に開きませんでした")
        return False

    def start_all(self):
        """バックグラウンドで順次起動。launch.py (Eroge) のポート開放を待ってから次へ進む。"""

        def worker():
            self.root.after(0, lambda: self.status_var.set("サービス起動中…"))
            self._log("sys", "========== 起動シーケンス開始 ==========")

            # 前回の残骸プロセスがポートを占有していると起動できないため先に解放
            ports_to_free = [PORT_SHELF, PORT_EROGE, 3000]
            for port in ports_to_free:
                if port_open("127.0.0.1", port, timeout=0.3):
                    killed = free_port(port)
                    if killed:
                        self._log("sys", f"port {port} を使用中のプロセスを終了しました: PID {killed}")
                        time.sleep(0.6)
                    else:
                        self._log("sys", f"[WARN] port {port} は使用中ですがプロセスを特定できませんでした")

            eroge_ok = False
            http_ok = False

            # 1) Eroge (launch.py) → port 8765 が開くまで待機
            if "eroge" in self.services:
                self._log("sys", "--- [1/4] Eroge (launch.py) を起動 ---")
                self.start_service("eroge")
                eroge_ok = self._wait_port("Eroge (launch.py)", PORT_EROGE, timeout_sec=90, interval=1.0)
                if not eroge_ok:
                    self._log("sys", "[WARN] Eroge の準備が完了しませんでしたが、他サービスは続行します")
            else:
                self._log("sys", "[WARN] Eroge サービスが未登録のためスキップ")

            # 2) Shelf HTTP → port 8000 が開くまで待機
            if "http" in self.services:
                self._log("sys", "--- [2/4] Shelf HTTP を起動 ---")
                self.start_service("http")
                http_ok = self._wait_port("Shelf HTTP", PORT_SHELF, timeout_sec=20, interval=0.5)
                if not http_ok:
                    self._log("sys", "[ERROR] Shelf HTTP が応答しません。漫画・動画・ASMR が開けない可能性があります")
            else:
                self._log("sys", "[WARN] HTTP サービスが未登録のためスキップ")

            # 3) Node
            if "node" in self.services:
                self._log("sys", "--- [3/4] Node を起動 ---")
                self.start_service("node")
                # Node は落ちてもサイト本体は動くので短めに待つだけ
                node_ok = self._wait_port("Node", 3000, timeout_sec=15, interval=0.5)
                if not node_ok:
                    self._log("sys", "[WARN] Node (port 3000) が開きませんでした（サイト内ページ操作が使えない可能性）")
            else:
                self._log("sys", "--- [3/4] Node スキップ ---")

            # 4) Cloudflare Tunnel (Shelf)
            if "cf" in self.services:
                self._log("sys", "--- [4/5] Cloudflare Tunnel (Shelf) を起動 ---")
                self.start_service("cf")
            else:
                self._log("sys", "--- [4/5] Cloudflare Shelf スキップ ---")

            # 5) Cloudflare Tunnel (Eroge)
            if "cf_eroge" in self.services:
                self._log("sys", "--- [5/6] Cloudflare Tunnel (Eroge) を起動 ---")
                self.start_service("cf_eroge")
            else:
                self._log("sys", "--- [5/6] Cloudflare Eroge スキップ ---")

            # 6) Cloudflare Tunnel (Stream)
            if "cf_stream" in self.services:
                self._log("sys", "--- [6/6] Cloudflare Tunnel (Stream) を起動 ---")
                self.start_service("cf_stream")
            else:
                self._log("sys", "--- [6/6] Cloudflare Stream スキップ ---")

            # 結果まとめ
            self._log("sys", "========== 起動シーケンス完了 ==========")
            self._log("sys", f"  Eroge  (8765): {'OK' if eroge_ok else 'NG'}")
            self._log("sys", f"  HTTP   (8000): {'OK' if http_ok else 'NG'}")
            if eroge_ok and http_ok:
                status = "稼働中"
            elif http_ok:
                status = "稼働中（Eroge未応答）"
            else:
                status = "起動失敗あり（要確認）"
            self.root.after(0, lambda s=status: self.status_var.set(s))
            self._log("sys", f"状態: {status}")

        threading.Thread(target=worker, daemon=True).start()
        self.status_var.set("起動シーケンス実行中…")

    def stop_all(self):
        self.status_var.set("停止中…")
        for key in list(self.services.keys())[::-1]:
            self.stop_service(key)
        self.cf_url = None
        self._update_url_panel()
        self.status_var.set("停止済み")
        self._log("sys", "すべてのサービスを停止しました")

    # ----- ブラウザ開く -----
    def open_shelf(self):
        url = f"http://{self.local_ip}:{PORT_SHELF}"
        webbrowser.open(url)
        self._log("sys", f"ブラウザで開きました: {url}")

    def open_public(self):
        url = self.url_public_var.get()
        webbrowser.open(url)
        self._log("sys", f"ブラウザで開きました: {url}")

    def open_eroge(self):
        url = self.url_eroge_var.get()
        webbrowser.open(url)
        self._log("sys", f"ブラウザで開きました: {url}")

    def open_cf(self):
        if getattr(self, "cf_url", None):
            webbrowser.open(self.cf_url)
            self._log("sys", f"ブラウザで開きました: {self.cf_url}")
        else:
            messagebox.showinfo("情報", "Cloudflare は無効です。")

    # ----- トレイ（インジケーター）関連 -----
    def hide_to_tray(self, icon=None, item=None):
        """ウィンドウを隠し、タスクバーからも消してインジケーター（トレイ）に収納。
        ×ボタン・トレイメニューから呼ばれる。終了はしない。"""
        self.root.withdraw()  # 非表示 + タスクバーから消える
        self._window_visible = False
        self._log("sys", "インジケーターに収納しました")

    def show_window(self):
        """インジケーターから通常ウィンドウ（アイコン付き）として再表示"""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        # タイトルバー用アイコンを再設定（表示復帰後に消える環境対策）
        try:
            if ICON_PATH.is_file():
                self.root.iconbitmap(str(ICON_PATH))
        except Exception:
            pass
        self._window_visible = True

    def toggle_window(self, icon=None, item=None):
        """トレイアイコンクリックで表示 ↔ 収納を切り替え"""
        if self._window_visible:
            self.root.after(0, self.hide_to_tray)
        else:
            self.root.after(0, self.show_window)

    def quit_app(self, icon=None, item=None):
        self._stop_event.set()
        self.stop_all()
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.after(0, self.root.destroy)

    def _setup_tray(self):
        icon_img = load_icon_image(64)
        menu = pystray.Menu(
            item("表示 / 収納", self.toggle_window, default=True),
            item("Shelf を開く", lambda: self.root.after(0, self.open_shelf)),
            pystray.Menu.SEPARATOR,
            item("終了", self.quit_app),
        )
        self.tray_icon = pystray.Icon(
            "shelf_launcher",
            icon_img,
            "Shelf Launcher",
            menu,
        )
        # 左クリックでもトグル（default=True のメニュー項目が呼ばれる）
        self.tray_icon.default_action = self.toggle_window

        def run_tray():
            self.tray_icon.run()

        threading.Thread(target=run_tray, daemon=True).start()

    # ----- エントリ -----
    def run(self):
        self._setup_tray()
        self.root.after(400, self.start_all)  # 起動時に自動で全サービス開始
        self.root.mainloop()


if __name__ == "__main__":
    if is_already_running():
        print("既に起動しています。")
        sys.exit(0)

    app = LauncherApp()
    app.run()
