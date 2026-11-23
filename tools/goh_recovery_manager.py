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
import threading
import queue
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import recovery_coordinator as coordinator
import recovery_harness as harness
import runtime_metadata as runtime
from archive_path_resolver import ArchivePathResolver
import field_test_report as field_reports

APP_VERSION = "0.2.1-field-test"
APP_AUTHOR = "Jerry93Sky"
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
        metadata = runtime.RuntimeMetadataCollector().parse_log(
            root / "absent.log")
        assert metadata["internal_mission_path"].status == runtime.MISSING
        assert ArchivePathResolver().resolve("@/profiles/test/save/coop_ckpt/", root, "test", "client").status == "UNVERIFIED_PATH"
    print(f"GOHRecoveryManager {APP_VERSION}: smoke check OK")
    return 0


class Manager(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("GOH Coop Recovery Manager — V0.2.1 field test")
        self.geometry("1040x720")
        self.minsize(850, 600)
        self.game_dir = tk.StringVar()
        self.store_dir = tk.StringVar(value=str(default_store()))
        self.checkpoint_dir = tk.StringVar()
        self.profile_dir = tk.StringVar()
        self.client_log_path = tk.StringVar()
        self.server_log_path = tk.StringVar()
        self.session_role = tk.StringVar(value="CLIENT")
        self.observation_status = tk.StringVar(value="Наблюдение выключено")
        self.collector = runtime.RuntimeMetadataCollector()
        self._watch_stop = threading.Event()
        self._watch_thread = None
        self._watch_queue: queue.Queue = queue.Queue()
        self.status = tk.StringVar(value="Диагностический режим. Игровая совместимость не проверена.")
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        header = ttk.Frame(self, padding=(12, 10))
        header.pack(fill="x")
        ttk.Label(header, text="GOH Coop Recovery", font=("Segoe UI", 18, "bold")).pack(side="left")
        ttk.Label(header, text="V0.2.1 • ЭКСПЕРИМЕНТ • TEST-A/B: NOT_RUN", foreground="#9a5700").pack(side="right")
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
        self.observation = ttk.Frame(tabs, padding=12)
        self.points = ttk.Frame(tabs, padding=12)
        self.recovery = ttk.Frame(tabs, padding=12)
        self.diagnostics = ttk.Frame(tabs, padding=12)
        tabs.add(self.connection, text="Игра")
        tabs.add(self.observation, text="Наблюдение")
        tabs.add(self.points, text="Контрольные точки")
        tabs.add(self.recovery, text="Восстановление")
        tabs.add(self.diagnostics, text="Диагностика")
        self._build_connection()
        self._build_observation()
        self._build_points()
        self._build_recovery()
        self._build_diagnostics()
        ttk.Label(self, textvariable=self.status, relief="sunken", anchor="w", padding=5).pack(fill="x", side="bottom")
        self.protocol("WM_DELETE_WINDOW", self._close_manager)
        self.after(500, self._drain_observation_events)

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

    def _build_observation(self) -> None:
        ttk.Label(self.observation, text="Локальный сбор метаданных", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(self.observation, text="Выберите папку одного профиля. Сканируются только его save\\coop_ckpt*.sav и точные диагностические логи Coop Checkpoint в TEMP. Синтетические сведения не используются.", wraplength=930).pack(anchor="w", pady=5)
        row = ttk.Frame(self.observation); row.pack(fill="x", pady=5)
        ttk.Entry(row, textvariable=self.profile_dir).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Выбрать профиль…", command=self.choose_profile).pack(side="left", padx=5)
        role_row = ttk.Frame(self.observation); role_row.pack(anchor="w", pady=5)
        ttk.Label(role_row, text="Роль этого компьютера:").pack(side="left")
        ttk.Combobox(role_row, textvariable=self.session_role, values=("HOST", "CLIENT"), state="readonly", width=12).pack(side="left", padx=6)
        for label, variable, process_role in (("Лог game/client process:", self.client_log_path, "client"),
                                               ("Лог локального server process (HOST):", self.server_log_path, "server")):
            log_row = ttk.Frame(self.observation); log_row.pack(fill="x", pady=2)
            ttk.Label(log_row, text=label, width=38).pack(side="left")
            ttk.Entry(log_row, textvariable=variable).pack(side="left", fill="x", expand=True)
            ttk.Button(log_row, text="Выбрать лог…", command=lambda v=variable, r=process_role: self.choose_role_log(v, r)).pack(side="left", padx=5)
        buttons = ttk.Frame(self.observation); buttons.pack(anchor="w", pady=5)
        ttk.Button(buttons, text="Наблюдать за сохранениями", command=self.start_observation).pack(side="left")
        ttk.Button(buttons, text="Остановить наблюдение", command=self.stop_observation).pack(side="left", padx=6)
        ttk.Button(buttons, text="Собрать доступные метаданные", command=self.collect_now).pack(side="left")
        ttk.Label(self.observation, textvariable=self.observation_status, foreground="#245b31").pack(anchor="w", pady=4)
        self.observation_text = tk.Text(self.observation, wrap="word")
        self.observation_text.pack(fill="both", expand=True, pady=6)
        self._text(self.observation_text, "Для каждой роли используется только точный лог: call_to_arms.exe.log для game process и call_to_arms_server.exe.log для локального server process. Роль HOST/CLIENT задаётся отдельно. Для сервера укажите лог из TEMP именно серверного процесса; разные TEMP блокируют подготовку, если этот путь не установлен. Каждое поле сопровождается источником и методом.\n")

    def choose_role_log(self, variable: tk.StringVar, role: str) -> None:
        expected = "goh_coop_checkpoint_call_to_arms.exe.log" if role == "client" else "goh_coop_checkpoint_call_to_arms_server.exe.log"
        selected = filedialog.askopenfilename(title=f"Выберите точный {role} process log", filetypes=[("Ожидаемый лог", expected), ("Все файлы", "*.*")])
        if selected:
            if Path(selected).name.casefold() != expected.casefold():
                messagebox.showerror("Неверная роль лога", f"Для {role} ожидается файл {expected}.")
                return
            variable.set(str(Path(selected).resolve()))

    def _role_log_path(self, role: str, required: bool = True) -> Path | None:
        variable = self.client_log_path if role == "client" else self.server_log_path
        selected = variable.get().strip()
        if not selected:
            if required:
                raise harness.HarnessError(f"UNSELECTED_ROLE_LOG: выберите точный файл лога {role} process; TEMP менеджера не используется как предположение")
            return None
        candidates = [Path(selected)]
        try:
            return self.collector.select_role_log(candidates, role)
        except Exception as exc:
            if required:
                raise harness.HarnessError(f"Нет однозначного {role} process log: {exc}") from exc
            return None

    def choose_profile(self) -> None:
        path = filedialog.askdirectory(title="Выберите корень профиля (содержит save)")
        if path:
            self.profile_dir.set(path)

    def _observation_files(self) -> list[Path]:
        profile = Path(self.profile_dir.get()).expanduser()
        return [p.resolve() for p in (profile / "save" / "coop_ckpt.sav", profile / "save" / "coop_ckpt_srv.sav") if p.is_file()]

    def _metadata_context(self, log_path: Path | None, profile_root: Path, game_root: Path) -> dict:
        fields = self.collector.parse_log(log_path) if log_path else {}
        fields["profile_root"] = runtime.FieldValue(str(profile_root.resolve()), runtime.OPERATOR_PROVIDED,
            None, runtime.utc_now(), "user selected profile directory")
        exe = game_root / "binaries/x64/call_to_arms.exe"
        version = game_file_version(exe) if exe.is_file() else None
        fields["game_exe_file_version"] = runtime.FieldValue(version, runtime.OBSERVED if version else runtime.MISSING,
            str(exe) if exe.is_file() else None, runtime.utc_now(), "Windows PE version resource; not the confirmed in-game build")
        inventory = self.collector.inspect_installation(game_root) if game_root.is_dir() else []
        hashes = {r["component"]: r["sha256"] for r in inventory if r.get("sha256")}
        fields["known_mod_file_hashes"] = runtime.FieldValue(hashes or None,
            runtime.OBSERVED if hashes else runtime.MISSING, str(game_root), runtime.utc_now(),
            "SHA-256 of discovered known loader/mod files; activation and mod list not inferred")
        return fields

    def _append_observation(self, value: str) -> None:
        self.observation_text.configure(state="normal")
        self.observation_text.insert("end", value + "\n")
        self.observation_text.see("end")
        self.observation_text.configure(state="disabled")

    def collect_now(self) -> None:
        profile = Path(self.profile_dir.get()).expanduser()
        if not profile.is_dir():
            messagebox.showwarning("Наблюдение", "Сначала выберите существующую папку профиля.")
            return
        selected_game = Path(self.game_dir.get()).expanduser() if self.game_dir.get() else Path("__no_game_selected__")
        files = self._observation_files()
        if not files:
            self._append_observation("Сохранения coop_ckpt.sav / coop_ckpt_srv.sav не найдены в выбранном profile\\save.")
        for source in files:
            role = "server" if source.name.casefold() == "coop_ckpt_srv.sav" else "client"
            try:
                log_path = self._role_log_path(role)
                fields = self._metadata_context(log_path, profile, selected_game)
                snap = self.collector.observe_save(source, role, Path(self.store_dir.get()) / "observed-local", fields, stable_interval=2.0, stable_samples=2)
                record = snap.to_dict()
                record["session_role"] = {"value": self.session_role.get(), "status": runtime.OPERATOR_PROVIDED,
                    "source_path": None, "observed_at_utc": runtime.utc_now(), "method": "GUI operator selection"}
                out = Path(self.store_dir.get()) / "observed-local" / (Path(snap.copy_path).parent.name + ".json")
                out.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                self._append_observation(f"Роль {role}; источник метаданных: {log_path}; архив: {source}; {snap.size_bytes} байт; SHA-256 {snap.sha256}; {snap.engine_write_confirmation}.\n" + ", ".join(f"{k}={v.value!r} [{v.status}] source={v.source_path} process_role={v.process_role}" for k, v in snap.metadata.items()))
            except Exception as exc:
                self._append_observation(f"Наблюдение остановлено для {source.name}: {exc}")

    def start_observation(self) -> None:
        if self._watch_thread and self._watch_thread.is_alive():
            return
        profile = Path(self.profile_dir.get()).expanduser()
        if not profile.is_dir():
            messagebox.showwarning("Наблюдение", "Выберите корень профиля до запуска наблюдения.")
            return
        watch_files = [profile / "save" / n for n in ("coop_ckpt.sav", "coop_ckpt_srv.sav")]
        for process_role in ("client", "server"):
            try:
                watch_files.append(self._role_log_path(process_role))
            except harness.HarnessError:
                pass
        watch_files = [p for p in watch_files if p.parent.is_dir()]
        if not watch_files:
            messagebox.showinfo("Наблюдение", "Каталог профиля недоступен.")
            return
        self._watch_stop.clear()
        store_root = Path(self.store_dir.get()) / "observed-local"
        operator_role = self.session_role.get()
        selected_profile = profile.resolve()
        selected_game = Path(self.game_dir.get()).expanduser() if self.game_dir.get() else Path("__no_game_selected__")
        def changed(path, signature):
            self._watch_queue.put((path, signature))
        def worker():
            self.collector.watch(watch_files, changed, self._watch_stop, interval=3.0)
        self._watch_thread = threading.Thread(target=worker, name="GOH-readonly-observer", daemon=True)
        self._watch_thread.start()
        self.observation_status.set(f"Наблюдение включено: {len(watch_files)} точных файлов/путей, опрос 3 с. Новые лог-файлы TEMP требуют перезапуска наблюдения.")

    def _drain_observation_events(self) -> None:
        while True:
            try:
                path, signature = self._watch_queue.get_nowait()
            except queue.Empty:
                break
            self._append_observation(f"Изменение обнаружено: {path.name}; size={signature[0]}. Ожидается локальная стабилизация; это не подтверждение завершения движком.")
            if path.name.casefold() in {"coop_ckpt.sav", "coop_ckpt_srv.sav"}:
                role = "server" if path.name.casefold().endswith("_srv.sav") else "client"
                def snapshot_worker(source=path, archive_role=role, out_root=store_root, selected_role=operator_role,
                                    profile_root=selected_profile, game_root=selected_game):
                    try:
                        log_path = self._role_log_path(archive_role)
                        metadata = self._metadata_context(log_path, profile_root, game_root)
                        snap = self.collector.observe_save(source, archive_role,
                            out_root, metadata, stable_interval=2, stable_samples=3, cancel=self._watch_stop)
                        record = snap.to_dict()
                        record["session_role"] = {"value": selected_role, "status": runtime.OPERATOR_PROVIDED,
                            "source_path": None, "observed_at_utc": runtime.utc_now(), "method": "GUI operator selection"}
                        self._watch_queue.put(("snapshot", (snap, record)))
                    except Exception as exc:
                        self._watch_queue.put(("error", (source.name, str(exc))))
                threading.Thread(target=snapshot_worker, name="GOH-save-copy", daemon=True).start()
            elif path.name == "snapshot":
                snap, record = signature
                self._append_observation(f"Снимок {snap.role}: источник метаданных={snap.metadata.get('process_role').source_path if snap.metadata.get('process_role') else None}; архив={snap.source_path}; копия={snap.copy_path}; SHA-256={snap.sha256}; {snap.engine_write_confirmation}")
                try:
                    output = Path(snap.copy_path).parent / "observation.json"
                    output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                except OSError as exc:
                    self._append_observation(f"Метаданные snapshot не сохранены: {exc}")
            elif path.name == "error":
                self._append_observation(f"Снимок не создан: {signature[0]}: {signature[1]}")
        if self.winfo_exists():
            self.after(500, self._drain_observation_events)

    def stop_observation(self) -> None:
        self._watch_stop.set()
        self.observation_status.set("Запрошено безопасное завершение наблюдения.")

    def _close_manager(self) -> None:
        self._watch_stop.set()
        self.destroy()

    def _build_recovery(self) -> None:
        ttk.Label(self.recovery, text="Экспорт / импорт пакета участника", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        row = ttk.Frame(self.recovery)
        row.pack(fill="x", pady=6)
        ttk.Label(row, text="ID checkpoint:").pack(side="left")
        ttk.Entry(row, textvariable=self.checkpoint_dir).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row, text="Выбрать…", command=self.choose_checkpoint).pack(side="left")
        ttk.Button(self.recovery, text="Экспорт пакета участника…", command=self.export_package).pack(anchor="w", pady=4)
        ttk.Button(self.recovery, text="Импорт и проверка пакета…", command=self.import_package).pack(anchor="w", pady=4)
        ttk.Button(self.recovery, text="Сверить пакеты всех участников…", command=self.validate_peer_group_dialog).pack(anchor="w", pady=4)
        ttk.Separator(self.recovery).pack(fill="x", pady=12)
        ttk.Label(self.recovery, text="Режим A — проверить существующие маркеры", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        marker_row = ttk.Frame(self.recovery)
        marker_row.pack(anchor="w", pady=6)
        ttk.Button(marker_row, text="Проверить локальные TEMP-маркеры", command=self.inspect_markers).pack(side="left")
        ttk.Button(marker_row, text="Сохранить копию существующих marker", command=self.backup_markers).pack(side="left", padx=8)
        ttk.Label(self.recovery, text="Локальная подготовка для HOST/CLIENT", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(8, 0))
        ttk.Label(self.recovery, text="HOST требует локальные client и server архивы; для них готовятся отдельные marker. CLIENT требует собственный client архив. Mission path, virtual archive path и player mapping берутся из авторского лога, если доступны; отсутствующие точные значения запрашиваются отдельно. Участники подтверждают готовность вручную.", wraplength=930).pack(anchor="w", pady=5)
        ttk.Button(self.recovery, text="Подготовить локальный marker…", command=self.prepare_marker).pack(anchor="w", pady=5)
        ttk.Label(self.recovery, text="Подготовка запускает 15-минутный TTL. Кнопка требует явного подтверждения готовности всех участников и не перезаписывает существующий marker. Без точного player mapping подготовка запрещена.", foreground="#8a4200", wraplength=900).pack(anchor="w")

    def _build_diagnostics(self) -> None:
        row = ttk.Frame(self.diagnostics)
        row.pack(fill="x")
        ttk.Button(row, text="Собрать очищенный отчёт…", command=self.export_diagnostics).pack(side="left")
        ttk.Button(row, text="Проверить выбранный recovery пакет…", command=self.validate_package_dialog).pack(side="left", padx=8)
        ttk.Button(row, text="Записать результат TEST-A/B…", command=self.record_field_outcome).pack(side="left", padx=8)
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
            for record in self.collector.inspect_installation(p):
                items.append(f"{record['file_status']}: {record['component']}: {record['path']}")
                if record["loader_status"] == "LOADER_IDENTIFIED":
                    items.append("  LOADER_IDENTIFIED по опубликованному имени; RUNTIME_UNVERIFIED")
                if record.get("sha256"):
                    items.append(f"  SHA-256: {record['sha256']}")
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
        self.mods_info.insert("end", "\n".join(items) + "\n\nFILE_FOUND = найден только файл. LOADER_IDENTIFIED = найден файл с известным именем, но запуск не подтверждён. RUNTIME_UNVERIFIED = совместимость/фактическая загрузка неизвестна.\nКаталог мода не подтверждает, что он включён в профиле или одинаков у всех участников. Наличие version.dll само по себе не идентифицирует её содержимое.\n")
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

    def validate_peer_group_dialog(self) -> None:
        packages = filedialog.askopenfilenames(title="Выберите ZIP каждого участника", filetypes=[("ZIP", "*.zip")])
        if not packages:
            return
        host = self.ask_value("HOST participant ID", "Введите player ID компьютера-хоста:")
        if not host:
            return
        protocol_path = filedialog.askopenfilename(title="Выберите ручной протокол сопоставления checkpoint (JSON); без него preflight блокируется", filetypes=[("JSON", "*.json")])
        protocol = None
        if protocol_path:
            try:
                protocol = json.loads(Path(protocol_path).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                messagebox.showerror("Протокол checkpoint", f"Не удалось прочитать JSON: {exc}")
                return
        result = harness.validate_peer_group(list(packages), host, protocol)
        messagebox.showinfo("Проверка набора файлов", json.dumps(result, ensure_ascii=False, indent=2))

    def inspect_markers(self) -> None:
        lines = []
        for role, name in (("client", harness.CLIENT_MARKER), ("server", harness.SERVER_MARKER)):
            try:
                log = self._role_log_path(role)
            except harness.HarnessError as exc:
                lines.append(f"{role}: marker path unknown; {exc}")
                continue
            path = log.parent / name
            marker, issues = harness.parse_marker(path)
            if issues:
                lines.append(f"{role}: source log={log}; marker={path}: " + "; ".join(issues))
            else:
                lines.append(f"{role}: source log={log}; marker={path}; mission={marker.mission}; archive={marker.archive}; players={marker.players_base}/{marker.players_count}; runtime match проверяется отдельно")
        messagebox.showinfo("Role-specific markers", "\n".join(lines) + "\n\nРежим только читает файлы; игра самостоятельно удаляет принятый/просроченный marker.")

    def backup_markers(self) -> None:
        backup_dir = default_store().parent / "backups" / "markers"
        saved = []
        for role, name in (("client", harness.CLIENT_MARKER), ("server", harness.SERVER_MARKER)):
            try:
                log = self._role_log_path(role)
            except harness.HarnessError as exc:
                saved.append(f"{role}: backup skipped; {exc}")
                continue
            path = log.parent / name
            if path.exists():
                try:
                    saved.append(str(harness.backup_marker(path, backup_dir)))
                except Exception as exc:
                    saved.append(f"{name}: ERROR {exc}")
        messagebox.showinfo("Резервная копия marker", "\n".join(saved) if saved else "Существующих маркеров нет.")

    def show_coop_logs(self) -> None:
        role_logs = {}
        for role in ("client", "server"):
            try:
                role_logs[role] = self._role_log_path(role)
            except harness.HarnessError:
                pass
        lines = harness.read_coop_diagnostics(tempfile.gettempdir(), role_logs=role_logs)
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
        try:
            manifest, issues = harness.read_checkpoint(checkpoint)
            if issues:
                raise harness.HarnessError("Checkpoint package is incomplete: " + "; ".join(issues))
            if self.session_role.get() == "HOST":
                role = "server" if messagebox.askyesno("HOST marker", "Подготовить SERVER marker? Выберите Нет для CLIENT marker хоста.") else "client"
            else:
                role = "client"
            player = self.ask_value("Участник", "Выберите точный participant ID из roster: " + ", ".join(manifest.get("expected_players", [])))
            if not player:
                return
            profile = Path(self.profile_dir.get()).expanduser().resolve()
            if not profile.is_dir():
                raise harness.HarnessError("Выберите точный локальный профиль на вкладке Наблюдение")
            profile_id = profile.name
            if not messagebox.askyesno("Профиль", f"Использовать ID профиля '{profile_id}' из выбранной папки? Проверяется точный файл save\\coop_ckpt*.sav."):
                return
            log_path = self._role_log_path(role)
            observed = self.collector.parse_log(log_path)
            if observed["process_role"].value != role:
                raise harness.HarnessError("Выбранный лог не подтверждает ожидаемую роль процесса")
            protocol_path = filedialog.askopenfilename(title="Выберите ручной JSON протокол сопоставления checkpoint", filetypes=[("JSON", "*.json")])
            if not protocol_path:
                return
            try:
                protocol = json.loads(Path(protocol_path).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise harness.HarnessError(f"Не удалось прочитать протокол: {exc}") from exc
            protocol_ok, protocol_issues = harness.validate_consistency_protocol(
                protocol, list(manifest.get("expected_players", [])), str(manifest.get("mission_id")),
                str(manifest.get("checkpoint_id")))
            if not protocol_ok:
                raise harness.HarnessError("Общая точка не подтверждена протоколом: " + "; ".join(protocol_issues))
            def observed_value(key: str):
                value = observed.get(key)
                if not value or value.status != runtime.OBSERVED or value.value in (None, ""):
                    raise harness.HarnessError(f"Preflight заблокирован: {key} отсутствует в выбранном {role} логе; значения не подставляются вручную")
                return value.value
            mission_path = observed_value("internal_mission_path")
            archive_path = observed_value("virtual_archive_base_path")
            mapping = observed_value("player_mapping")
            if not mission_path or not archive_path or not mapping:
                return
            metadata = {"checkpoint_id": manifest.get("checkpoint_id"), "internal_mission_path": mission_path,
                "archive_path": archive_path, "player_id": player, "game_build": manifest.get("game_build"),
                "mission_id": manifest.get("mission_id"), "mod_fingerprint": manifest.get("mod_fingerprint"),
                "is_host": self.session_role.get() == "HOST", "role": role, "player_mapping": mapping,
                "profile_root": str(profile), "profile_id": profile_id,
                "process_role": role, "source_log_path": str(log_path),
                "source_log_sha256": observed["source_log_sha256"].value,
                "observed_log_fields": {name: observed[name].value for name in
                    ("internal_mission_path", "virtual_archive_base_path", "quant_clock", "checkpoint_id")},
                "consistency_protocol": protocol, "host_player_id": protocol.get("host_player_id")}
            resolution = ArchivePathResolver().resolve(archive_path, profile, profile_id, role)
            if resolution.status != "RESOLVED_LOCAL_FILE":
                raise harness.HarnessError("UNVERIFIED_PATH: " + resolution.reason)
            marker_dir = log_path.parent.resolve()
            log_mtime = datetime.fromtimestamp(log_path.stat().st_mtime).astimezone().isoformat(timespec="seconds")
            preview = (f"Роль процесса: {role}\nИсточник метаданных: {log_path}\n"
                       f"Log modified: {log_mtime}; SHA-256: {observed['source_log_sha256'].value}\n"
                       f"Архив checkpoint: {resolution.local_file}\nMarker: {marker_dir / (harness.SERVER_MARKER if role == 'server' else harness.CLIENT_MARKER)}\n"
                       "Preflight: пройдены локальные проверки manifest/owner/size/SHA-256/profile/log/protocol. "
                       "Локальные номера checkpoint не являются общим ID.\n\n"
                       "Подтвердите, что TEMP/TMP этого процесса не изменились после создания лога. Для server process его каталог установлен по точному server log; если это не его фактический TEMP, отмените операцию.")
            if not messagebox.askyesno("Preflight marker", preview):
                return
            path = harness.prepare_marker(checkpoint, metadata, role, resolution.local_file, marker_dir, True)
            messagebox.showwarning("Marker подготовлен — эксперимент", f"Роль: {role}\nИсточник: {log_path}\nАрхив: {resolution.local_file}\nMarker: {path}\nPreflight: PASS (локальные проверки)\n\nЭто не подтверждает синхронность или загрузку в игре. TEST-A/TEST-B остаются NOT_RUN.")
        except Exception as exc:
            messagebox.showerror("Подготовка остановлена", str(exc))

    def export_diagnostics(self) -> None:
        store = Path(self.store_dir.get())
        exe = Path(self.game_dir.get()) / "binaries" / "x64" / "call_to_arms.exe"
        report: dict[str, object] = {"tool": "GOHRecoveryManager", "version": APP_VERSION, "author": APP_AUTHOR, "game_dir_set": bool(self.game_dir.get()), "game_file_version": game_file_version(exe) if exe.is_file() else None, "generated_local": datetime.now().astimezone().isoformat(timespec="seconds"), "coop_checkpoint_log_summary": harness.read_coop_diagnostics(tempfile.gettempdir()), "checkpoints": [], "field_test_reports": []}
        if store.is_dir():
            for d in store.iterdir():
                if d.is_dir() and (d / "manifest.json").is_file():
                    try:
                        m, issues = harness.read_checkpoint(d)
                        report["checkpoints"].append({"checkpoint_id": m.get("checkpoint_id"), "status": m.get("status"), "mission_id": m.get("mission_id"), "game_build": m.get("game_build"), "mod_fingerprint": m.get("mod_fingerprint"), "files": [{"owner": f.get("owner"), "path": Path(f.get("path", "")).name, "size_bytes": f.get("size_bytes"), "sha256": f.get("sha256")} for f in m.get("files", [])], "integrity_issues": issues})
                    except Exception as exc:
                        report["checkpoints"].append({"directory_name": d.name, "error": str(exc)})
            field_report_root = store / "results" / "field-test-reports"
            if field_report_root.is_dir():
                for result_path in sorted(field_report_root.glob("*.json")):
                    try:
                        value = json.loads(result_path.read_text(encoding="utf-8"))
                        report["field_test_reports"].append({k: value.get(k) for k in
                            ("report_id", "checkpoint_id", "test_id", "operator_reported_outcome", "confirmed_real_game", "evidence_file_names", "recorded_at_utc", "restore_verified_status")})
                    except (OSError, json.JSONDecodeError):
                        report["field_test_reports"].append({"status": "UNREADABLE_REPORT", "file": result_path.name})
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

    def record_field_outcome(self) -> None:
        checkpoint = self.selected_dir()
        if not checkpoint:
            messagebox.showwarning("Результат теста", "Сначала выберите checkpoint в соответствующей вкладке.")
            return
        test_id = self.ask_value("Испытание", "Введите TEST-A или TEST-B:").strip().upper()
        outcome = self.ask_value("Результат", "Введите PASS, FAIL или INCONCLUSIVE:").strip().upper()
        if test_id not in {"TEST-A", "TEST-B"} or outcome not in {"PASS", "FAIL", "INCONCLUSIVE"}:
            messagebox.showwarning("Результат теста", "Допустимы TEST-A/TEST-B и PASS/FAIL/INCONCLUSIVE.")
            return
        evidence = []
        confirmed = False
        if outcome in {"PASS", "FAIL"}:
            confirmed = messagebox.askyesno("Реальный игровой прогон", "Вы лично провели выбранное испытание в реальной игре со всеми участниками?")
            if not confirmed:
                messagebox.showwarning("Результат теста", "PASS/FAIL не записан без подтверждения игрового прогона.")
                return
            evidence = filedialog.askopenfilenames(title="Выберите игровые логи/свидетельства", filetypes=[("Evidence", "*.log *.txt *.json *.png *.jpg *.jpeg *.mp4"), ("All files", "*.*")])
            if not evidence:
                messagebox.showwarning("Результат теста", "Для PASS/FAIL требуется хотя бы один файл evidence.")
                return
        notes = self.ask_value("Примечание", "Краткое примечание (необязательно):")
        try:
            report = field_reports.record_field_outcome(Path(self.store_dir.get()), checkpoint.name,
                test_id, outcome, list(evidence), confirmed, notes)
            messagebox.showinfo("Результат сохранён", f"Локальная запись создана:\n{report}\n\nОна не устанавливает RESTORE_VERIFIED автоматически.")
            self.status.set(f"{test_id}: {outcome} сохранён как операторский отчёт; статус восстановления не менялся")
        except Exception as exc:
            messagebox.showerror("Ошибка результата", str(exc))


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
