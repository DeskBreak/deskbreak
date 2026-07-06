# -*- coding: utf-8 -*-
"""
DeskBreak — напоминания о перерывах для тех, кто много сидит за компьютером.

Функции:
- Работает в трее (иконка в системном лотке), не мешает работе.
- Уведомления Windows (toast) о необходимости встать/размяться.
- Отдельные напоминания: движение, отдых для глаз (20-20-20), осанка, вода.
- Настраиваемые интервалы для каждого типа напоминаний.
- Библиотека упражнений с описаниями.
- Автозапуск с Windows (опционально).

Автор: подготовлено для публикации на Softpedia.
"""

import json
import os
import queue
import random
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

import pystray
from PIL import Image

try:
    from winotify import Notification
    NOTIFICATIONS_AVAILABLE = True
except ImportError:
    NOTIFICATIONS_AVAILABLE = False

if sys.platform == "win32":
    import winreg

from exercises import CATEGORIES

APP_NAME = "DeskBreak"
APP_ID = "DeskBreak.PC.Wellness"

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def get_base_dir():
    """Returns directory where the app (or the frozen exe) lives."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(relative_path):
    """Returns path to bundled resources, working both for a normal script
    run and for a PyInstaller --onefile frozen exe (which extracts data
    files to a temporary _MEIPASS folder at runtime)."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative_path)


def get_config_dir():
    appdata = os.getenv("APPDATA") or os.path.expanduser("~")
    path = os.path.join(appdata, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


BASE_DIR = get_base_dir()
CONFIG_PATH = os.path.join(get_config_dir(), "config.json")
ICON_PATH = resource_path(os.path.join("assets", "icon.ico"))

DEFAULT_CONFIG = {
    "intervals_min": {
        "movement": 30,
        "eye": 20,
        "posture": 60,
        "water": 45,
    },
    "enabled": {
        "movement": True,
        "eye": True,
        "posture": True,
        "water": True,
    },
    "autostart": False,
    "paused": False,
}


def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            # merge with defaults to survive future new keys
            merged = json.loads(json.dumps(DEFAULT_CONFIG))
            merged.update({k: v for k, v in cfg.items() if k in DEFAULT_CONFIG})
            for sub in ("intervals_min", "enabled"):
                if sub in cfg:
                    merged[sub].update(cfg[sub])
            return merged
        except Exception:
            pass
    return json.loads(json.dumps(DEFAULT_CONFIG))


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Autostart (Windows registry)
# ---------------------------------------------------------------------------

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def set_autostart(enabled: bool):
    if sys.platform != "win32":
        return
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE)
        if enabled:
            exe_path = sys.executable if getattr(sys, "frozen", False) else \
                f'"{sys.executable}" "{os.path.abspath(__file__)}"'
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, exe_path)
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception as e:
        print("Autostart error:", e)


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def show_notification(title, message):
    """Show a Windows toast notification, with console fallback."""
    if sys.platform == "win32" and NOTIFICATIONS_AVAILABLE:
        try:
            icon = ICON_PATH if os.path.exists(ICON_PATH) else ""
            toast = Notification(
                app_id=APP_NAME,
                title=title,
                msg=message,
                icon=icon,
                duration="short",
            )
            toast.show()
            return
        except Exception as e:
            print("Notification error:", e)
    # Fallback (non-Windows / dev environment)
    print(f"[{title}] {message}")


CATEGORY_TITLES = {
    "movement": "Пора размяться!",
    "eye": "Дай отдых глазам",
    "posture": "Проверь осанку",
    "water": "Не забывай пить воду",
}


def build_reminder_text(category):
    items = CATEGORIES[category]["items"]
    item = random.choice(items)
    return CATEGORY_TITLES[category], f"{item['emoji']} {item['name']}: {item['desc']}"


# ---------------------------------------------------------------------------
# Scheduler (background thread)
# ---------------------------------------------------------------------------

class Scheduler(threading.Thread):
    def __init__(self, cfg_getter):
        super().__init__(daemon=True)
        self.cfg_getter = cfg_getter
        self._stop_event = threading.Event()
        self._last_fired = {k: time.time() for k in CATEGORIES}

    def stop(self):
        self._stop_event.set()

    def run(self):
        while not self._stop_event.is_set():
            cfg = self.cfg_getter()
            if not cfg.get("paused", False):
                now = time.time()
                for category in CATEGORIES:
                    if not cfg["enabled"].get(category, True):
                        continue
                    interval_sec = max(1, int(cfg["intervals_min"].get(category, 30))) * 60
                    if now - self._last_fired[category] >= interval_sec:
                        self._last_fired[category] = now
                        title, msg = build_reminder_text(category)
                        show_notification(title, msg)
            self._stop_event.wait(1)

    def reset_timer(self, category):
        self._last_fired[category] = time.time()

    def reset_all(self):
        for k in self._last_fired:
            self._last_fired[k] = time.time()


# ---------------------------------------------------------------------------
# Tkinter GUI (settings + exercise library)
# ---------------------------------------------------------------------------

class App:
    def __init__(self):
        self.cfg = load_config()
        self.cmd_queue = queue.Queue()

        self.root = tk.Tk()
        self.root.withdraw()  # hidden main window, used only as Tk event loop host
        self.root.title(APP_NAME)

        self.settings_win = None
        self.library_win = None

        self.scheduler = Scheduler(lambda: self.cfg)
        self.scheduler.start()

        self.tray_icon = self._build_tray_icon()
        self.tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
        self.tray_thread.start()

        self.root.after(150, self._poll_queue)

    # -- Tray -------------------------------------------------------------

    def _build_tray_icon(self):
        image = Image.open(ICON_PATH) if os.path.exists(ICON_PATH) else Image.new("RGB", (64, 64), "green")
        menu = pystray.Menu(
            pystray.MenuItem("Открыть настройки", self._on_open_settings),
            pystray.MenuItem("Библиотека упражнений", self._on_open_library),
            pystray.MenuItem(
                "На паузе",
                self._on_toggle_pause,
                checked=lambda item: self.cfg.get("paused", False),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", self._on_exit),
        )
        return pystray.Icon(APP_ID, image, APP_NAME, menu)

    def _on_open_settings(self, icon=None, item=None):
        self.cmd_queue.put(("open_settings", None))

    def _on_open_library(self, icon=None, item=None):
        self.cmd_queue.put(("open_library", None))

    def _on_toggle_pause(self, icon=None, item=None):
        self.cfg["paused"] = not self.cfg.get("paused", False)
        save_config(self.cfg)
        self.tray_icon.update_menu()

    def _on_exit(self, icon=None, item=None):
        self.cmd_queue.put(("exit", None))

    # -- Main-thread queue polling -----------------------------------------

    def _poll_queue(self):
        try:
            while True:
                cmd, _ = self.cmd_queue.get_nowait()
                if cmd == "open_settings":
                    self._open_settings_window()
                elif cmd == "open_library":
                    self._open_library_window()
                elif cmd == "exit":
                    self._do_exit()
                    return
        except queue.Empty:
            pass
        self.root.after(150, self._poll_queue)

    # -- Settings window -----------------------------------------------------

    def _open_settings_window(self):
        if self.settings_win is not None and self.settings_win.winfo_exists():
            self.settings_win.lift()
            self.settings_win.focus_force()
            return

        win = tk.Toplevel(self.root)
        self.settings_win = win
        win.title(f"{APP_NAME} — Настройки")
        win.geometry("420x420")
        win.resizable(False, False)
        try:
            win.iconbitmap(ICON_PATH)
        except Exception:
            pass

        pad = {"padx": 12, "pady": 6}

        tk.Label(win, text="Напоминания", font=("Segoe UI", 12, "bold")).pack(anchor="w", **pad)

        vars_enabled = {}
        vars_interval = {}

        for cat_key, cat in CATEGORIES.items():
            frame = tk.Frame(win)
            frame.pack(fill="x", **pad)

            v_enabled = tk.BooleanVar(value=self.cfg["enabled"].get(cat_key, True))
            vars_enabled[cat_key] = v_enabled
            chk = tk.Checkbutton(
                frame,
                text=f"{cat['icon']} {cat['title']}",
                variable=v_enabled,
                width=18,
                anchor="w",
            )
            chk.pack(side="left")

            tk.Label(frame, text="каждые").pack(side="left")

            v_interval = tk.IntVar(value=self.cfg["intervals_min"].get(cat_key, 30))
            vars_interval[cat_key] = v_interval
            spin = tk.Spinbox(frame, from_=1, to=240, width=5, textvariable=v_interval)
            spin.pack(side="left", padx=4)

            tk.Label(frame, text="мин.").pack(side="left")

        ttk.Separator(win, orient="horizontal").pack(fill="x", pady=8, padx=12)

        v_autostart = tk.BooleanVar(value=self.cfg.get("autostart", False))
        tk.Checkbutton(
            win,
            text="Запускать вместе с Windows",
            variable=v_autostart,
        ).pack(anchor="w", **pad)

        def on_save():
            for cat_key in CATEGORIES:
                self.cfg["enabled"][cat_key] = vars_enabled[cat_key].get()
                try:
                    interval = int(vars_interval[cat_key].get())
                except (tk.TclError, ValueError):
                    interval = self.cfg["intervals_min"].get(cat_key, 30)
                self.cfg["intervals_min"][cat_key] = max(1, interval)
            self.cfg["autostart"] = v_autostart.get()
            save_config(self.cfg)
            set_autostart(self.cfg["autostart"])
            self.scheduler.reset_all()
            messagebox.showinfo(APP_NAME, "Настройки сохранены.", parent=win)

        btn_frame = tk.Frame(win)
        btn_frame.pack(fill="x", pady=16, padx=12)
        tk.Button(btn_frame, text="Сохранить", command=on_save, width=14).pack(side="left")
        tk.Button(btn_frame, text="Тест уведомления", command=lambda: show_notification(
            *build_reminder_text("movement")
        )).pack(side="left", padx=8)
        tk.Button(btn_frame, text="Закрыть", command=win.destroy, width=10).pack(side="right")

    # -- Exercise library window ---------------------------------------------

    def _open_library_window(self):
        if self.library_win is not None and self.library_win.winfo_exists():
            self.library_win.lift()
            self.library_win.focus_force()
            return

        win = tk.Toplevel(self.root)
        self.library_win = win
        win.title(f"{APP_NAME} — Библиотека упражнений")
        win.geometry("480x560")
        try:
            win.iconbitmap(ICON_PATH)
        except Exception:
            pass

        canvas = tk.Canvas(win, borderwidth=0)
        frame = tk.Frame(canvas)
        vsb = tk.Scrollbar(win, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.create_window((0, 0), window=frame, anchor="nw")

        for cat_key, cat in CATEGORIES.items():
            tk.Label(
                frame, text=f"{cat['icon']}  {cat['title']}",
                font=("Segoe UI", 12, "bold")
            ).pack(anchor="w", padx=10, pady=(14, 4))
            for item in cat["items"]:
                block = tk.Frame(frame)
                block.pack(fill="x", padx=16, pady=4, anchor="w")
                tk.Label(
                    block, text=f"{item['emoji']} {item['name']}",
                    font=("Segoe UI", 10, "bold")
                ).pack(anchor="w")
                tk.Label(
                    block, text=item["desc"], wraplength=420, justify="left",
                    font=("Segoe UI", 9)
                ).pack(anchor="w")

        frame.update_idletasks()
        canvas.configure(scrollregion=canvas.bbox("all"))

    # -- Exit -----------------------------------------------------------------

    def _do_exit(self):
        self.scheduler.stop()
        try:
            self.tray_icon.stop()
        except Exception:
            pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    if sys.platform != "win32":
        print("Внимание: полноценно приложение работает только на Windows "
              "(нужны Windows toast-уведомления и системный трей).")
    app = App()
    app.run()


if __name__ == "__main__":
    main()
