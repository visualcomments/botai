#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Save a GitHub token to .env, then verify it — a small desktop dialog.

Why this exists
---------------
A token pasted into a chat, an issue, a shell history or a git config is a
leaked token: it is written down somewhere that outlives the intent. This
dialog keeps it out of all of those. The token goes to `.env`, which the
repository's `.gitignore` already excludes (`.gitignore` line 24), and it is
never printed, logged, or echoed back.

The window offers three things and refuses to do more:

  1. Save the token to `.env` as `GITHUB_TOKEN=...`, preserving every other
     line already in the file and replacing only that key.
  2. Check the token against the GitHub API — who it belongs to, and which
     scopes it carries — without pushing anything.
  3. Copy the ready-to-run `git push` command (no token inside it) to the
     clipboard, so the push happens in your own terminal.

It deliberately does not push, commit, or read the token back out: those are
the actions that need your hands on the keyboard, and a GUI is the wrong place
for an irreversible networked action.

Usage:
    python scripts/github_token_gui.py

Requires only the standard library (tkinter ships with CPython on Windows and
macOS; on Debian/Ubuntu install python3-tk).
"""

from __future__ import annotations

import os
import re
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

# Russian UI text needs a UTF-8 aware stream on a legacy Windows console.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"
ENV_KEY = "GITHUB_TOKEN"

# A classic PAT is `ghp_` + 36 chars; a fine-grained PAT is `github_pat_` + 82.
# Anything else is accepted too (some hosts mint other prefixes) but flagged, so
# a truncated paste is noticed before it is saved.
CLASSIC_PAT = re.compile(r"^ghp_[A-Za-z0-9]{36}$")
FINE_GRAINED_PAT = re.compile(r"^github_pat_[A-Za-z0-9_]{60,}$")


def looks_like_token(value: str) -> tuple[bool, str]:
    """(plausible, human note). Shape only — never a validity claim."""
    value = value.strip()
    if not value:
        return False, "токен пуст"
    if CLASSIC_PAT.match(value):
        return True, "похож на классический PAT (ghp_…)"
    if FINE_GRAINED_PAT.match(value):
        return True, "похож на fine-grained PAT (github_pat_…)"
    if value.startswith(("ghp_", "github_pat_", "ghs_", "gho_")):
        return False, "префикс верный, но длина не та — возможно, токен обрезан"
    return False, "не похож на токен GitHub (ожидается ghp_… или github_pat_…)"


def mask(value: str, keep: int = 4) -> str:
    """A preview that identifies the token without disclosing it."""
    value = value.strip()
    if len(value) <= keep * 2:
        return "*" * len(value)
    return "%s…%s (%d символов)" % (value[:keep], value[-keep:], len(value))


def read_env_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    # utf-8-sig: a .env written by Notepad or PowerShell can carry a BOM.
    return path.read_text(encoding="utf-8-sig").splitlines()


def write_env_token(path: Path, key: str, token: str) -> tuple[bool, str]:
    """Set `key=token` in the .env, preserving every other line.

    Returns (changed, note). An existing value for the key is replaced in place;
    otherwise the key is appended. Everything else — other keys, comments,
    blank lines — is left exactly as it was.
    """
    lines = read_env_lines(path)
    pattern = re.compile(r"^\s*(?:export\s+)?%s\s*=" % re.escape(key))
    kept: list[str] = []
    replaced = False
    for line in lines:
        if pattern.match(line):
            if not replaced:
                kept.append("%s=%s" % (key, token))
                replaced = True
            # a duplicate key would make the file ambiguous: drop the extra
            continue
        kept.append(line)

    if not replaced:
        if kept and kept[-1].strip() != "":
            kept.append("")
        kept.append("# GitHub token for pushing this fork. Ignored by .gitignore.")
        kept.append("%s=%s" % (key, token))

    text = "\n".join(kept) + "\n"
    previous = path.read_text(encoding="utf-8-sig") if path.exists() else None
    if previous == text:
        return False, "токен уже сохранён без изменений"
    path.write_text(text, encoding="utf-8", newline="\n")
    try:
        os.chmod(path, 0o600)  # best effort; a no-op on Windows
    except OSError:
        pass
    return True, ("обновлён ключ %s" % key) if replaced else ("добавлен ключ %s" % key)


def api_check(token: str, timeout: float = 15.0) -> tuple[bool, str]:
    """Ask GitHub who this token is. No repository is touched."""
    req = urllib.request.Request(
        "https://api.github.com/user",
        headers={
            "Authorization": "Bearer %s" % token,
            "Accept": "application/vnd.github+json",
            "User-Agent": "botai-token-gui",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            import json

            payload = json.loads(response.read().decode("utf-8", "replace"))
            scopes = response.headers.get("X-OAuth-Scopes") or "(не сообщается)"
            login = payload.get("login") or "?"
            return True, "токен действителен. Аккаунт: %s\nПрава (scopes): %s" % (
                login,
                scopes,
            )
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return False, "GitHub ответил 401: токен недействителен или отозван"
        if exc.code == 403:
            return False, (
                "GitHub ответил 403: токен принят, но прав недостаточно "
                "(для push нужен scope repo)"
            )
        return False, "GitHub ответил HTTP %s" % exc.code
    except urllib.error.URLError as exc:
        return False, "нет связи с api.github.com: %s" % (exc.reason,)
    except Exception as exc:  # noqa: BLE001 - the dialog must never crash
        return False, "ошибка проверки: %s: %s" % (type(exc).__name__, exc)


def build_ui():  # pragma: no cover - requires a display
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title("GitHub token → .env")
    root.minsize(640, 430)

    frame = ttk.Frame(root, padding=14)
    frame.pack(fill="both", expand=True)
    frame.columnconfigure(0, weight=1)

    ttk.Label(
        frame,
        text="Вставьте GitHub PAT. Токен сохранится в .env (файл в .gitignore).",
        wraplength=600,
        justify="left",
    ).grid(row=0, column=0, sticky="w")

    ttk.Label(frame, text=str(ENV_PATH), foreground="#555").grid(
        row=1, column=0, sticky="w", pady=(2, 10)
    )

    # --- token entry, masked by default ------------------------------------
    entry_row = ttk.Frame(frame)
    entry_row.grid(row=2, column=0, sticky="ew")
    entry_row.columnconfigure(0, weight=1)

    token_var = tk.StringVar()
    entry = ttk.Entry(entry_row, textvariable=token_var, show="•")
    entry.grid(row=0, column=0, sticky="ew")
    entry.focus_set()

    revealed = tk.BooleanVar(value=False)

    def toggle_reveal():
        entry.configure(show="" if revealed.get() else "•")

    ttk.Checkbutton(
        entry_row, text="показать", variable=revealed, command=toggle_reveal
    ).grid(row=0, column=1, padx=(8, 0))

    # --- status line --------------------------------------------------------
    status_var = tk.StringVar(value="Вставьте токен и нажмите «Проверить».")
    status = ttk.Label(
        frame,
        textvariable=status_var,
        wraplength=600,
        justify="left",
        foreground="#333",
    )
    status.grid(row=3, column=0, sticky="w", pady=(10, 4))

    preview_var = tk.StringVar(value="")
    ttk.Label(frame, textvariable=preview_var, foreground="#777").grid(
        row=4, column=0, sticky="w"
    )

    progress = ttk.Progressbar(frame, mode="indeterminate")
    progress.grid(row=5, column=0, sticky="ew", pady=(8, 0))

    # --- actions ------------------------------------------------------------
    buttons = ttk.Frame(frame)
    buttons.grid(row=6, column=0, sticky="ew", pady=(12, 0))
    for column in range(3):
        buttons.columnconfigure(column, weight=1)

    def current_token() -> str:
        return token_var.get().strip()

    def on_check():
        token = current_token()
        plausible, note = looks_like_token(token)
        if not plausible:
            status_var.set("Не сохранено: " + note)
            return
        status_var.set("Проверка токена…")
        progress.start(12)

        def worker():
            ok, message = api_check(token)

            def finish():
                progress.stop()
                status_var.set(("✔ " if ok else "✘ ") + message)

            root.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def on_save():
        token = current_token()
        plausible, note = looks_like_token(token)
        if not plausible:
            status_var.set("Не сохранено: " + note)
            return
        try:
            changed, message = write_env_token(ENV_PATH, ENV_KEY, token)
        except OSError as exc:
            messagebox.showerror("Не удалось записать .env", str(exc))
            return
        preview_var.set("Сохранено: " + mask(token))
        status_var.set(
            ("✔ " if changed else "• ")
            + message
            + "\nДальше: «Проверить», затем «Скопировать push» и выполнить "
            "команду в терминале репозитория."
        )
        token_var.set("")  # the value now lives in .env, not on screen
        entry.configure(show="•")
        revealed.set(False)

    def on_copy_push():
        command = 'cd "%s"; git push origin main' % REPO_ROOT
        root.clipboard_clear()
        root.clipboard_append(command)
        status_var.set(
            "Скопировано в буфер обмена:\n"
            + command
            + "\n\nВ .env уже лежит токен; git подхватит его при push "
            "(credential.helper=store)."
        )

    ttk.Button(buttons, text="Проверить", command=on_check).grid(
        row=0, column=0, sticky="ew", padx=(0, 6)
    )
    ttk.Button(buttons, text="Сохранить в .env", command=on_save).grid(
        row=0, column=1, sticky="ew", padx=6
    )
    ttk.Button(buttons, text="Скопировать push", command=on_copy_push).grid(
        row=0, column=2, sticky="ew", padx=(6, 0)
    )

    # --- safety note --------------------------------------------------------
    ttk.Separator(frame).grid(row=7, column=0, sticky="ew", pady=(14, 8))
    ttk.Label(
        frame,
        text=(
            "Токен не печатается и не пишется нигде, кроме .env. "
            "Отозвать старый токен: github.com/settings/tokens. "
            "Окно не пушит само — команда push выполняется вами."
        ),
        wraplength=600,
        justify="left",
        foreground="#555",
    ).grid(row=8, column=0, sticky="w")

    root.bind("<Return>", lambda _event: on_check())
    root.bind("<Escape>", lambda _event: root.destroy())
    return root


def main() -> int:
    if not ENV_PATH.parent.exists():  # pragma: no cover
        print("репозиторий не найден: %s" % REPO_ROOT)
        return 2
    try:
        root = build_ui()
    except ImportError:
        print(
            "tkinter недоступен. Установите: apt install python3-tk "
            "(Debian/Ubuntu) — или задайте GITHUB_TOKEN в .env вручную."
        )
        return 2
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
