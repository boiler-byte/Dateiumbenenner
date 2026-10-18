"""
Datei- und Ordnerumbenennung - Flet 1.0.4
Python 3.10+

Start:           python main.py
Build (Windows): pip install flet pyinstaller && flet pack main.py --name DateiUmbenenner
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

import flet as ft
from platformdirs import user_config_dir

# ---------------------------------------------------------------------------
# Persistenz
# ---------------------------------------------------------------------------
CONFIG_DIR = Path(user_config_dir("FileRenamer"))
CONFIG_FILE = CONFIG_DIR / "config.json"
HISTORY_FILE = CONFIG_DIR / "history.json"


def _ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    _ensure_config_dir()
    default = {
        "last_directory": "",
        "case_sensitive": True,
        "handle_collisions": False,
    }
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            default.update({k: data.get(k, default[k]) for k in default})
        except Exception:
            pass
    return default


def save_config(cfg: dict) -> None:
    _ensure_config_dir()
    CONFIG_FILE.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_history() -> list[dict]:
    _ensure_config_dir()
    if not HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data[-10:]
    except Exception:
        pass
    return []


def save_history(history: list[dict]) -> None:
    _ensure_config_dir()
    history = history[-10:]
    HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Kern-Logik
# ---------------------------------------------------------------------------
def compile_search_pattern(search: str, case_sensitive: bool) -> re.Pattern | None:
    """
    Wandelt einen Suchbegriff mit Wildcards in ein Regex-Muster um.

    Wildcards:
      *  → beliebig viele Zeichen (auch keine)   → .*
      ?  → genau ein Zeichen                     → .
      Alles andere wird literal behandelt.

    Sonderfall: Besteht das Muster ausschließlich aus Wildcards (keine
    literalen Zeichen), wird es mit \\A...\\Z verankert. Das verhindert das
    bekannte re.sub-Verhalten, dass ein Muster wie .* am Ende der Zeichenkette
    ein zweites (leeres) Match erzeugt und dadurch der Ersatz doppelt
    eingefügt wird (z. B. search="*", replace="blub" ergäbe sonst "blubblub").
    """
    if not search:
        return None

    parts: list[str] = []
    has_literal = False
    for ch in search:
        if ch == "*":
            parts.append(".*")
        elif ch == "?":
            parts.append(".")
        else:
            parts.append(re.escape(ch))
            has_literal = True

    body = "".join(parts)
    if not has_literal:
        body = rf"\A{body}\Z"

    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(body, flags)


def apply_rules(name: str, rules: list[dict], case_sensitive: bool) -> tuple[str, bool]:
    """
    Wendet die Regel-Liste sequenziell auf `name` an.

    Wildcards im Suchbegriff:
      *  → beliebig viele Zeichen
      ?  → genau ein Zeichen

    required=True bedeutet:
      Die Regel wird NUR angewendet, wenn der AKTUELLE Name (nach vorherigen
      Regeln) den Suchbegriff enthält. Andernfalls wird sie übersprungen.
    required=False bedeutet:
      Die Regel wird immer angewendet (Substitution, auch wenn nichts matcht –
      dann bleibt der Name unverändert).

    Rückgabe: (result, applied)
    """
    result = name
    applied = False
    for rule in rules:
        search = rule.get("search", "")
        replace = rule.get("replace", "")
        required = bool(rule.get("required", False))
        pattern = compile_search_pattern(search, case_sensitive)
        if pattern is None:
            continue
        if required:
            if pattern.search(result):
                result = pattern.sub(replace, result)
                applied = True
        else:
            result = pattern.sub(replace, result)
            applied = True
    return result, applied


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    return name.strip().rstrip(".") or "_"


def scan_targets(directory: str, mode: str, include_subfolders: bool) -> list[Path]:
    if not directory or not Path(directory).is_dir():
        return []
    root = Path(directory)
    results: list[Path] = []
    if include_subfolders:
        for current, dirs, files in os.walk(root):
            cur = Path(current)
            if mode == "file":
                for f in files:
                    results.append(cur / f)
            else:
                for d in dirs:
                    results.append(cur / d)
    else:
        for entry in sorted(root.iterdir()):
            if mode == "file" and entry.is_file():
                results.append(entry)
            elif mode == "dir" and entry.is_dir():
                results.append(entry)
    return results


def _build_numbered_name(
    desired: str, suffix: str, number: int, leading_zeros: int, is_file: bool
) -> str:
    """Baut aus 'desired' einen Namen mit Suffix + Nummer vor der Endung."""
    if is_file:
        stem = Path(desired).stem
        ext = Path(desired).suffix
    else:
        stem = desired
        ext = ""
    num = str(number).zfill(max(0, leading_zeros))
    return f"{stem}{suffix}{num}{ext}"


def build_preview(
    directory: str,
    mode: str,
    include_subfolders: bool,
    rules: list[dict],
    case_sensitive: bool,
    handle_collisions: bool,
    suffix: str,
    leading_zeros: int,
) -> list[dict]:
    """
    Erzeugt die Vorschau inkl. Kollisionsauflösung.

    Wichtige Prinzipien:
    - Ein Eintrag wird nur dann als "betroffen" betrachtet, wenn sich sein
      Name durch die Regeln tatsächlich ändert.
    - Kollisionen werden NUR zwischen betroffenen Einträgen bzw. zwischen
      betroffenen Einträgen und NICHT betroffenen (also unveränderten)
      Einträgen im selben Verzeichnis geprüft.
    - Der eigene Originalname eines Eintrags zählt NICHT als Kollision.
    - Bei deaktivierter Kollisionsbehandlung werden kollidierende Einträge
      als "Kollision" markiert (und erscheinen trotzdem in der Vorschau!).
    - Bei aktivierter Kollisionsbehandlung werden sie mit Suffix+Nummer
      eindeutig gemacht und als "OK (angepasst)" markiert.
    """
    targets = scan_targets(directory, mode, include_subfolders)
    preview: list[dict] = []
    is_file_mode = mode == "file"

    for p in targets:
        old_name = p.name
        new_raw, _applied = apply_rules(old_name, rules, case_sensitive)
        new_name = sanitize_filename(new_raw)
        name_changed = new_name != old_name

        if not new_name or not name_changed:
            status = "Übersprungen"
        else:
            status = "OK"

        preview.append(
            {
                "path": str(p),
                "old": old_name,
                "new": new_name,
                "status": status,
                "parent": str(p.parent),
                "affected": name_changed,
            }
        )

    # Gruppierung nach Elternverzeichnis
    per_parent: dict[str, list[dict]] = {}
    for item in preview:
        per_parent.setdefault(item["parent"], []).append(item)

    for parent, items in per_parent.items():
        # Namen der Einträge, die selbst umbenannt werden (deren Originalnamen
        # also "frei" werden und nicht als blockierend zählen).
        affected_old_names = {it["old"].lower() for it in items if it["affected"]}

        # Alle aktuell im Verzeichnis existierenden Namen (lowercase)
        try:
            all_names = {p.name.lower() for p in Path(parent).iterdir()}
        except Exception:
            all_names = set()

        # "Blockierende" Namen = existierende Namen, die NICHT selbst umbenannt
        # werden. Nur diese können echte Kollisionen verursachen.
        blocking = all_names - affected_old_names

        # Zielnamen zählen (nur betroffene Einträge)
        target_counts: dict[str, int] = {}
        for it in items:
            if not it["affected"]:
                continue
            key = it["new"].lower()
            target_counts[key] = target_counts.get(key, 0) + 1

        reserved: set[str] = set()
        counter: dict[str, int] = {}

        for it in items:
            if not it["affected"]:
                continue

            desired = it["new"]
            key = desired.lower()

            # Kollision, wenn:
            # - mehrere betroffene Einträge denselben Zielnamen haben
            # - der Zielname mit einer nicht-umbenannten Datei kollidiert
            # - der Zielname bereits von einem anderen Eintrag reserviert wurde
            collides_with_other_target = target_counts.get(key, 0) > 1
            collides_with_existing = key in blocking
            collides_with_reserved = key in reserved

            needs_resolution = (
                collides_with_other_target
                or collides_with_existing
                or collides_with_reserved
            )

            if not needs_resolution:
                reserved.add(key)
                continue

            if not handle_collisions:
                it["status"] = "Kollision"
                reserved.add(key)
                continue

            # Auflösung: Suffix + laufende Nummer
            n = counter.get(key, 0)
            while True:
                n += 1
                candidate = _build_numbered_name(
                    desired, suffix or "_", n, leading_zeros, is_file_mode
                )
                cand_key = candidate.lower()
                if cand_key not in blocking and cand_key not in reserved:
                    break
            counter[key] = n
            reserved.add(cand_key)
            it["new"] = candidate
            it["status"] = "OK (angepasst)"

    return preview


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
def main(page: ft.Page) -> None:
    page.title = "Datei- und Ordnerumbenennung"
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 16
    page.window.width = 1200
    page.window.height = 800

    config = load_config()
    history: list[dict] = load_history()

    state: dict = {
        "mode": "file",
        "directory": config.get("last_directory", ""),
        "include_subfolders": False,
        "case_sensitive": bool(config.get("case_sensitive", True)),
        "handle_collisions": bool(config.get("handle_collisions", False)),
        "suffix": "_",
        "leading_zeros": 0,
    }

    rules_ui: list[dict] = []

    # --- Verzeichnis ------------------------------------------------------
    directory_field = ft.TextField(
        label="Verzeichnis", value=state["directory"], read_only=True, expand=True
    )

    file_picker = ft.FilePicker()

    async def open_directory_dialog(e):
        path = await file_picker.get_directory_path(
            dialog_title="Verzeichnis wählen",
            initial_directory=state["directory"] or None,
        )
        if path:
            state["directory"] = path
            directory_field.value = path
            config["last_directory"] = path
            save_config(config)
            refresh_preview()
            page.update()

    directory_button = ft.Button(
        "Verzeichnis wählen", icon=ft.Icons.FOLDER_OPEN, on_click=open_directory_dialog
    )

    # --- Modus ------------------------------------------------------------
    mode_segmented = ft.SegmentedButton(
        selected=["file"],
        allow_multiple_selection=False,
        segments=[
            ft.Segment(value="file", label=ft.Text("Dateien umbenennen")),
            ft.Segment(value="dir", label=ft.Text("Ordner umbenennen")),
        ],
    )

    def on_mode_change(e):
        state["mode"] = next(iter(mode_segmented.selected))
        refresh_preview()
        page.update()

    mode_segmented.on_change = on_mode_change

    # --- Checkboxen -------------------------------------------------------
    def on_subfolders_change(e):
        state["include_subfolders"] = e.control.value
        refresh_preview()
        page.update()

    subfolders_checkbox = ft.Checkbox(
        label="Unterordner einbeziehen",
        value=state["include_subfolders"],
        on_change=on_subfolders_change,
    )

    def on_case_change(e):
        state["case_sensitive"] = e.control.value
        config["case_sensitive"] = e.control.value
        save_config(config)
        refresh_preview()
        page.update()

    case_checkbox = ft.Checkbox(
        label="Groß-/Kleinschreibung beachten",
        value=state["case_sensitive"],
        on_change=on_case_change,
    )

    # --- Kollisionsoptionen ----------------------------------------------
    def on_suffix_change(e):
        state["suffix"] = e.control.value or "_"
        refresh_preview()
        page.update()

    suffix_field = ft.TextField(
        label="Suffix",
        value=state["suffix"],
        width=100,
        on_change=on_suffix_change,
    )

    def on_leading_zeros_change(e):
        state["leading_zeros"] = _parse_int(e.control.value)
        refresh_preview()
        page.update()

    leading_zeros_field = ft.TextField(
        label="Führende Nullen",
        value=str(state["leading_zeros"]),
        width=140,
        keyboard_type=ft.KeyboardType.NUMBER,
        on_change=on_leading_zeros_change,
    )

    collision_options_container = ft.Row(
        spacing=16,
        visible=state["handle_collisions"],
        controls=[
            ft.Text("Suffix:", size=12),
            suffix_field,
            leading_zeros_field,
        ],
    )

    def on_collision_checkbox_change(e):
        state["handle_collisions"] = e.control.value
        config["handle_collisions"] = e.control.value
        save_config(config)
        collision_options_container.visible = e.control.value
        refresh_preview()
        page.update()

    collision_checkbox = ft.Checkbox(
        label="Kollisionen behandeln",
        value=state["handle_collisions"],
        on_change=on_collision_checkbox_change,
    )

    # --- Regeln -----------------------------------------------------------
    rules_column = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)

    def rebuild_rules_ui():
        rules_column.controls.clear()
        for idx, r in enumerate(rules_ui):
            row = ft.Row(
                spacing=6,
                controls=[
                    ft.TextField(
                        label="Wildcard Suche",
                        value=r["search"].value,
                        expand=2,
                        on_change=lambda e, i=idx: (
                            setattr(rules_ui[i]["search"], "value", e.control.value),
                            refresh_preview(),
                            page.update(),
                        ),
                    ),
                    ft.TextField(
                        label="Ersetzen",
                        value=r["replace"].value,
                        expand=2,
                        on_change=lambda e, i=idx: (
                            setattr(rules_ui[i]["replace"], "value", e.control.value),
                            refresh_preview(),
                            page.update(),
                        ),
                    ),
                    ft.Checkbox(
                        label="Required",
                        value=r["required"],
                        on_change=lambda e, i=idx: (
                            rules_ui[i].__setitem__("required", e.control.value),
                            refresh_preview(),
                            page.update(),
                        ),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.ARROW_UPWARD,
                        on_click=lambda e, i=idx: move_rule(i, -1),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.ARROW_DOWNWARD,
                        on_click=lambda e, i=idx: move_rule(i, +1),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.DELETE,
                        icon_color=ft.Colors.RED_400,
                        on_click=lambda e, i=idx: remove_rule(i),
                    ),
                ],
            )
            rules_column.controls.append(row)

    def add_rule(e=None):
        rules_ui.append(
            {
                "search": ft.TextField(value=""),
                "replace": ft.TextField(value=""),
                "required": False,
            }
        )
        rebuild_rules_ui()
        refresh_preview()
        page.update()

    def remove_rule(index: int):
        if 0 <= index < len(rules_ui):
            rules_ui.pop(index)
            rebuild_rules_ui()
            refresh_preview()
            page.update()

    def move_rule(index: int, delta: int):
        new_index = index + delta
        if 0 <= new_index < len(rules_ui):
            rules_ui[index], rules_ui[new_index] = rules_ui[new_index], rules_ui[index]
            rebuild_rules_ui()
            refresh_preview()
            page.update()

    add_rule_button = ft.Button(
        "Regel hinzufügen", icon=ft.Icons.ADD, on_click=add_rule
    )

    # --- Vorschau ---------------------------------------------------------
    preview_list = ft.ListView(expand=True, spacing=2, padding=8)

    status_colors = {
        "OK": ft.Colors.GREEN_400,
        "OK (angepasst)": ft.Colors.CYAN_300,
        "Kollision": ft.Colors.RED_400,
        "Übersprungen": ft.Colors.GREY_400,
    }

    def refresh_preview(*_):
        """
        Baut den Inhalt der Vorschau neu auf. Macht KEIN eigenes update() –
        der Aufrufer ist dafür verantwortlich, page.update() aufzurufen.
        """
        preview_list.controls.clear()
        rules_data = [
            {
                "search": r["search"].value,
                "replace": r["replace"].value,
                "required": r["required"],
            }
            for r in rules_ui
        ]
        directory = state["directory"]
        if not directory:
            preview_list.controls.append(
                ft.Text("Kein Verzeichnis gewählt.", color=ft.Colors.GREY_500)
            )
            return

        try:
            items = build_preview(
                directory,
                state["mode"],
                state["include_subfolders"],
                rules_data,
                state["case_sensitive"],
                state["handle_collisions"],
                state["suffix"],
                state["leading_zeros"],
            )
        except Exception as ex:
            preview_list.controls.append(
                ft.Text(f"Fehler: {ex}", color=ft.Colors.RED_400)
            )
            return

        if not items:
            preview_list.controls.append(
                ft.Text("Keine passenden Einträge gefunden.", color=ft.Colors.GREY_500)
            )
            return

        # Gruppierung nach Elternverzeichnis (für Zwischenüberschriften)
        root = Path(directory)
        grouped: dict[str, list[dict]] = {}
        for it in items:
            grouped.setdefault(it["parent"], []).append(it)

        # Elternverzeichnisse sortiert nach relativem Pfad
        def rel_label(parent: str) -> str:
            try:
                rel = Path(parent).relative_to(root)
                s = str(rel)
                return str(root) if s == "." else f"{root} / {s}"
            except ValueError:
                return parent

        sorted_parents = sorted(grouped.keys(), key=lambda p: rel_label(p))

        for parent in sorted_parents:
            # Zwischenüberschrift
            preview_list.controls.append(
                ft.Container(
                    padding=ft.Padding.only(top=8, bottom=4),
                    content=ft.Text(
                        rel_label(parent),
                        weight=ft.FontWeight.BOLD,
                        size=13,
                        color=ft.Colors.BLUE_200,
                    ),
                )
            )
            # Kopfzeile pro Gruppe
            preview_list.controls.append(
                ft.Row(
                    controls=[
                        ft.Text("Alter Name", weight=ft.FontWeight.BOLD, expand=3),
                        ft.Text("Neuer Name", weight=ft.FontWeight.BOLD, expand=3),
                        ft.Text("Status", weight=ft.FontWeight.BOLD, width=130),
                    ],
                    spacing=8,
                )
            )
            for it in grouped[parent]:
                color = status_colors.get(it["status"], ft.Colors.WHITE)
                preview_list.controls.append(
                    ft.Row(
                        controls=[
                            ft.Text(
                                it["old"],
                                expand=3,
                                no_wrap=True,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            ft.Text(
                                it["new"],
                                expand=3,
                                no_wrap=True,
                                overflow=ft.TextOverflow.ELLIPSIS,
                                color=color if it["status"] != "OK" else None,
                            ),
                            ft.Text(it["status"], width=130, color=color),
                        ],
                        spacing=8,
                    )
                )
            preview_list.controls.append(ft.Divider(height=1))

    # --- Historie (Buttons-Liste statt Dropdown) -------------------------
    history_list = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO)

    def history_key(rules_data: list[dict]) -> str:
        return json.dumps(
            [
                {
                    "search": r["search"],
                    "replace": r["replace"],
                    "required": bool(r["required"]),
                }
                for r in rules_data
            ],
            sort_keys=True,
            ensure_ascii=False,
        )

    def refresh_history_ui():
        """Baut die Historie-Buttons neu auf. Kein eigenes update()."""
        history_list.controls.clear()
        if not history:
            history_list.controls.append(
                ft.Text(
                    "Noch keine Historie vorhanden.",
                    size=12,
                    color=ft.Colors.GREY_500,
                )
            )
            return
        # Einträge mit Label vorbereiten und absteigend nach Label sortieren
        labeled_entries = []
        for entry in history:
            n = len(entry.get("rules", []))
            ts = entry.get("timestamp", "")
            label = f"{ts}  ({n} Regel(n))"
            labeled_entries.append((label, entry))

        labeled_entries.sort(key=lambda pair: pair[0], reverse=True)

        for label, entry in labeled_entries:
            history_list.controls.append(
                ft.TextButton(
                    content=ft.Text(label, size=12),
                    on_click=lambda e, ent=entry: load_history_entry(ent),
                )
            )

        def load_history_entry(entry: dict):
            rules_ui.clear()
            for r in entry.get("rules", []):
                rules_ui.append(
                    {
                        "search": ft.TextField(value=r.get("search", "")),
                        "replace": ft.TextField(value=r.get("replace", "")),
                        "required": bool(r.get("required", False)),
                    }
                )
            rebuild_rules_ui()
            refresh_preview()
            page.update()

    # --- Nachrichten-Dialog ----------------------------------------------
    async def show_message(msg: str, error: bool = False):
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Fehler" if error else "Info"),
            content=ft.Text(msg, selectable=True),
            actions=[
                ft.TextButton("OK", on_click=lambda e: page.pop_dialog())
            ],
        )
        page.show_dialog(dlg)

    # --- Anwenden ---------------------------------------------------------
    def _collect_rules() -> list[dict]:
        return [
            {
                "search": r["search"].value,
                "replace": r["replace"].value,
                "required": r["required"],
            }
            for r in rules_ui
        ]

    def _apply_preview_items(items: list[dict], errors: list[str]):
        """Führt die im Preview bereits aufgelösten Umbenennungen aus."""
        for item in items:
            if item["status"] not in ("OK", "OK (angepasst)"):
                continue
            src = Path(item["path"])
            desired = item["new"]
            try:
                dst = src.with_name(desired)
                if str(dst) == str(src):
                    continue
                if dst.exists():
                    # Sollte durch die Auflösung nicht mehr vorkommen,
                    # aber als Sicherheitsnetz: überspringen.
                    errors.append(
                        f"{src.name}: Ziel existiert bereits ({desired})"
                    )
                    continue
                os.rename(src, dst)
            except Exception as ex:
                errors.append(f"{src.name}: {ex}")

    def _save_history_entry(rules_data: list[dict]):
        if not rules_data or not any(r["search"] for r in rules_data):
            return
        key = history_key(rules_data)
        existing_idx = None
        for i, entry in enumerate(history):
            if history_key(entry.get("rules", [])) == key:
                existing_idx = i
                break
        entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "case_sensitive": state["case_sensitive"],
            "rules": rules_data,
        }
        if existing_idx is not None:
            history.pop(existing_idx)
        history.append(entry)
        while len(history) > 10:
            history.pop(0)
        save_history(history)
        refresh_history_ui()

    async def apply_renames(e):
        rules_data = _collect_rules()
        if not state["directory"]:
            await show_message("Bitte zuerst ein Verzeichnis wählen.")
            return
        if not rules_data or not any(r["search"] for r in rules_data):
            await show_message(
                "Bitte mindestens eine Regel mit Suchbegriff definieren."
            )
            return

        preview = build_preview(
            state["directory"],
            state["mode"],
            state["include_subfolders"],
            rules_data,
            state["case_sensitive"],
            state["handle_collisions"],
            state["suffix"],
            state["leading_zeros"],
        )
        collisions = [p for p in preview if p["status"] == "Kollision"]

        if collisions and not state["handle_collisions"]:
            await show_message(
                f"{len(collisions)} Kollision(en) gefunden. "
                "Aktiviere 'Kollisionen behandeln' oder passe die Regeln an, "
                "damit alle Zielnamen eindeutig sind.",
                error=True,
            )
            return

        to_rename = [
            p for p in preview if p["status"] in ("OK", "OK (angepasst)")
        ]
        if not to_rename:
            await show_message("Keine Umbenennungen nötig.")
            return

        errors: list[str] = []
        try:
            _apply_preview_items(preview, errors)
        except Exception as ex:
            errors.append(str(ex))

        _save_history_entry(rules_data)

        refresh_preview()
        page.update()

        if errors:
            await show_message(
                "Umbenennung mit Fehlern:\n" + "\n".join(errors[:10]), error=True
            )
        else:
            await show_message(f"{len(to_rename)} Eintrag/Einträge umbenannt.")

    apply_button = ft.Button(
        "Anwenden",
        icon=ft.Icons.PLAY_ARROW,
        on_click=apply_renames,
        style=ft.ButtonStyle(color=ft.Colors.WHITE, bgcolor=ft.Colors.GREEN_700),
    )

    # --- Layout -----------------------------------------------------------
    history_panel = ft.Container(
        width=360,
        content=ft.Column(
            spacing=2,
            controls=[
                ft.Text("Historie", weight=ft.FontWeight.BOLD, size=13),
                ft.Container(
                    content=history_list,
                    height=90,
                ),
            ],
        ),
    )

    top_bar = ft.Column(
        spacing=8,
        controls=[
            ft.Row(spacing=8, controls=[directory_field, directory_button]),
            ft.Row(
                spacing=16,
                wrap=False,
                vertical_alignment=ft.CrossAxisAlignment.START,
                controls=[
                    mode_segmented,
                    subfolders_checkbox,
                    case_checkbox,
                    ft.Container(expand=True),   # Spacer: drückt Historie nach rechts
                    history_panel,
                ],
            ),
        ],
    )

    left_panel = ft.Container(
        expand=1,
        padding=8,
        content=ft.Column(
            expand=True,
            controls=[
                ft.Text("Regeln", weight=ft.FontWeight.BOLD, size=16),
                ft.Container(content=rules_column, expand=True),
                ft.Row(controls=[add_rule_button]),
            ],
        ),
    )

    right_panel = ft.Container(
        expand=1,
        padding=8,
        content=ft.Column(
            expand=True,
            controls=[
                ft.Text("Vorschau", weight=ft.FontWeight.BOLD, size=16),
                ft.Container(content=preview_list, expand=True),
            ],
        ),
    )

    middle = ft.Row(
        expand=True,
        controls=[left_panel, ft.VerticalDivider(width=1), right_panel],
    )

    history_panel = ft.Container(
        width=420,
        content=ft.Column(
            spacing=4,
            controls=[
                ft.Text("Historie", weight=ft.FontWeight.BOLD, size=13),
                ft.Container(
                    content=history_list,
                    height=130,
                ),
            ],
        ),
    )

    bottom_bar = ft.Column(
        spacing=8,
        controls=[
            ft.Row(
                spacing=16,
                wrap=True,
                controls=[collision_checkbox, collision_options_container],
            ),
            ft.Row(spacing=16, wrap=True, controls=[apply_button]),
        ],
    )

    page.add(
        ft.Column(
            expand=True,
            spacing=12,
            controls=[top_bar, ft.Divider(), middle, ft.Divider(), bottom_bar],
        )
    )

    add_rule()
    refresh_history_ui()
    refresh_preview()
    page.update()


def _parse_int(value: str) -> int:
    try:
        return max(0, int(value))
    except (ValueError, TypeError):
        return 0


if __name__ == "__main__":
    ft.run(main)