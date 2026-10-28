# Github更新.pyw
# このファイルがあるフォルダをGitHubへ更新する（ネットワーク対応 + 進捗ウィンドウ）

import os
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, simpledialog


MAX_FILE_SIZE_MB = 50
MAX_FILE_SIZE = MAX_FILE_SIZE_MB * 1024 * 1024


def to_git_path(path):
    return os.path.abspath(path).replace("\\", "/")


def run_git(cmd, cwd, timeout=300):
    try:
        result = subprocess.run(
            ["git"] + cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        out = (result.stdout or "") + (result.stderr or "")
        return result.returncode == 0, out
    except subprocess.TimeoutExpired:
        return False, "タイムアウトしました"
    except Exception as e:
        return False, str(e)


def ensure_safe_directory(folder):
    git_path = to_git_path(folder)
    candidates = [git_path, git_path + "/"]
    if git_path.startswith("//"):
        unc_form = "%(prefix)/" + git_path
        candidates.append(unc_form)
        candidates.append(unc_form + "/")
    home = os.path.expanduser("~")
    for c in candidates:
        run_git(["config", "--global", "--add", "safe.directory", c], cwd=home)


def update_gitignore_for_large_files(folder):
    gitignore_path = os.path.join(folder, ".gitignore")
    existing_lines = []
    if os.path.isfile(gitignore_path):
        try:
            with open(gitignore_path, "r", encoding="utf-8", errors="replace") as f:
                existing_lines = [line.strip() for line in f.readlines() if line.strip()]
        except Exception:
            existing_lines = []
    existing_set = set(existing_lines)
    large_files = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d != ".git"]
        for filename in files:
            file_path = os.path.join(root, filename)
            if os.path.abspath(file_path) == os.path.abspath(gitignore_path):
                continue
            try:
                file_size = os.path.getsize(file_path)
            except (OSError, PermissionError):
                continue
            if file_size >= MAX_FILE_SIZE:
                relative_path = os.path.relpath(file_path, folder).replace("\\", "/")
                large_files.append(("/" + relative_path, file_size))
    new_entries = [p for p, _ in large_files if p not in existing_set]
    if new_entries:
        try:
            with open(gitignore_path, "a", encoding="utf-8") as f:
                try:
                    with open(gitignore_path, "rb") as cf:
                        content = cf.read()
                    if content and not content.endswith(b"\n"):
                        f.write("\n")
                except Exception:
                    pass
                for entry in new_entries:
                    f.write(entry + "\n")
        except Exception as e:
            raise Exception(f".gitignoreの作成に失敗:\n{e}")
    return large_files


def format_large_files(large_files):
    if not large_files:
        return ""
    return "\n".join(
        f"  {path} ({size / (1024 * 1024):.1f} MB)"
        for path, size in large_files
    )


class UpdateApp:
    def __init__(self, folder):
        self.folder = folder
        self.root = tk.Tk()
        self.root.title("Github 更新")
        self.root.geometry("600x420")
        self.root.minsize(480, 320)
        try:
            self.root.attributes("-topmost", True)
        except Exception:
            pass
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)
        # 起動後すぐに処理開始
        self.root.after(200, self._start)

    def _build_ui(self):
        frm = ttk.Frame(self.root, padding=10)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text="対象フォルダ:", font=("", 9, "bold")).pack(anchor=tk.W)
        ttk.Label(frm, text=self.folder, wraplength=560, foreground="#333").pack(anchor=tk.W, pady=(0, 8))

        self.lbl_status = ttk.Label(frm, text="準備中...", font=("", 9))
        self.lbl_status.pack(anchor=tk.W)

        self.progress = ttk.Progressbar(frm, mode="indeterminate")
        self.progress.pack(fill=tk.X, pady=(4, 8))
        self.progress.start(12)

        frm_log = ttk.LabelFrame(frm, text="詳細・進捗", padding=6)
        frm_log.pack(fill=tk.BOTH, expand=True)

        self.txt_log = scrolledtext.ScrolledText(
            frm_log, wrap=tk.WORD, font=("Consolas", 9),
            height=12, state=tk.DISABLED, background="#f8f8f8"
        )
        self.txt_log.pack(fill=tk.BOTH, expand=True)
        self.txt_log.tag_configure("info", foreground="#222")
        self.txt_log.tag_configure("ok", foreground="#0a7a0a")
        self.txt_log.tag_configure("warn", foreground="#a06000")
        self.txt_log.tag_configure("error", foreground="#c00000")
        self.txt_log.tag_configure("step", foreground="#0055aa", font=("Consolas", 9, "bold"))

        self.btn_close = ttk.Button(frm, text="閉じる", command=self.root.destroy, state=tk.DISABLED)
        self.btn_close.pack(pady=(8, 0))

    def log(self, msg, tag="info"):
        def _do():
            self.txt_log.configure(state=tk.NORMAL)
            self.txt_log.insert(tk.END, msg + "\n", tag)
            self.txt_log.see(tk.END)
            self.txt_log.configure(state=tk.DISABLED)
        self.root.after(0, _do)

    def set_status(self, text):
        self.root.after(0, lambda: self.lbl_status.configure(text=text))

    def finish(self):
        def _do():
            self.progress.stop()
            self.btn_close.configure(state=tk.NORMAL)
        self.root.after(0, _do)

    def _start(self):
        thread = threading.Thread(target=self._worker, daemon=True)
        thread.start()

    def _worker(self):
        folder = self.folder
        try:
            self.log(f"対象: {folder}", "info")
            self.log("-" * 40, "info")

            self.set_status("ネットワーク対策を設定中...")
            self.log("[1] safe.directory 設定...", "step")
            ensure_safe_directory(folder)
            self.log("  → 完了", "ok")

            self.set_status("リポジトリ確認中...")
            self.log("[2] Gitリポジトリ確認...", "step")
            ok, out = run_git(["rev-parse", "--is-inside-work-tree"], folder)
            if not ok:
                if "dubious" in out.lower() or "safe.directory" in out.lower():
                    ensure_safe_directory(folder)
                    ok, out = run_git(["rev-parse", "--is-inside-work-tree"], folder)
                if not ok:
                    raise Exception("このフォルダはGitリポジトリではありません。\n" + out)
            self.log("  → OK", "ok")

            self.set_status("大容量ファイル検出中...")
            self.log("[3] 大容量ファイル検索...", "step")
            large_files = update_gitignore_for_large_files(folder)
            if large_files:
                self.log(f"  → {len(large_files)} 件を除外対象に追加", "warn")
                self.log(format_large_files(large_files), "warn")
            else:
                self.log("  → 除外対象なし", "ok")

            self.set_status("git add 実行中...")
            self.log("[4] git add ...", "step")
            ok, out = run_git(["add", "."], folder)
            if not ok:
                if "dubious" in out.lower() or "safe.directory" in out.lower():
                    ensure_safe_directory(folder)
                    ok, out = run_git(["add", "."], folder)
                if not ok:
                    raise Exception(f"git add 失敗:\n{out}")
            self.log("  → 完了", "ok")

            ok, status = run_git(["status", "--porcelain"], folder)
            if not status.strip():
                self.log("変更がありません。", "info")
                self.set_status("変更なし")
                self.finish()
                return

            # コミットメッセージ（メインスレッドでダイアログ）
            self.set_status("コミットメッセージ入力待ち...")
            msg_holder = [None]
            event = threading.Event()

            def ask():
                m = simpledialog.askstring(
                    "Git更新",
                    "コミットメッセージを入力してください:\n（空欄の場合は「Update」）",
                    parent=self.root
                )
                msg_holder[0] = m
                event.set()

            self.root.after(0, ask)
            event.wait()
            msg = msg_holder[0]
            if msg is None:
                self.log("キャンセルされました。", "warn")
                self.set_status("キャンセル")
                self.finish()
                return
            if not msg.strip():
                msg = "Update"

            self.set_status("コミット中...")
            self.log(f"[5] git commit -m \"{msg}\" ...", "step")
            ok, out = run_git(["commit", "-m", msg], folder)
            if not ok:
                raise Exception(f"git commit 失敗:\n{out}")
            self.log("  → 完了", "ok")

            self.set_status("プッシュ中...")
            self.log("[6] git push ...", "step")
            ok, out = run_git(["push"], folder)
            if not ok:
                raise Exception(f"git push 失敗:\n{out}")
            self.log("  → 完了", "ok")

            self.log("=" * 40, "ok")
            self.log("★ 更新が完了しました！", "ok")
            if large_files:
                self.log(f"※ {MAX_FILE_SIZE_MB}MB以上のファイルは除外されています", "warn")
            self.log("=" * 40, "ok")
            self.set_status("完了")
            self.finish()

        except Exception as e:
            self.log("=" * 40, "error")
            self.log("★ エラーが発生しました", "error")
            self.log(str(e), "error")
            self.log("=" * 40, "error")
            self.set_status("エラーで終了")
            self.finish()

    def run(self):
        self.root.mainloop()


def main():
    folder = os.path.dirname(os.path.abspath(__file__))
    folder = os.path.normpath(folder)
    if not os.path.isdir(folder):
        messagebox.showerror("エラー", f"フォルダが存在しません:\n{folder}")
        return
    app = UpdateApp(folder)
    app.run()


if __name__ == "__main__":
    main()
