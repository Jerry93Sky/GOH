#!/usr/bin/env python3
"""Russian-language, filesystem-only GOH Coop Recovery Manager."""
from __future__ import annotations

import argparse
import ctypes
import re
import json
import os
import sys
import tempfile
import traceback
import zipfile
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import recovery_coordinator as coordinator
import recovery_harness as harness

APP_VERSION = "0.1.0-experimental"
APP_ID = "400750"


def steam_libraries() -> list[Path]:
    roots: list[Path] = []
    try:
        import winreg
        for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
            try:
                with winreg.OpenKey(hive, key) as handle:
                    value, _ = winreg.QueryValueEx(handle, "SteamPath")
                    roots.append(Path(value.replace("/", "\\")) / "steamapps" / "common")
            except OSError:
                pass
    except ImportError:
        pass
    for candidate in (Path("C:/Program Files (x86)/Steam"), Path("C:/Program Files/Steam")):
        if candidate.exists():
            roots.append(candidate / "steamapps" / "common")
    libraries = []
    for root in roots:
        if root not in libraries:
            libraries.append(root)
        vdf = root.parent / "libraryfolders.vdf"
        try:
            content = vdf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for raw in re.findall(r'"path"\s+"([^"]+)"', content):
            candidate = Path(raw.replace("\\\\", "\\").replace("\\/", "/")) / "steamapps" / "common"
            if candidate not in libraries:
                libraries.append(candidate)
    return libraries


def find_game_installation() -> Path | None:
    for common in steam_libraries():
        manifest = common.parent / "appmanifest_400750.acf"
        game = common / "Call to Arms - Gates of Hell"
        if manifest.is_file() and game.is_dir() and (game / "binaries" / "x64" / "call_to_arms.exe").is_file():
            return game
    return None


def game_file_version(path: Path) -> str | None:
    if os.name != "nt":
        return None
    try:
        version = ctypes.WinDLL("version", use_last_error=True)
        size = version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return None
        data = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(str(path), 0, size, data):
            return None
        pointer = ctypes.c_void_p()
        length = ctypes.c_uint()
        if not version.VerQueryValueW(data, "\\", ctypes.byref(pointer), ctypes.byref(length)) or length.value < 4:
            return None
        words = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32))
        ms, ls = words[2], words[3]
        return f"{ms >> 16}.{ms & 0xffff}.{ls >> 16}.{ls & 0xffff}"
    except (OSError, AttributeError, ValueError):
        return None


def default_store() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    return base / "GOH Coop Recovery" / "checkpoints"


def smoke_check() -> int:
    assert harness.MARKER_TTL_SECONDS == 900
    assert harness.CLIENT_MARKER.endswith("client.txt")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        marker = root / harness.CLIENT_MARKER
        marker.write_text("campaign/test.mi\n@/profiles/test/save/coop_ckpt/\nplayers 4 2\n", encoding="utf-8")
        parsed, issues = harness.parse_marker(marker)
        assert not issues and parsed and parsed.players_base == 4 and parsed.players_count == 2
    print(f"GOHRecoveryManager {APP_VERSION}: smoke check OK")
    return 0


class Manager(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("GOH Coop Recovery Manager — экспериментальная версия 0.1")
        self.geometry("1040x720")
        self.minsize(850, 600)
        self.game_dir = tk.StringVar()
        self.store_dir = tk.StringVar(value=str(default_store()))
        self.checkpoint_dir = tk.StringVar()
        self.status = tk.StringVar(value="Диагностический режим. Игровая совместимость не проверена.")
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        header = ttk.Frame(self, padding=(12, 10))
        header.pack(fill="x")
        ttk.Label(header, text="GOH Coop Recovery", font=("Segoe UI", 18, "bold")).pack(side="left")
        ttk.Label(header, text="V0.1 • READY FOR GAME TEST • GAME VERIFIED: НЕТ", foreground="#9a5700").pack(side="right")
        bar = ttk.Frame(self, padding=(12, 0, 12, 8))
        bar.pack(fill="x")
        ttk.Label(bar, text="Папка игры:").grid(row=0, column=0, sticky="w")
        ttk.Entry(bar, textvariable=self.game_dir).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(bar, text="Выбрать…", command=self.choose_game).grid(row=0, column=2)
        ttk.Label(bar, text="Хранилище checkpoint:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(bar, textvariable=self.store_dir).grid(row=1, column=1, sticky="ew", padx=6, pady=(6, 0))
        ttk.Button(bar, text="Обзор…", command=self.choose_store).grid(row=1, column=2, pady=(6, 0))
        bar.columnconfigure(1, weight=1)
        tabs = ttk.Notebook(self)
        tabs.pack(fill="both", expand=True, padx=12, pady=6)
        self.connection = ttk.Frame(tabs, padding=12)
        self.points = ttk.Frame(tabs, padding=12)
        self.recovery = ttk.Frame(tabs, padding=12)
        self.diagnostics = ttk.Frame(tabs, padding=12)
        tabs.add(self.connection, text="Игра и моды")
        tabs.add(self.points, text="Контрольные точки")
        tabs.add(self.recovery, text="Восстановление и пакеты")
        tabs.add(self.diagnostics, text="Диагностика")
        self._build_connection()
        self._build_points()
        self._build_recovery()
        self._build_diagnostics()
        ttk.Label(self, textvariable=self.status, relief="sunken", anchor="w", padding=5).pack(fill="x", side="bottom")

    def _build_connection(self) -> None:
        ttk.Label(self.connection, text="Поиск установки", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Button(self.connection, text="Автоматически найти игру", command=self.find_game).pack(anchor="w", pady=8)
        self.game_info = tk.Text(self.connection, height=8, wrap="word")
        self.game_info.pack(fill="x")
        self._text(self.game_info, "Автопоиск Steam-библиотек и ручной выбор папки доступны. Версия игры читается только если обнаружен исполняемый файл; отсутствие игры не блокирует офлайн диагностику.\n")
        ttk.Label(self.connection, text="Проверка Coop Checkpoint и модов", font=("Segoe UI", 13, "bold")).pack(anchor="w", pady=(18, 6))
        ttk.Button(self.connection, text="Проверить установленные файлы", command=self.inspect_mods).pack(anchor="w")
        self.mods_info = tk.Text(self.connection, height=12, wrap="word")
        self.mods_info.pack(fill="both", expand=True, pady=8)

    def _build_points(self) -> None:
        controls = ttk.Frame(self.points)
        controls.pack(fill="x")
        ttk.Button(controls, text="Обновить список", command=self.refresh).pack(side="left")
        ttk.Button(controls, text="Проверить целостность", command=self.verify_selected).pack(side="left", padx=8)
        ttk.Button(controls, text="Резервировать выбранную точку", command=self.backup_selected).pack(side="left")
        self.latest_checkpoint_text = tk.StringVar(value="Последняя полная точка: не найдена")
        self.latest_verified_text = tk.StringVar(value="Последняя подтверждённая игрой точка: нет")
        ttk.Label(self.points, textvariable=self.latest_checkpoint_text, foreground="#245b31").pack(anchor="w", pady=(8, 0))
        ttk.Label(self.points, textvariable=self.latest_verified_text, foreground="#245b31").pack(anchor="w")
        cols = ("id", "time", "mission", "build", "status", "game", "files")
        self.tree = ttk.Treeview(self.points, columns=cols, show="headings", height=14)
        for col, label, width in (("id", "ID", 145), ("time", "Время", 135), ("mission", "Миссия", 170),
                                  ("build", "Build", 90), ("status", "Файлы", 105), ("game", "Игровой тест", 125), ("files", "Архивы", 65)):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, stretch=col == "mission")
        self.tree.pack(fill="both", expand=True, pady=10)
        self.tree.bind("<<TreeviewSelect>>", self.select_checkpoint)
        ttk.Label(self.points, text="Полная точка означает только проверенную файловую структуру. Игровая загрузка считается подтверждённой только с приложенным протоколом реального теста.", wraplength=900).pack(anchor="w")

    def _build_recovery(self) -> None:
        ttk.Label(self.recovery, text="Экспорт / импорт пакета участника", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        row = ttk.Frame(self.recovery)
        row.pack(fill="x", pady=6)
        ttk.Label(row, text="ID checkpoint:").pack(side="left")
        ttk.Entry(row, textvariable=self.checkpoint_dir).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row, text="Выбрать…", command=self.choose_checkpoint).pack(side="left")
        ttk.Button(self.recovery, text="Экспорт пакета участника…", command=self.export_package).pack(anchor="w", pady=4)
        ttk.Button(self.recovery, text="Импорт и проверка пакета…", command=self.import_package).pack(anchor="w", pady=4)
        ttk.Separator(self.recovery).pack(fill="x", pady=12)
        ttk.Label(self.recovery, text="Режим A — проверить существующие маркеры", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        marker_row = ttk.Frame(self.recovery)
        marker_row.pack(anchor="w", pady=6)
        ttk.Button(marker_row, text="Проверить локальные TEMP-маркеры", command=self.inspect_markers).pack(side="left")
        ttk.Button(marker_row, text="Сохранить копию существующих marker", command=self.backup_markers).pack(side="left", padx=8)
        ttk.Label(self.recovery, text="Режим B — подготовить маркер из явных метаданных", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(8, 0))
        ttk.Label(self.recovery, text="Требуется JSON с точными internal_mission_path и archive_path (виртуальный base path из runtime лога, обычно с завершающим /; не Windows путь к .sav), player_id, player_mapping {base,count}, game_build, mission_id и mod_fingerprint. Для server role укажите is_host=true. Это экспериментальная подготовка файлов; она не подтверждает загрузку движком.", wraplength=930).pack(anchor="w", pady=5)
        ttk.Button(self.recovery, text="Подготовить локальный marker…", command=self.prepare_marker).pack(anchor="w", pady=5)
        ttk.Label(self.recovery, text="Подготовка запускает 15-минутный TTL. Кнопка требует явного подтверждения готовности всех участников и не перезаписывает существующий marker. Без точного player mapping подготовка запрещена.", foreground="#8a4200", wraplength=900).pack(anchor="w")

    def _build_diagnostics(self) -> None:
        row = ttk.Frame(self.diagnostics)
        row.pack(fill="x")
        ttk.Button(row, text="Собрать очищенный отчёт…", command=self.export_diagnostics).pack(side="left")
        ttk.Button(row, text="Проверить выбранный recovery пакет…", command=self.validate_package_dialog).pack(side="left", padx=8)
        ttk.Button(row, text="Прочитать Coop Checkpoint log", command=self.show_coop_logs).pack(side="left")
        self.diag_text = tk.Text(self.diagnostics, wrap="word")
        self.diag_text.pack(fill="both", expand=True, pady=8)
        self._text(self.diag_text, "Отчёт включает версии, manifest metadata, статусы, размеры и SHA-256. Игровые архивы, пути профилей и содержимое TEMP-маркеров не включаются. Перед отправкой оператор может проверить экспортированный JSON.\n")

    @staticmethod
    def _text(widget: tk.Text, content: str) -> None:
        widget.insert("end", content)
        widget.configure(state="disabled")

    def choose_game(self) -> None:
        path = filedialog.askdirectory(title="Выберите папку Gates of Hell")
        if path:
            self.game_dir.set(path)
            self.inspect_mods()

    def choose_store(self) -> None:
        path = filedialog.askdirectory(title="Выберите хранилище checkpoint", initialdir=self.store_dir.get())
        if path:
            self.store_dir.set(path)
            self.refresh()

    def find_game(self) -> None:
        candidate = find_game_installation()
        if candidate:
            self.game_dir.set(str(candidate))
            self.inspect_mods()
            self.status.set(f"Найдена папка: {candidate}")
        else:
            messagebox.showinfo("Игра не найдена", "Игра не обнаружена в стандартных Steam-папках. Укажите папку вручную; можно продолжить без игры.")

    def inspect_mods(self) -> None:
        p = Path(self.game_dir.get())
        items = []
        if p.is_dir():
            for name in ("binaries/x64/call_to_arms.exe", "binaries/x64/call_to_arms_server.exe", "version.dll", "binaries/x64/goh_mods/coop_checkpoint.dll", "mods/coop_checkpoint"):
                f = p / name
                items.append(f"{'НАЙДЕН' if f.exists() else 'нет'}: {f}")
            exe = p / "binaries" / "x64" / "call_to_arms.exe"
            version = game_file_version(exe) if exe.is_file() else None
            self.game_info.configure(state="normal")
            self.game_info.delete("1.0", "end")
            self.game_info.insert("end", f"Путь: {p}\nВерсия файла: {version or 'UNKNOWN'}\n")
            if version:
                self.game_info.insert("end", "Сверьте с build в игре и релизной заметкой; version resource может не совпасть с отображаемым build.\n")
            self.game_info.configure(state="disabled")
            mods = p / "mods"
            if mods.is_dir():
                discovered = sorted(x.name for x in mods.iterdir() if x.is_dir())
                items.append("Каталоги модов (активация не проверена): " + (", ".join(discovered) if discovered else "нет"))
        else:
            items.append("Папка игры не задана или отсутствует.")
        self.mods_info.configure(state="normal")
        self.mods_info.delete("1.0", "end")
        self.mods_info.insert("end", "\n".join(items) + "\n\nКаталог мода не подтверждает, что он включён в профиле или одинаков у всех участников.\nCoop Checkpoint: найденные файлы не подтверждают правильную загрузку или совместимость.\n")
        self.mods_info.configure(state="disabled")

    def refresh(self) -> None:
        if not hasattr(self, "tree"):
            return
        self.tree.delete(*self.tree.get_children())
        store = Path(self.store_dir.get())
        if not store.is_dir():
            return
        candidates = []
        latest_verified = []
        for d in sorted(store.iterdir(), key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True):
            if not d.is_dir() or not (d / "manifest.json").exists():
                continue
            try:
                m, issues = harness.read_checkpoint(d)
                result_records = coordinator.read_test_results(store, m.get("checkpoint_id", d.name))
                game_records = [r for r in result_records if r.get("status") in {"RESTORE_VERIFIED", "RESTORE_FAILED"}
                                and r.get("protocol", {}).get("experiment_type") == "real_game"]
                latest_result = max(game_records, key=lambda r: r.get("recorded_at_utc", ""), default=None)
                game_result = (f"{latest_result.get('test_id')} " + ("PASS" if latest_result.get("status") == "RESTORE_VERIFIED" else "FAIL")) if latest_result else "не проверено"
                files = str(len(m.get("files", [])))
                stamp = m.get("checkpoint_time_utc", "?")[:16].replace("T", " ")
                iid = str(d.resolve())
                self.tree.insert("", "end", iid=iid, values=(m.get("checkpoint_id", d.name), stamp,
                    m.get("mission_id", "?"), m.get("game_build", "?"),
                    "целая" if m.get("status") == "FILE_VERIFIED" and not issues else "неполная/ошибка",
                    game_result, files))
                if m.get("status") == "FILE_VERIFIED" and not issues:
                    candidates.append((m.get("checkpoint_time_utc", ""), m.get("checkpoint_id", d.name)))
                if latest_result and latest_result.get("status") == "RESTORE_VERIFIED":
                    latest_verified.append((latest_result.get("recorded_at_utc", ""), m.get("checkpoint_id", d.name), latest_result.get("test_id", "?")))
            except Exception as exc:
                self.tree.insert("", "end", iid=str(d.resolve()), values=(d.name, "?", "?", "?", "ERROR", "—", str(exc)[:40]))
        newest = max(candidates, default=None)
        verified = max(latest_verified, default=None)
        self.latest_checkpoint_text.set(f"Последняя полная файловая точка: {newest[1] if newest else 'не найдена'}")
        self.latest_verified_text.set(f"Последняя точка, подтверждённая реальным игровым тестом: {verified[1] + ' / ' + verified[2] if verified else 'нет'}")

    def selected_dir(self) -> Path | None:
        selected = self.tree.selection()
        return Path(selected[0]) if selected else None

    def select_checkpoint(self, _event=None) -> None:
        p = self.selected_dir()
        if p:
            self.checkpoint_dir.set(str(p))

    def choose_checkpoint(self) -> None:
        p = filedialog.askdirectory(title="Выберите каталог checkpoint", initialdir=self.store_dir.get())
        if p:
            self.checkpoint_dir.set(p)

    def verify_selected(self) -> None:
        p = self.selected_dir()
        if not p:
            messagebox.showwarning("Checkpoint", "Сначала выберите checkpoint.")
            return
        try:
            manifest, issues = harness.read_checkpoint(p)
            messagebox.showinfo("Проверка", "Файлы целы." if not issues else "\n".join(issues))
            self.status.set(f"{manifest.get('checkpoint_id')}: файловая проверка {'OK' if not issues else 'FAIL'}")
        except Exception as exc:
            messagebox.showerror("Ошибка", str(exc))

    def backup_selected(self) -> None:
        p = self.selected_dir()
        if not p:
            messagebox.showwarning("Резервная копия", "Выберите checkpoint.")
            return
        target = filedialog.askdirectory(title="Папка для резервной копии")
        if not target:
            return
        try:
            dst = Path(target) / f"{p.name}-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            import shutil
            shutil.copytree(p, dst)
            messagebox.showinfo("Готово", f"Резервная копия создана:\n{dst}")
        except Exception as exc:
            messagebox.showerror("Ошибка резервирования", str(exc))

    def export_package(self) -> None:
        checkpoint = Path(self.checkpoint_dir.get())
        if not checkpoint.is_dir():
            messagebox.showwarning("Пакет", "Укажите каталог checkpoint.")
            return
        try:
            manifest, issues = harness.read_checkpoint(checkpoint)
            if issues:
                raise harness.HarnessError("Checkpoint повреждён: " + "; ".join(issues))
            player = self.ask_value("Экспорт пакета", "ID участника (точно как в checkpoint):")
            if not player:
                return
            host = messagebox.askyesno("Роль хоста", "Экспортировать также локальный серверный архив? Выберите Да только для компьютера хоста.")
            dest = filedialog.asksaveasfilename(defaultextension=".zip", initialfile=f"GOH-{manifest['checkpoint_id']}-{player}.zip", filetypes=[("ZIP", "*.zip")])
            if not dest:
                return
            output = harness.build_peer_package(checkpoint, player, dest, host)
            messagebox.showinfo("Пакет создан", f"Пакет участника сформирован:\n{output}\n\nДля каждой машины экспортируйте отдельный пакет.")
        except Exception as exc:
            messagebox.showerror("Ошибка экспорта", str(exc))

    @staticmethod
    def ask_value(title: str, prompt: str) -> str:
        import tkinter.simpledialog as sd
        return sd.askstring(title, prompt) or ""

    def import_package(self) -> None:
        source = filedialog.askopenfilename(title="Выберите пакет участника", filetypes=[("ZIP", "*.zip")])
        if not source:
            return
        target = filedialog.askdirectory(title="Папка для локальных архивов; выберите профиль save folder")
        if not target:
            return
        try:
            meta, issues = harness.inspect_peer_package(source)
            if issues:
                raise harness.HarnessError("; ".join(issues))
            if not messagebox.askyesno("Подтвердите импорт", f"Участник: {meta['participant_id']}\nCheckpoint: {meta['checkpoint_id']}\nМиссия: {meta['mission_id']}\nСборка: {meta['game_build']}\n\nИмпортировать локальные архивы без перезаписи существующих файлов?"):
                return
            result = harness.import_peer_package(source, target)
            messagebox.showinfo("Импорт выполнен", "Целостность пакета проверена. Файлы записаны:\n" + "\n".join(result["files"]))
        except Exception as exc:
            messagebox.showerror("Ошибка импорта", str(exc))

    def inspect_markers(self) -> None:
        temp = Path(tempfile.gettempdir())
        lines = []
        for name in (harness.CLIENT_MARKER, harness.SERVER_MARKER):
            path = temp / name
            marker, issues = harness.parse_marker(path)
            if issues:
                lines.append(f"{name}: " + "; ".join(issues))
            else:
                lines.append(f"{name}: mission={marker.mission}; archive={marker.archive}; players={marker.players_base}/{marker.players_count}; сопоставление текущей миссии и существование архива проверяются отдельно")
        messagebox.showinfo("TEMP-маркеры", "\n".join(lines) + "\n\nРежим A только читает файлы; игра самостоятельно удаляет принятый/просроченный marker.")

    def backup_markers(self) -> None:
        temp = Path(tempfile.gettempdir())
        backup_dir = default_store().parent / "backups" / "markers"
        saved = []
        for name in (harness.CLIENT_MARKER, harness.SERVER_MARKER):
            path = temp / name
            if path.exists():
                try:
                    saved.append(str(harness.backup_marker(path, backup_dir)))
                except Exception as exc:
                    saved.append(f"{name}: ERROR {exc}")
        messagebox.showinfo("Резервная копия marker", "\n".join(saved) if saved else "Существующих маркеров нет.")

    def show_coop_logs(self) -> None:
        lines = harness.read_coop_diagnostics(tempfile.gettempdir())
        self.diag_text.configure(state="normal")
        self.diag_text.delete("1.0", "end")
        self.diag_text.insert("end", "\n".join(lines) + "\n")
        self.diag_text.configure(state="disabled")

    def prepare_marker(self) -> None:
        if not messagebox.askyesno("Подтверждение готовности", "Все участники подтвердили готовность нового лобби? Создание marker запускает 15-минутный срок действия."):
            return
        checkpoint = Path(self.checkpoint_dir.get())
        if not checkpoint.is_dir():
            messagebox.showwarning("Marker", "Выберите checkpoint.")
            return
        metadata_path = filedialog.askopenfilename(title="Recovery metadata JSON", filetypes=[("JSON", "*.json")])
        if not metadata_path:
            return
        try:
            metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
            role = metadata.get("role")
            archive = filedialog.askopenfilename(title="Выберите локальную клиентскую или серверную копию", filetypes=[("GOH save", "*.sav")])
            if not archive:
                return
            marker_dir = filedialog.askdirectory(title="Каталог TEMP для тестового marker (в рабочей игре оставьте %TEMP%)")
            if not marker_dir:
                return
            if Path(marker_dir).resolve() != Path(tempfile.gettempdir()).resolve() and not messagebox.askyesno("Путь marker", "Указан нестандартный каталог. Загрузчик штатно смотрит Windows TEMP. Продолжить для изолированного теста?"):
                return
            path = harness.prepare_marker(checkpoint, metadata, role, archive, marker_dir, True)
            messagebox.showwarning("Marker подготовлен — эксперимент", f"Файл записан:\n{path}\n\nЭто не подтверждает работу в игре. Сверьте полный путь, тип процесса, архив и оставшееся время TTL. Резервная копия автоматизирована только для уже существующего marker (здесь перезапись запрещена).")
        except Exception as exc:
            messagebox.showerror("Подготовка остановлена", str(exc))

    def export_diagnostics(self) -> None:
        store = Path(self.store_dir.get())
        exe = Path(self.game_dir.get()) / "binaries" / "x64" / "call_to_arms.exe"
        report: dict[str, object] = {"tool": "GOHRecoveryManager", "version": APP_VERSION, "game_dir_set": bool(self.game_dir.get()), "game_file_version": game_file_version(exe) if exe.is_file() else None, "generated_local": datetime.now().astimezone().isoformat(timespec="seconds"), "coop_checkpoint_log_summary": harness.read_coop_diagnostics(tempfile.gettempdir()), "checkpoints": []}
        if store.is_dir():
            for d in store.iterdir():
                if d.is_dir() and (d / "manifest.json").is_file():
                    try:
                        m, issues = harness.read_checkpoint(d)
                        report["checkpoints"].append({"checkpoint_id": m.get("checkpoint_id"), "status": m.get("status"), "mission_id": m.get("mission_id"), "game_build": m.get("game_build"), "mod_fingerprint": m.get("mod_fingerprint"), "files": [{"owner": f.get("owner"), "path": Path(f.get("path", "")).name, "size_bytes": f.get("size_bytes"), "sha256": f.get("sha256")} for f in m.get("files", [])], "integrity_issues": issues})
                    except Exception as exc:
                        report["checkpoints"].append({"directory_name": d.name, "error": str(exc)})
        destination = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")], initialfile="GOH-recovery-diagnostics.json")
        if destination:
            Path(destination).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            messagebox.showinfo("Отчёт экспортирован", f"Перед передачей проверьте файл: {destination}")

    def validate_package_dialog(self) -> None:
        path = filedialog.askopenfilename(title="Пакет участника", filetypes=[("ZIP", "*.zip")])
        if not path:
            return
        try:
            meta, issues = harness.inspect_peer_package(path)
            messagebox.showinfo("Проверка пакета", json.dumps({"metadata": meta, "issues": issues}, ensure_ascii=False, indent=2))
        except Exception as exc:
            messagebox.showerror("Ошибка пакета", str(exc))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline GOH cooperative checkpoint recovery manager")
    parser.add_argument("--smoke-test", action="store_true", help="run packaged component smoke test and exit")
    args = parser.parse_args(argv)
    if args.smoke_test:
        return smoke_check()
    try:
        Manager().mainloop()
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
