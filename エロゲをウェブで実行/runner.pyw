# -*- coding: utf-8 -*-
"""Sunshine から呼ばれるランナー。

first_run.json は「ゲーム（ボタン）ごと」の初回フラグ。
  例:
  {
    "某タイトル": {"status": "OK", "exe": "C:\\games\\foo\\game.exe"},
    "別タイトル": {"status": "NG"}
  }

- selected.txt の name が first_run.json に無い、または status が OK でない
  → そのボタン登録フォルダを開いて exe 選択ダイアログ → 起動・終了監視
  → buttons.json の exe を更新し、first_run.json にそのボタン分を OK で書き込む
  → 終了後に force_stop_capture.bat で Sunshine キャプチャを停止
- すでに OK なら selected.txt + buttons.json の exe で通常起動
"""
import json
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
STATE = BASE / "session.json"
SELECTED = BASE / "selected.txt"
BUTTONS = BASE / "buttons.json"
FIRST_RUN = BASE / "first_run.json"
PATH_FILE = BASE / "eroge_path.json"
FORCE_STOP_BAT = BASE / "force_stop_capture.bat"


def write_state(status, app=""):
    STATE.write_text(
        json.dumps({"status": status, "app": app}, ensure_ascii=False),
        encoding="utf-8",
    )


def load_first_run() -> dict:
    """ゲーム名 → {status, exe} の辞書。無ければ空。"""
    if not FIRST_RUN.exists():
        return {}
    try:
        data = json.loads(FIRST_RUN.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return data
    except (json.JSONDecodeError, OSError):
        return {}


def save_first_run(data: dict):
    FIRST_RUN.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def is_first_for(game_name: str) -> bool:
    """そのボタンが初回扱いなら True（エントリ無し / status が OK 以外）。"""
    if not game_name:
        return True
    data = load_first_run()
    entry = data.get(game_name)
    if not isinstance(entry, dict):
        return True
    return entry.get("status") != "OK"


def mark_done(game_name: str, exe: str):
    """そのボタンを初回完了（OK）として first_run.json に書き込む。"""
    data = load_first_run()
    data[game_name] = {"status": "OK", "exe": exe}
    save_first_run(data)


def load_buttons() -> list:
    if not BUTTONS.exists():
        return []
    try:
        return json.loads(BUTTONS.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def save_buttons(buttons: list):
    BUTTONS.write_text(
        json.dumps(buttons, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def eroge_root() -> Path | None:
    if not PATH_FILE.exists():
        return None
    try:
        text = json.loads(PATH_FILE.read_text(encoding="utf-8")).get("path", "").strip()
    except (json.JSONDecodeError, OSError):
        return None
    if text and Path(text).is_dir():
        return Path(text)
    return None


def find_button(game_name: str) -> dict | None:
    for b in load_buttons():
        if b.get("name") == game_name:
            return b
    return None


def registered_dir(button: dict | None) -> str:
    """ボタン登録内容からダイアログ初期ディレクトリを決める。

    優先順:
      1. 登録済み exe の親フォルダ（実在する場合）
      2. eroge_path.json のルート + folder 名
      3. exe パスから辿れる親
      4. BASE
    """
    if not button:
        return str(BASE)

    exe = (button.get("exe") or "").strip()
    if exe:
        p = Path(exe)
        if p.is_file() and p.parent.is_dir():
            return str(p.parent)
        if p.parent.is_dir():
            return str(p.parent)

    folder_name = (button.get("folder") or "").strip()
    if folder_name:
        root = eroge_root()
        if root is not None:
            game_dir = root / folder_name
            if game_dir.is_dir():
                return str(game_dir)

    if exe:
        parent = Path(exe).parent
        if str(parent) and str(parent) != ".":
            return str(parent)

    return str(BASE)


def pick_exe_dialog(initial_dir: str = "") -> str:
    import tkinter as tk
    from tkinter import filedialog

    start = initial_dir if initial_dir and Path(initial_dir).is_dir() else str(BASE)

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    path = filedialog.askopenfilename(
        title="起動する実行ファイルを選択（このゲームは初回）",
        initialdir=start,
        filetypes=[
            ("実行ファイル", "*.exe"),
            ("すべて", "*.*"),
        ],
    )
    root.destroy()
    return path or ""


def update_button_exe(game_name: str, exe: str) -> None:
    buttons = load_buttons()
    changed = False
    for b in buttons:
        if b.get("name") == game_name:
            b["exe"] = exe
            changed = True
            break
    if changed:
        save_buttons(buttons)


def force_stop_capture():
    """カレントの force_stop_capture.bat で Sunshine キャプチャを停止。"""
    write_state("stopped")
    bat = FORCE_STOP_BAT
    if not bat.is_file():
        print(f"[WARN] force_stop_capture.bat が無い: {bat}", flush=True)
        return
    try:
        # Windows 上で bat を実行（cwd = 同ディレクトリ）
        subprocess.run(
            ["cmd", "/c", str(bat)],
            cwd=str(BASE),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print("force_stop_capture.bat を実行しました", flush=True)
    except Exception as e:
        print(f"[WARN] force_stop_capture.bat 失敗: {e}", flush=True)


def run_exe(exe: str, game_name: str = "") -> int:
    write_state("running", game_name)
    try:
        return subprocess.run([exe], cwd=str(Path(exe).parent)).returncode
    finally:
        write_state("stopped", game_name)


def main() -> int:
    if not SELECTED.exists():
        write_state("stopped", "")
        sys.exit(1)

    game_name = SELECTED.read_text(encoding="utf-8").strip()
    if not game_name:
        write_state("stopped", "")
        sys.exit(1)

    button = find_button(game_name)

    # ---------- このボタンが初回（first_run.json に OK が無い） ----------
    if is_first_for(game_name):
        initial = registered_dir(button)
        exe = pick_exe_dialog(initial)
        if not exe:
            write_state("stopped", game_name)
            data = load_first_run()
            data[game_name] = {"status": "NG", "exe": ""}
            save_first_run(data)
            # キャンセル時もキャプチャは止める
            force_stop_capture()
            sys.exit(1)

        # ダイアログで選んだ exe で起動（終了まで待つ）
        code = run_exe(exe, game_name)

        # 終了後: 登録を残す
        update_button_exe(game_name, exe)
        mark_done(game_name, exe)

        # Sunshine 側キャプチャ終了（初回はこれがないと止まらない）
        force_stop_capture()
        return code

    # ---------- このボタンは初回済み → 通常起動 ----------
    exe = ""
    if button:
        exe = (button.get("exe") or "").strip()
    if not exe or not Path(exe).is_file():
        entry = load_first_run().get(game_name) or {}
        exe = (entry.get("exe") or "").strip()
    if not exe or not Path(exe).is_file():
        write_state("stopped", game_name)
        sys.exit(1)

    return run_exe(exe, game_name)


if __name__ == "__main__":
    sys.exit(main() or 0)
