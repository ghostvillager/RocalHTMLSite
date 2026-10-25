# -*- coding: utf-8 -*-
"""
buttons.json 視覚編集ツール（仮想リスト化版）

改善点:
- 仮想リスト: 表示中の行だけウィジェット生成（スクロール軽量化・ざー描画解消）
- 検索フィルタ: 上部の検索ボックスで名前/フォルダ/ブランド/パスを絞り込み
- 削除: 即時反映（フル再構築なし、スクロール位置ほぼ維持）
- サムネ: 表示中のものだけ遅延読み込み
- スクロール位置: 再読込時も可能な限り保持
"""

from __future__ import annotations

import json
import re
import time
import tkinter as tk
import urllib.parse
import webbrowser
from difflib import SequenceMatcher
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    from PIL import Image, ImageTk
except ImportError:
    raise SystemExit("Pillow が必要です: pip install Pillow")

HERE = Path(__file__).resolve().parent
BUTTONS = HERE / "buttons.json"
EXCLUDED = HERE / "excluded_folders.json"
PATH_FILE = HERE / "eroge_path.json"
DISMISS_KEY = "mismatch_ok"

SKIP = (
    "uninstall", "setup", "support", "config", "setting", "crash", "report",
    "redist", "vcredist", "install", "update", "patch", "dxsetup", "unity",
    "アンインストール", "サポート", "設定", "導入", "エンジン",
)

THUMB_W, THUMB_H = 72, 96
ROW_HEIGHT = 112
SAVE_DEBOUNCE_MS = 400
COVER_POLL_MS = 1500
JSON_POLL_MS = 2500
COVER_NAME = "cover.jpg"
MIN_COVER_BYTES = 4096
MIN_MATCH_RATIO = 0.55
POOL_SIZE = 28  # 表示行＋バッファ

_thumb_cache: dict[str, ImageTk.PhotoImage | None] = {}
_THUMB_CACHE_MAX = 120


def load_buttons() -> list[dict]:
    if not BUTTONS.exists():
        return []
    try:
        data = json.loads(BUTTONS.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_buttons(buttons: list[dict]) -> None:
    BUTTONS.write_text(
        json.dumps(buttons, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_excluded() -> list[str]:
    if not EXCLUDED.is_file():
        return []
    try:
        data = json.loads(EXCLUDED.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(x) for x in data]
        if isinstance(data, dict):
            return [str(x) for x in data.get("folders", [])]
    except (json.JSONDecodeError, OSError):
        pass
    return []


def save_excluded(names: list[str]) -> None:
    uniq = sorted(set(names))
    EXCLUDED.write_text(
        json.dumps(uniq, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def add_excluded(folder_name: str) -> None:
    if not folder_name:
        return
    cur = load_excluded()
    if folder_name not in cur:
        cur.append(folder_name)
        save_excluded(cur)


def remove_excluded(folder_name: str) -> None:
    cur = [x for x in load_excluded() if x != folder_name]
    save_excluded(cur)


def eroge_root() -> Path | None:
    if PATH_FILE.exists():
        try:
            text = json.loads(PATH_FILE.read_text(encoding="utf-8")).get("path", "").strip()
            if text and Path(text).is_dir():
                return Path(text)
        except (json.JSONDecodeError, OSError):
            pass
    return None


def exe_candidates(folder: Path) -> list[Path]:
    return [
        exe for exe in folder.rglob("*.exe")
        if not any(w in exe.stem.casefold() for w in SKIP)
    ]


def pick_exe(folder: Path, title: str) -> str:
    exes = exe_candidates(folder)
    if not exes:
        return ""

    def score(exe: Path) -> float:
        n = exe.stem.casefold()
        points = max(
            SequenceMatcher(None, n, title.casefold()).ratio(),
            SequenceMatcher(None, n, folder.name.casefold()).ratio(),
        )
        if "64" in n:
            points += 0.2
        if "32" in n or "86" in n:
            points -= 0.1
        return points

    return str(max(exes, key=score))


def cover_dest_dir(btn: dict, root: Path | None) -> Path | None:
    exe = (btn.get("exe") or "").strip()
    if exe:
        p = Path(exe)
        if p.parent.is_dir():
            return p.parent
    folder = (btn.get("folder") or "").strip()
    if root and folder:
        d = root / folder
        if d.is_dir():
            return d
    return None


def folder_full_path(btn: dict, root: Path | None) -> str:
    folder = (btn.get("folder") or "").strip()
    if root and folder:
        return (root / folder).as_posix()
    exe = (btn.get("exe") or "").strip()
    if exe:
        try:
            return Path(exe).parent.as_posix()
        except Exception:
            pass
    return folder or "（不明）"


def _norm(text: str) -> str:
    t = text.casefold()
    t = t.replace("_", "").replace(" ", "").replace("　", "")
    t = t.replace("＊", "*").replace("☆", "").replace("★", "")
    t = t.replace("〜", "~").replace("～", "~")
    t = t.replace("－", "-").replace("—", "-").replace("・", "")
    t = re.sub(r"[（(][^）)]*[）)]", "", t)
    t = re.sub(r"[【\[][^】\]]*[】\]]", "", t)
    t = re.sub(r"[^\w\*~\-]", "", t, flags=re.UNICODE)
    return t


def _longest_common_substr_len(a: str, b: str) -> int:
    if not a or not b:
        return 0
    if len(a) > len(b):
        a, b = b, a
    best = 0
    for i in range(len(a)):
        for j in range(i + 1, len(a) + 1):
            sub = a[i:j]
            if len(sub) > best and sub in b:
                best = len(sub)
    return best


def is_name_folder_mismatch(name: str, folder: str) -> bool:
    n = _norm(name or "")
    f = _norm(folder or "")
    if not n or not f:
        return False
    if n == f:
        return False
    ratio = SequenceMatcher(None, n, f).ratio()
    shorter, longer = (n, f) if len(n) <= len(f) else (f, n)
    if len(shorter) >= 3 and shorter in longer and len(shorter) >= len(longer) * 0.45:
        return False
    tokens_n = set(re.findall(r"[a-z0-9]{3,}", n))
    tokens_f = set(re.findall(r"[a-z0-9]{3,}", f))
    if tokens_n and tokens_f and (tokens_n & tokens_f) and ratio >= 0.4:
        return False
    lcs = _longest_common_substr_len(n, f)
    if len(shorter) >= 4 and lcs >= max(4, int(len(shorter) * 0.6)):
        return False
    if ratio >= MIN_MATCH_RATIO:
        return False
    return True


def cover_stamp(path: str | Path | None) -> str:
    if not path:
        return "0"
    try:
        st = Path(path).stat()
        return f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        return "0"


def resolve_cover_path(btn: dict, root: Path | None) -> str:
    cov = (btn.get("cover") or "").strip()
    if cov and Path(cov).is_file():
        try:
            if Path(cov).stat().st_size >= MIN_COVER_BYTES:
                return cov
        except OSError:
            pass
    dest = cover_dest_dir(btn, root)
    if not dest or not dest.is_dir():
        return cov
    candidates = []
    try:
        for cand in dest.iterdir():
            if not cand.is_file():
                continue
            if cand.stem.casefold() in ("cover", "folder") or cand.name.casefold().startswith("cover."):
                try:
                    if cand.stat().st_size >= MIN_COVER_BYTES:
                        candidates.append(cand)
                except OSError:
                    pass
    except OSError:
        return cov
    if not candidates:
        return cov
    return str(max(candidates, key=lambda p: p.stat().st_size))


def load_thumb(path: str | Path | None, size=(THUMB_W, THUMB_H)) -> ImageTk.PhotoImage | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    st = cover_stamp(p)
    key = f"{p}|{st}|{size[0]}x{size[1]}"
    if key in _thumb_cache:
        return _thumb_cache[key]
    try:
        img = Image.open(p)
        img = img.convert("RGB")
        img.thumbnail(size, Image.Resampling.BILINEAR)
        canvas = Image.new("RGB", size, (30, 30, 36))
        canvas.paste(img, ((size[0] - img.width) // 2, (size[1] - img.height) // 2))
        photo = ImageTk.PhotoImage(canvas)
    except Exception:
        photo = None
    if len(_thumb_cache) >= _THUMB_CACHE_MAX:
        for k in list(_thumb_cache.keys())[: _THUMB_CACHE_MAX // 2]:
            _thumb_cache.pop(k, None)
    _thumb_cache[key] = photo
    return photo


def save_cover_as_jpg(src: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / COVER_NAME
    for old in dest_dir.glob("cover.*"):
        if old.resolve() != dest.resolve():
            try:
                old.unlink()
            except OSError:
                pass
    img = Image.open(src)
    if img.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", img.size, (0, 0, 0))
        if img.mode == "P":
            img = img.convert("RGBA")
        if img.mode in ("RGBA", "LA"):
            bg.paste(img.convert("RGB"), mask=img.split()[-1])
            img = bg
        else:
            img = img.convert("RGB")
    elif img.mode != "RGB":
        img = img.convert("RGB")
    img.save(dest, "JPEG", quality=90, optimize=True)
    return dest


class Row:
    def __init__(self, app: "EditorApp", parent: tk.Widget):
        self.app = app
        self.index = -1
        self.btn: dict = {}
        self.photo: ImageTk.PhotoImage | None = None
        self._applying_folder = False
        self._updating_checks = False
        self._cover_stamp = "pending"
        self._thumb_loaded = False

        self.frame = ttk.Frame(parent, padding=(2, 2))
        self.frame.columnconfigure(2, weight=1)
        # 固定高さで重ねを防ぐ
        self.frame.configure(height=ROW_HEIGHT)
        self.frame.grid_propagate(False)

        self.thumb_lbl = tk.Label(
            self.frame, text="…", width=10, height=6,
            bg="#1a1a22", fg="#666", relief="sunken",
        )
        self.thumb_lbl.grid(row=0, column=0, rowspan=5, padx=(0, 6), sticky="ns")

        # 名前
        ttk.Label(self.frame, text="名前").grid(row=0, column=1, sticky="e", padx=(0, 4))
        name_row = ttk.Frame(self.frame)
        name_row.grid(row=0, column=2, sticky="ew", padx=(0, 4))
        name_row.columnconfigure(0, weight=1)
        self.name_var = tk.StringVar()
        self.name_entry = ttk.Entry(name_row, textvariable=self.name_var)
        self.name_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(name_row, text="検索", command=self.search_name, width=5).grid(
            row=0, column=1, padx=(4, 0)
        )

        # フォルダ名
        ttk.Label(self.frame, text="フォルダ").grid(row=1, column=1, sticky="e", padx=(0, 4))
        folder_row = ttk.Frame(self.frame)
        folder_row.grid(row=1, column=2, sticky="ew", padx=(0, 4))
        folder_row.columnconfigure(0, weight=1)
        self.folder_var = tk.StringVar()
        ttk.Label(folder_row, textvariable=self.folder_var, foreground="#444").grid(
            row=0, column=0, sticky="w"
        )
        self.warn_lbl = tk.Label(folder_row, text="", fg="#e74c3c", font=("", 9, "bold"))
        self.warn_lbl.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.mismatch_actions = ttk.Frame(folder_row)
        self.mismatch_actions.grid(row=0, column=2, sticky="w")
        self.ok_var = tk.BooleanVar(value=False)
        self.wrong_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self.mismatch_actions, text="不一致を無視",
            variable=self.ok_var, command=self._on_ignore_toggle,
        ).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(
            self.mismatch_actions, text="間違い",
            variable=self.wrong_var, command=self._on_wrong_toggle,
        ).pack(side="left", padx=(4, 0))

        # フルパス
        ttk.Label(self.frame, text="パス").grid(row=2, column=1, sticky="e", padx=(0, 4))
        self.path_var = tk.StringVar()
        self.path_entry = ttk.Entry(self.frame, textvariable=self.path_var)
        self.path_entry.grid(row=2, column=2, sticky="ew", padx=(0, 4))
        self.path_entry.state(["readonly"])

        # ブランド
        ttk.Label(self.frame, text="ブランド").grid(row=3, column=1, sticky="e", padx=(0, 4))
        self.brand_var = tk.StringVar()
        ttk.Entry(self.frame, textvariable=self.brand_var).grid(
            row=3, column=2, sticky="ew", padx=(0, 4)
        )

        extra = ttk.Frame(self.frame)
        extra.grid(row=4, column=1, columnspan=2, sticky="ew", padx=(0, 4))
        ttk.Label(extra, text="日付").pack(side="left")
        self.date_var = tk.StringVar()
        ttk.Entry(extra, textvariable=self.date_var, width=12).pack(side="left", padx=(4, 8))
        ttk.Label(extra, text="中央値").pack(side="left")
        self.median_var = tk.StringVar()
        ttk.Entry(extra, textvariable=self.median_var, width=6).pack(side="left", padx=(4, 8))
        self.exe_hint = ttk.Label(extra, text="", foreground="#888")
        self.exe_hint.pack(side="left")

        btns = ttk.Frame(self.frame)
        btns.grid(row=0, column=3, rowspan=5, sticky="ns", padx=(6, 0))
        ttk.Button(btns, text="カバー…", command=self.browse_cover, width=10).pack(pady=1)
        ttk.Button(btns, text="削除", command=self.delete_row, width=10).pack(pady=1)

        for var in (self.name_var, self.brand_var, self.date_var, self.median_var):
            var.trace_add("write", self._on_field_change)
        self.name_var.trace_add("write", lambda *_: self._on_name_edited())

    def set_button(self, btn: dict, index: int):
        """プール再利用時にデータを差し替え"""
        self.btn = btn
        self.index = index
        self._applying_folder = True
        self._updating_checks = True
        try:
            self.name_var.set(btn.get("name", ""))
            self.folder_var.set((btn.get("folder") or "").strip())
            self.brand_var.set(btn.get("brand", ""))
            self.date_var.set(btn.get("date", ""))
            self.median_var.set(btn.get("median", ""))
            self.path_var.set(folder_full_path(btn, self.app.root_path))
            self.ok_var.set(bool(btn.get(DISMISS_KEY)))
            self.wrong_var.set(False)
        finally:
            self._applying_folder = False
            self._updating_checks = False
        self._thumb_loaded = False
        self._cover_stamp = "pending"
        self._refresh_exe_hint()
        self._refresh_mismatch()
        # サムネは後でバッチ
        self.thumb_lbl.configure(image="", text="…", width=10, height=6, fg="#666")

    def load_thumb_now(self):
        if self._thumb_loaded or not self.btn:
            return
        self._thumb_loaded = True
        cov = resolve_cover_path(self.btn, self.app.root_path)
        if cov and cov != (self.btn.get("cover") or ""):
            self.btn["cover"] = cov
        self._cover_stamp = cover_stamp(cov)
        self.photo = load_thumb(cov)
        if self.photo:
            self.thumb_lbl.configure(image=self.photo, text="", width=THUMB_W, height=THUMB_H)
        else:
            self.thumb_lbl.configure(image="", text="no image", width=10, height=6, fg="#666")

    def refresh_cover_if_changed(self) -> bool:
        if not self.btn:
            return False
        cov = resolve_cover_path(self.btn, self.app.root_path)
        if cov and cov != (self.btn.get("cover") or ""):
            self.btn["cover"] = cov
        st = cover_stamp(cov)
        if st == self._cover_stamp and self._thumb_loaded:
            return False
        self._cover_stamp = st
        self._thumb_loaded = True
        self.photo = load_thumb(cov)
        if self.photo:
            self.thumb_lbl.configure(image=self.photo, text="", width=THUMB_W, height=THUMB_H)
        else:
            self.thumb_lbl.configure(image="", text="no image", width=10, height=6, fg="#666")
        return True

    def _refresh_exe_hint(self):
        exe = self.btn.get("exe") or ""
        self.exe_hint.configure(text=f"exe: {Path(exe).name}" if exe else "exe: （未設定）")

    def _refresh_mismatch(self):
        name = self.name_var.get()
        folder = self.folder_var.get()
        bad = is_name_folder_mismatch(name, folder)
        ignored = self.ok_var.get()
        if not bad:
            self.warn_lbl.configure(text="")
            self.name_entry.configure(foreground="")
            self.mismatch_actions.grid_remove()
            self._updating_checks = True
            try:
                self.ok_var.set(False)
                self.wrong_var.set(False)
            finally:
                self._updating_checks = False
            self.btn.pop(DISMISS_KEY, None)
            return
        self.mismatch_actions.grid()
        if ignored:
            self.warn_lbl.configure(text="")
            self.name_entry.configure(foreground="")
        else:
            self.warn_lbl.configure(text="※不一致")
            self.name_entry.configure(foreground="#e74c3c")

    def _on_ignore_toggle(self):
        if self._updating_checks:
            return
        if self.ok_var.get():
            self._updating_checks = True
            try:
                self.wrong_var.set(False)
            finally:
                self._updating_checks = False
            self.btn[DISMISS_KEY] = True
        else:
            self.btn.pop(DISMISS_KEY, None)
        self._refresh_mismatch()
        self.app.schedule_save()

    def _on_wrong_toggle(self):
        if self._updating_checks or not self.wrong_var.get():
            return
        folder = self.folder_var.get()
        if not folder:
            self.wrong_var.set(False)
            return
        self._applying_folder = True
        self._updating_checks = True
        try:
            self.name_var.set(folder)
            self.btn["name"] = folder
            self.ok_var.set(False)
            self.btn.pop(DISMISS_KEY, None)
            self.wrong_var.set(False)
        finally:
            self._applying_folder = False
            self._updating_checks = False
        self._refresh_mismatch()
        self.app.schedule_save()

    def _on_name_edited(self):
        if not self._applying_folder:
            self._refresh_mismatch()

    def search_name(self):
        name = self.name_var.get().strip()
        if name:
            webbrowser.open(
                "https://www.google.com/search?q="
                + urllib.parse.quote_plus(f"{name} エロゲ")
            )

    def _on_field_change(self, *_):
        if self._applying_folder or not self.btn:
            return
        self.btn["name"] = self.name_var.get()
        self.btn["brand"] = self.brand_var.get()
        self.btn["date"] = self.date_var.get()
        self.btn["median"] = self.median_var.get()
        self.app.schedule_save()

    def collect(self) -> dict:
        d = dict(self.btn)
        d["name"] = self.name_var.get().strip()
        d["brand"] = self.brand_var.get().strip()
        d["date"] = self.date_var.get().strip()
        d["median"] = self.median_var.get().strip()
        d["folder"] = self.folder_var.get().strip()
        if is_name_folder_mismatch(d["name"], d["folder"]) and self.ok_var.get():
            d[DISMISS_KEY] = True
        else:
            d.pop(DISMISS_KEY, None)
        return d

    def browse_cover(self):
        path = filedialog.askopenfilename(
            title="カバー画像を選択",
            filetypes=[("画像", "*.jpg *.jpeg *.png *.webp *.gif *.bmp *.avif"), ("すべて", "*.*")],
        )
        if not path:
            return
        dest_dir = cover_dest_dir(self.btn, self.app.root_path)
        if not dest_dir:
            messagebox.showerror("保存先不明", "exe / フォルダが見つかりません。")
            return
        try:
            saved = save_cover_as_jpg(Path(path), dest_dir)
        except Exception as e:
            messagebox.showerror("変換失敗", str(e))
            return
        self.btn["cover"] = str(saved)
        self._thumb_loaded = False
        self._cover_stamp = "force"
        self.refresh_cover_if_changed()
        self.app.schedule_save()

    def delete_row(self):
        name = self.name_var.get() or self.folder_var.get() or "?"
        folder = self.folder_var.get().strip()
        if not messagebox.askyesno(
            "削除",
            f"「{name}」を一覧から削除しますか？\n"
            f"フォルダ: {folder}\n\n"
            "実フォルダは残りますが、launch 側にも再表示されません。",
        ):
            return
        self.app.remove_row(self.btn, folder)


class EditorApp:
    def __init__(self):
        self.win = tk.Tk()
        self.win.title("buttons.json 編集")
        self.win.geometry("1040x680")
        self.win.minsize(760, 420)

        self.root_path = eroge_root()
        excl = set(load_excluded())
        self.all_buttons: list[dict] = [
            b for b in load_buttons() if b.get("folder") not in excl
        ]
        self.filtered: list[dict] = list(self.all_buttons)
        self._save_after_id: str | None = None
        self._dirty = False
        self._saving = False
        self._json_mtime = self._file_mtime(BUTTONS)
        self._thumb_queue: list[Row] = []
        self._pool: list[Row] = []
        self._window_ids: list[int] = []
        self._last_first = -1

        self._build_ui()
        self._ensure_pool()
        self._update_scrollregion()
        self._refresh_view()
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)
        self.win.after(COVER_POLL_MS, self._poll_covers)
        self.win.after(JSON_POLL_MS, self._poll_json)
        self.win.after(80, self._load_thumbs_batch)

    @staticmethod
    def _file_mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime if path.is_file() else 0.0
        except OSError:
            return 0.0

    def _build_ui(self):
        top = ttk.Frame(self.win, padding=6)
        top.pack(fill="x")
        root_txt = str(self.root_path) if self.root_path else "（eroge_path.json 未設定）"
        ttk.Label(top, text=f"親フォルダ: {root_txt}", foreground="#555").pack(side="left")

        # 検索
        ttk.Label(top, text="検索:").pack(side="left", padx=(16, 4))
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(top, textvariable=self.search_var, width=28)
        self.search_entry.pack(side="left")
        self.search_var.trace_add("write", lambda *_: self._schedule_filter())
        ttk.Button(top, text="クリア", command=self._clear_search, width=6).pack(side="left", padx=4)

        ttk.Button(top, text="新規追加…", command=self.add_new).pack(side="right", padx=4)
        ttk.Button(top, text="再読込", command=self.reload).pack(side="right", padx=4)
        self.status = tk.StringVar(value="自動保存オン")
        ttk.Label(top, textvariable=self.status, foreground="#2a7").pack(side="right", padx=12)

        outer = ttk.Frame(self.win)
        outer.pack(fill="both", expand=True, padx=6, pady=(0, 4))
        self.canvas = tk.Canvas(outer, highlightthickness=0, bg="#f4f4f6")
        self.sb = ttk.Scrollbar(outer, orient="vertical", command=self._yview)
        self.canvas.configure(yscrollcommand=self.sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.sb.pack(side="right", fill="y")

        self.canvas.bind("<Configure>", self._on_canvas_configure)
        # マウスホイール: 子ウィジェット上でも確実に効くよう Enter/Leave で bind_all
        self.canvas.bind("<Enter>", self._bind_mousewheel)
        self.canvas.bind("<Leave>", self._unbind_mousewheel)

        ttk.Label(
            self.win,
            text="上部の検索で絞り込み可能。削除は即時反映（一覧から除去＋除外登録）。実フォルダは残ります。"
                 " スクロールは軽量化済み。",
            foreground="#666",
            wraplength=1000,
        ).pack(fill="x", padx=6, pady=(0, 4))

    def _clear_search(self):
        self.search_var.set("")

    def _schedule_filter(self):
        if hasattr(self, "_filter_after") and self._filter_after:
            try:
                self.win.after_cancel(self._filter_after)
            except tk.TclError:
                pass
        self._filter_after = self.win.after(180, self._apply_filter)

    def _apply_filter(self):
        self._filter_after = None
        q = self.search_var.get().strip().casefold()
        if not q:
            self.filtered = list(self.all_buttons)
        else:
            self.filtered = [
                b for b in self.all_buttons
                if q in (b.get("name") or "").casefold()
                or q in (b.get("folder") or "").casefold()
                or q in (b.get("brand") or "").casefold()
                or q in folder_full_path(b, self.root_path).casefold()
            ]
        # 検索時は先頭へ
        self.canvas.yview_moveto(0)
        self._update_scrollregion()
        self._refresh_view()
        self.set_status(f"{len(self.filtered)} / {len(self.all_buttons)} 件  |  自動保存オン")

    def set_status(self, msg: str):
        self.status.set(msg)

    def _ensure_pool(self):
        while len(self._pool) < POOL_SIZE:
            row = Row(self, self.canvas)
            wid = self.canvas.create_window(
                0, 0, window=row.frame, anchor="nw",
                width=self.canvas.winfo_width() or 900,
                height=ROW_HEIGHT,
            )
            self._pool.append(row)
            self._window_ids.append(wid)
            # 最初は画面外
            self.canvas.coords(wid, -2000, -2000)

    def _update_scrollregion(self):
        total = max(1, len(self.filtered) * ROW_HEIGHT)
        w = max(1, self.canvas.winfo_width())
        self.canvas.configure(scrollregion=(0, 0, w, total))

    def _on_canvas_configure(self, event=None):
        w = self.canvas.winfo_width()
        for wid in self._window_ids:
            self.canvas.itemconfigure(wid, width=w)
        self._update_scrollregion()
        self._refresh_view()

    def _bind_mousewheel(self, event=None):
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind_all("<Button-4>", lambda e: self._on_mousewheel_linux(-1))
        self.canvas.bind_all("<Button-5>", lambda e: self._on_mousewheel_linux(1))

    def _unbind_mousewheel(self, event=None):
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _yview(self, *args):
        self.canvas.yview(*args)
        self._refresh_view()

    def _on_mousewheel(self, event):
        # Windows / Mac: delta は通常 ±120
        delta = int(-1 * (event.delta / 120))
        if delta == 0:
            delta = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(delta, "units")
        self._refresh_view()

    def _on_mousewheel_linux(self, direction):
        self.canvas.yview_scroll(direction, "units")
        self._refresh_view()

    def _refresh_view(self):
        if not self.filtered:
            for wid in self._window_ids:
                self.canvas.coords(wid, -2000, -2000)
            self.set_status("0 件")
            return
        try:
            y0 = self.canvas.canvasy(0)
        except Exception:
            y0 = 0
        h = max(1, self.canvas.winfo_height())
        first = max(0, int(y0 // ROW_HEIGHT) - 3)
        last = min(len(self.filtered), int((y0 + h) // ROW_HEIGHT) + 5)
        if first == self._last_first and last - first == len([r for r in self._pool if r.index >= 0]):
            # ほぼ同じ範囲なら位置だけ微調整
            pass
        self._last_first = first

        needed = last - first
        w = self.canvas.winfo_width() or 900
        for i in range(needed):
            idx = first + i
            if idx >= len(self.filtered):
                break
            row = self._pool[i]
            btn = self.filtered[idx]
            if row.btn is not btn or row.index != idx:
                row.set_button(btn, idx)
                self._thumb_queue.append(row)
            y = idx * ROW_HEIGHT
            wid = self._window_ids[i]
            self.canvas.coords(wid, 0, y)
            self.canvas.itemconfigure(wid, width=w, height=ROW_HEIGHT)
        # 余りを画面外へ
        for i in range(needed, len(self._pool)):
            self.canvas.coords(self._window_ids[i], -2000, -2000)
            self._pool[i].index = -1
            self._pool[i].btn = {}

    def _load_thumbs_batch(self):
        n = 0
        while self._thumb_queue and n < 4:
            try:
                r = self._thumb_queue.pop(0)
                if r.index >= 0:  # まだ表示中
                    r.load_thumb_now()
            except Exception:
                pass
            n += 1
        if self._thumb_queue:
            self.win.after(30, self._load_thumbs_batch)

    def _poll_covers(self):
        try:
            c = 0
            for r in self._pool:
                if r.index >= 0 and r.refresh_cover_if_changed():
                    c += 1
            if c:
                self.set_status(f"カバー更新 {c}件  {time.strftime('%H:%M:%S')}")
        except Exception:
            pass
        self.win.after(COVER_POLL_MS, self._poll_covers)

    def _poll_json(self):
        try:
            if not self._dirty and not self._saving:
                mt = self._file_mtime(BUTTONS)
                if mt and mt != self._json_mtime:
                    self._json_mtime = mt
                    # スクロール位置を記憶
                    try:
                        frac = self.canvas.yview()[0]
                    except Exception:
                        frac = 0.0
                    excl = set(load_excluded())
                    self.all_buttons = [b for b in load_buttons() if b.get("folder") not in excl]
                    self._apply_filter()
                    self.canvas.yview_moveto(frac)
                    self._refresh_view()
        except Exception:
            pass
        self.win.after(JSON_POLL_MS, self._poll_json)

    def schedule_save(self):
        self._dirty = True
        self.set_status("保存待ち…")
        if self._save_after_id is not None:
            try:
                self.win.after_cancel(self._save_after_id)
            except tk.TclError:
                pass
        self._save_after_id = self.win.after(SAVE_DEBOUNCE_MS, self._do_save)

    def _do_save(self):
        self._save_after_id = None
        if self._saving:
            return
        self._saving = True
        try:
            # 現在表示中の行の最新値を反映（念のため）
            for r in self._pool:
                if r.index >= 0 and r.btn:
                    r.collect()  # 副作用で dict 更新済みだが明示
            save_buttons(self.all_buttons)
            self._json_mtime = self._file_mtime(BUTTONS)
            self._dirty = False
            self.set_status(f"保存済み  {time.strftime('%H:%M:%S')}  ({len(self.all_buttons)} 件)")
        except Exception as e:
            messagebox.showerror("保存失敗", str(e))
        finally:
            self._saving = False

    def remove_row(self, btn: dict, folder_name: str):
        """即時削除（仮想リストなので再描画ほぼゼロコスト）"""
        if btn in self.all_buttons:
            self.all_buttons.remove(btn)
        if folder_name:
            add_excluded(folder_name)
        try:
            save_buttons(self.all_buttons)
            self._json_mtime = self._file_mtime(BUTTONS)
            self._dirty = False
        except Exception as e:
            messagebox.showerror("保存失敗", str(e))
            return
        # フィルタ再適用（位置は維持）
        try:
            frac = self.canvas.yview()[0]
        except Exception:
            frac = 0.0
        self._apply_filter()
        self.canvas.yview_moveto(frac)
        self._refresh_view()
        self.set_status(f"削除済み: {folder_name}  ({len(self.all_buttons)} 件)")

    def reload(self):
        if self._dirty and not messagebox.askyesno("再読込", "未保存を破棄しますか？"):
            return
        try:
            frac = self.canvas.yview()[0]
        except Exception:
            frac = 0.0
        excl = set(load_excluded())
        self.all_buttons = [b for b in load_buttons() if b.get("folder") not in excl]
        self._json_mtime = self._file_mtime(BUTTONS)
        self._dirty = False
        self._apply_filter()
        self.canvas.yview_moveto(frac)
        self._refresh_view()
        self.set_status(f"再読込完了  {len(self.all_buttons)} 件")

    def add_new(self):
        root = self.root_path
        if not root or not root.is_dir():
            messagebox.showerror("親フォルダなし", "eroge_path.json を確認してください。")
            return
        existing = {b.get("folder") for b in self.all_buttons}
        excl = set(load_excluded())
        candidates = sorted(
            p for p in root.iterdir()
            if p.is_dir() and (p.name not in existing)
        )
        if not candidates:
            messagebox.showinfo("追加なし", "未登録フォルダがありません。")
            return

        dlg = tk.Toplevel(self.win)
        dlg.title("新規ボタン追加")
        dlg.transient(self.win)
        dlg.grab_set()
        dlg.geometry("640x400")

        ttk.Label(dlg, text="フォルダを選択（パス付き）").pack(anchor="w", padx=12, pady=(12, 4))
        listbox = tk.Listbox(dlg, height=12, exportselection=False)
        listbox.pack(fill="both", expand=True, padx=12)
        for p in candidates:
            mark = " [除外中]" if p.name in excl else ""
            listbox.insert("end", f"{p.name}{mark}  |  {p.as_posix()}")

        form = ttk.Frame(dlg, padding=12)
        form.pack(fill="x")
        ttk.Label(form, text="名前 *").grid(row=0, column=0, sticky="e", padx=4)
        name_var = tk.StringVar()
        ttk.Entry(form, textvariable=name_var, width=40).grid(row=0, column=1, sticky="ew")
        ttk.Label(form, text="ブランド").grid(row=1, column=0, sticky="e", padx=4)
        brand_var = tk.StringVar()
        ttk.Entry(form, textvariable=brand_var, width=40).grid(row=1, column=1, sticky="ew")
        cover_path: list[Path | None] = [None]
        cover_lbl = ttk.Label(form, text="カバー: （未選択）", foreground="#888")
        cover_lbl.grid(row=2, column=0, columnspan=2, sticky="w", pady=4)

        def pick_cover():
            path = filedialog.askopenfilename(
                title="カバー画像", parent=dlg,
                filetypes=[("画像", "*.jpg *.jpeg *.png *.webp *.gif *.bmp *.avif"), ("すべて", "*.*")],
            )
            if path:
                cover_path[0] = Path(path)
                cover_lbl.configure(text=f"カバー: {Path(path).name}")

        ttk.Button(form, text="カバー参照… *", command=pick_cover).grid(row=3, column=1, sticky="w")
        form.columnconfigure(1, weight=1)

        def do_add():
            sel = listbox.curselection()
            if not sel:
                messagebox.showwarning("未選択", "フォルダを選んでください。", parent=dlg)
                return
            folder_name = candidates[sel[0]].name
            name = name_var.get().strip()
            if not name or not cover_path[0]:
                messagebox.showwarning("必須", "名前とカバーが必要です。", parent=dlg)
                return
            folder = root / folder_name
            exe = pick_exe(folder, name)
            dest_dir = Path(exe).parent if exe else folder
            try:
                saved = save_cover_as_jpg(cover_path[0], dest_dir)
            except Exception as e:
                messagebox.showerror("変換失敗", str(e), parent=dlg)
                return
            remove_excluded(folder_name)
            btn = {
                "name": name,
                "folder": folder_name,
                "exe": exe,
                "cover": str(saved),
                "brand": brand_var.get().strip(),
                "date": "",
                "median": "",
                "url": "",
            }
            if is_name_folder_mismatch(name, folder_name):
                btn[DISMISS_KEY] = True
            self.all_buttons.append(btn)
            save_buttons(self.all_buttons)
            self._json_mtime = self._file_mtime(BUTTONS)
            dlg.destroy()
            self._apply_filter()
            self.set_status(f"追加: {name}")

        bottom = ttk.Frame(dlg, padding=12)
        bottom.pack(fill="x")
        ttk.Button(bottom, text="追加", command=do_add).pack(side="right", padx=4)
        ttk.Button(bottom, text="キャンセル", command=dlg.destroy).pack(side="right")

    def _on_close(self):
        if self._save_after_id is not None:
            try:
                self.win.after_cancel(self._save_after_id)
            except tk.TclError:
                pass
        if self._dirty:
            try:
                self._do_save()
            except Exception:
                pass
        self.win.destroy()

    def run(self):
        self.win.mainloop()


if __name__ == "__main__":
    EditorApp().run()
