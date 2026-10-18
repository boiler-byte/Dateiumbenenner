"""
Datei- und Ordnerumbenennung - Flet Desktop App
Python 3.10+, Flet 1.0.4

Start:      python main.py
Build (Windows): pip install flet pyinstaller && flet pack main.py --name DateiUmbenenner
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

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
    """Lädt config.json oder legt Defaults an."""
    _ensure_config_dir()
    default = {"last_directory": "", "case_sensitive": True}
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
    """Lädt die Historie (max. 10)."""
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
def apply_rules(
    name: str, rules: list[dict], case_sensitive: bool
) -> str:
    """Wendet die Regel-Liste sequenziell auf `name` an."""
    result = name
    for rule in rules:
        search = rule.get("search", "")
        replace = rule.get("replace", "")
        required = bool(rule.get("required", False))
        if not search:
            continue
        flags = 0 if case_sensitive else re.IGNORECASE
        pattern = re.compile(re.escape(search), flags)
        if required:
            # Nur anwenden, wenn der aktuelle Name den Suchbegriff enthält
            if pattern.search(result):
                result = pattern.sub(replace, result)
        else:
            result = pattern.sub(replace, result)
    return result


def sanitize_filename(name: str) -> str:
    """Entfernt für Windows ungültige Zeichen aus einem Dateinamen."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    return name.strip().rstrip(".") or "_"


def scan_targets(
    directory: str, mode: str, include_subfolders: bool
) -> list[Path]:
    """Sammelt Dateien (mode='file') oder Ordner (mode='dir')."""
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
                # Ordner rekursiv: alle Unterordner außer dem Root selbst
                for d in dirs:
                    results.append(cur / d)
    else:
        for entry in sorted(root.iterdir()):
            if mode == "file" and entry.is_file():
                results.append(entry)
            elif mode == "dir" and entry.is_dir():
                results.append(entry)
    return results


def build_preview(
    directory: str,
    mode: str,
    include_subfolders: bool,
    rules: list[dict],
    case_sensitive: bool,
) -> list[dict]:
    """
    Erzeugt die Vorschau: Liste von {path, old, new, status}.
    Status: "OK" | "Kollision" | "Übersprungen".
    """
    targets = scan_targets(directory, mode, include_subfolders)
    preview: list[dict] = []
    for p in targets:
        old_name = p.name
        new_name = apply_rules(old_name, rules, case_sensitive)
        if mode == "file":
            # Extension beim Filenamen beibehalten, wenn keine Regel sie ändert
            new_name = sanitize_filename(new_name)
        else:
            new_name = sanitize_filename(new_name)

        if new_name == old_name:
            status = "Übersprungen"
        elif not new_name:
            status = "Übersprungen"
        else:
            status = "OK"
        preview.append(
            {
                "path": str(p),
                "old": old_name,
                "new": new_name,
                "status": status,
            }
        )

    # Kollisionen im selben Elternverzeichnis erkennen
    groups: dict[tuple[str, str], list[dict]] = {}
    for item in preview:
        if item["status"] == "Übersprungen":
            continue
        parent = str(Path(item["path"]).parent)
        groups.setdefault((parent, item["new"].lower()), []).append(item)
    for items in groups.values():
        if len(items) > 1:
            for it in items:
                it["status"] = "Kollision"
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

    # ------------------------------------------------------------------
    # Zustand
    # ------------------------------------------------------------------
    config = load_config()
    history: list[dict] = load_history()

    state: dict = {
        "mode": "file",  # "file" | "dir"
        "directory": config.get("last_directory", ""),
        "include_subfolders": False,
        "case_sensitive": bool(config.get("case_sensitive", True)),
        "suffix": "_",
        "leading_zeros": 0,
    }

    # ------------------------------------------------------------------
    # Regel-Liste (UI-Repräsentation)
    # ------------------------------------------------------------------
    rules_ui: list[dict] = []  # [{"search": tf, "replace": tf, "required": bool}]

    # ------------------------------------------------------------------
    # UI-Elemente
    # ------------------------------------------------------------------
    directory_field = ft.TextField(
        label="Verzeichnis",
        value=state["directory"],
        read_only=True,
        expand=True,
    )

    file_picker = ft.FilePicker()
    page.overlay.append(file_picker)

    def on_pick_directory(e: ft.FilePickerResultEvent):
        if e.path:
            state["directory"] = e.path
            directory_field.value = e.path
            config["last_directory"] = e.path
            save_config(config)
            refresh_preview()

    def open_directory_dialog(e):
        file_picker.get_directory_path(
            dialog_title="Verzeichnis wählen",
            initial_directory=state["directory"] or None,
        )

    file_picker.on_result = on_pick_directory

    directory_button = ft.ElevatedButton(
        "Verzeichnis wählen", icon=ft.Icons.FOLDER_OPEN, on_click=open_directory_dialog
    )

    # Modus-Auswahl
    mode_segmented = ft.SegmentedButton(
        selected={"file"},
        allow_multiple_selection=False,
        segments=[
            ft.Segment(value="file", label=ft.Text("Dateien umbenennen")),
            ft.Segment(value="dir", label=ft.Text("Ordner umbenennen")),
        ],
    )

    def on_mode_change(e):
        state["mode"] = next(iter(mode_segmented.selected))
        refresh_preview()

    mode_segmented.on_change = on_mode_change

    # Unterordner-Checkbox
    subfolders_checkbox = ft.Checkbox(
        label="Unterordner einbeziehen",
        value=state["include_subfolders"],
        on_change=lambda e: (
            state.__setitem__("include_subfolders", e.control.value),
            refresh_preview(),
        ),
    )

    # Case-Sensitivity
    def on_case_change(e):
        state["case_sensitive"] = e.control.value
        config["case_sensitive"] = e.control.value
        save_config(config)
        refresh_preview()

    case_checkbox = ft.Checkbox(
        label="Groß-/Kleinschreibung beachten",
        value=state["case_sensitive"],
        on_change=on_case_change,
    )

    # Kollisionsoptionen
    suffix_field = ft.TextField(
        label="Suffix",
        value=state["suffix"],
        width=100,
        on_change=lambda e: state.__setitem__("suffix", e.control.value or "_"),
    )
    leading_zeros_field = ft.TextField(
        label="Führende Nullen",
        value=str(state["leading_zeros"]),
        width=140,
        keyboard_type=ft.KeyboardType.NUMBER,
        on_change=lambda e: state.__setitem__(
            "leading_zeros", _parse_int(e.control.value)
        ),
    )

    # Regel-Container
    rules_column = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)

    def rebuild_rules_ui():
        """Zeichnet die Regel-Liste neu."""
        rules_column.controls.clear()
        for idx, r in enumerate(rules_ui):
            row = ft.Row(
                spacing=6,
                controls=[
                    ft.TextField(
                        label="Suchen",
                        value=r["search"].value,
                        expand=2,
                        on_change=lambda e, i=idx: (
                            rules_ui[i]["search"].__setattr__("value", e.control.value),
                            refresh_preview(),
                        ),
                    ),
                    ft.TextField(
                        label="Ersetzen",
                        value=r["replace"].value,
                        expand=2,
                        on_change=lambda e, i=idx: (
                            rules_ui[i]["replace"].__setattr__("value", e.control.value),
                            refresh_preview(),
                        ),
                    ),
                    ft.Checkbox(
                        label="Required",
                        value=r["required"],
                        on_change=lambda e, i=idx: (
                            rules_ui[i].__setitem__("required", e.control.value),
                            refresh_preview(),
                        ),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.ARROW_UPWARD,
                        tooltip="Nach oben",
                        on_click=lambda e, i=idx: move_rule(i, -1),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.ARROW_DOWNWARD,
                        tooltip="Nach unten",
                        on_click=lambda e, i=idx: move_rule(i, +1),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.DELETE,
                        tooltip="Regel entfernen",
                        icon_color=ft.Colors.RED_400,
                        on_click=lambda e, i=idx: remove_rule(i),
                    ),
                ],
            )
            rules_column.controls.append(row)
        page.update()

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

    def remove_rule(index: int):
        if 0 <= index < len(rules_ui):
            rules_ui.pop(index)
            rebuild_rules_ui()
            refresh_preview()

    def move_rule(index: int, delta: int):
        new_index = index + delta
        if 0 <= new_index < len(rules_ui):
            rules_ui[index], rules_ui[new_index] = rules_ui[new_index], rules_ui[index]
            rebuild_rules_ui()
            refresh_preview()

    add_rule_button = ft.ElevatedButton(
        "Regel hinzufügen", icon=ft.Icons.ADD, on_click=add_rule
    )

    # ------------------------------------------------------------------
    # Vorschau
    # ------------------------------------------------------------------
    preview_list = ft.ListView(expand=True, spacing=2, padding=8)

    status_colors = {
        "OK": ft.Colors.GREEN_400,
        "Kollision": ft.Colors.RED_400,
        "Übersprungen": ft.Colors.GREY_400,
    }

    def refresh_preview(*_):
        """Berechnet die Vorschau neu und aktualisiert die UI."""
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
            page.update()
            return

        try:
            items = build_preview(
                directory,
                state["mode"],
                state["include_subfolders"],
                rules_data,
                state["case_sensitive"],
            )
        except Exception as ex:
            preview_list.controls.append(ft.Text(f"Fehler: {ex}", color=ft.Colors.RED_400))
            page.update()
            return

        if not items:
            preview_list.controls.append(
                ft.Text("Keine passenden Einträge gefunden.", color=ft.Colors.GREY_500)
            )
            page.update()
            return

        # Header
        preview_list.controls.append(
            ft.Row(
                controls=[
                    ft.Text("Alter Name", weight=ft.FontWeight.BOLD, expand=3),
                    ft.Text("Neuer Name", weight=ft.FontWeight.BOLD, expand=3),
                    ft.Text("Status", weight=ft.FontWeight.BOLD, width=110),
                ],
                spacing=8,
            )
        )

        for it in items:
            color = status_colors.get(it["status"], ft.Colors.WHITE)
            preview_list.controls.append(
                ft.Row(
                    controls=[
                        ft.Text(it["old"], expand=3, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Text(
                            it["new"],
                            expand=3,
                            no_wrap=True,
                            overflow=ft.TextOverflow.ELLIPSIS,
                            color=color if it["status"] != "OK" else None,
                        ),
                        ft.Text(it["status"], width=110, color=color),
                    ],
                    spacing=8,
                )
            )
        page.update()

    # ------------------------------------------------------------------
    # Historie
    # ------------------------------------------------------------------
    history_dropdown = ft.Dropdown(
        label="Historie",
        width=420,
        options=[],
        on_change=lambda e: load_history_entry(int(e.control.value)),
    )

    def history_key(rules_data: list[dict]) -> str:
        """Eindeutiger Schlüssel über search/replace/required."""
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

    def refresh_history_dropdown():
        history_dropdown.options.clear()
        # Neueste zuerst
        for i, entry in enumerate(reversed(history)):
            n = len(entry.get("rules", []))
            label = f"{entry.get('timestamp', '')} ({n}x)"
            history_dropdown.options.append(ft.dropdown.Option(key=str(i), text=label))
        page.update()

    def load_history_entry(idx: int):
        # idx bezieht sich auf umgekehrte Liste
        if idx < 0 or idx >= len(history):
            return
        real_index = len(history) - 1 - idx
        entry = history[real_index]
        current_has_rules = any(r["search"].value for r in rules_ui)

        def do_load(e=None):
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
            page.close(dlg)

        dlg = None
        if current_has_rules:
            dlg = ft.AlertDialog(
                title=ft.Text("Historie laden"),
                content=ft.Text(
                    "Die aktuelle Regel-Liste wird überschrieben. Fortfahren?"
                ),
                actions=[
                    ft.TextButton("Abbrechen", on_click=lambda e: page.close(dlg)),
                    ft.TextButton("Laden", on_click=do_load),
                ],
            )
            page.open(dlg)
        else:
            do_load()

    # ------------------------------------------------------------------
    # Anwenden
    # ------------------------------------------------------------------
    def _collect_rules() -> list[dict]:
        return [
            {
                "search": r["search"].value,
                "replace": r["replace"].value,
                "required": r["required"],
            }
            for r in rules_ui
        ]

    def apply_renames(e):
        rules_data = _collect_rules()
        if not state["directory"]:
            show_message("Bitte zuerst ein Verzeichnis wählen.")
            return
        if not rules_data or not any(r["search"] for r in rules_data):
            show_message("Bitte mindestens eine Regel mit Suchbegriff definieren.")
            return

        preview = build_preview(
            state["directory"],
            state["mode"],
            state["include_subfolders"],
            rules_data,
            state["case_sensitive"],
        )
        collisions = [p for p in preview if p["status"] == "Kollision"]
        if collisions:
            show_message(
                f"{len(collisions)} Kollision(en) gefunden. "
                "Bitte Regeln anpassen, damit alle Zielnamen eindeutig sind.",
                error=True,
            )
            return

        to_rename = [p for p in preview if p["status"] == "OK"]
        if not to_rename:
            show_message("Keine Umbenennungen nötig.")
            return

        # Umbenennen
        errors: list[str] = []
        try:
            _apply_with_collision_resolution(
                preview, state["suffix"], state["leading_zeros"], errors
            )
        except Exception as ex:
            errors.append(str(ex))

        # Historie aktualisieren
        _save_history_entry(rules_data)

        if errors:
            show_message("Umbenennung mit Fehlern:\n" + "\n".join(errors[:10]), error=True)
        else:
            show_message(f"{len(to_rename)} Eintrag/Einträge umbenannt.")

        refresh_preview()

    def _apply_with_collision_resolution(
        preview: list[dict],
        suffix: str,
        leading_zeros: int,
        errors: list[str],
    ):
        """
        Führt die Umbenennung aus. Da Kollisionen vorher ausgeschlossen wurden,
        werden Suffix und führende Nullen nur genutzt, falls beim Anwenden
        doch eine Kollision auftritt (z.B. externer Eingriff) - dann wird
        fortlaufend nummeriert.
        """
        # Zuerst alle gewünschten Zielnamen sammeln, um Kollisionen zu erkennen
        target_map: dict[str, int] = {}
        for item in preview:
            if item["status"] != "OK":
                continue
            parent = str(Path(item["path"]).parent)
            target_map[(parent, item["new"].lower())] = target_map.get(
                (parent, item["new"].lower()), 0
            ) + 1

        counter_per_parent: dict[tuple[str, str], int] = {}
        for item in preview:
            if item["status"] != "OK":
                continue
            src = Path(item["path"])
            parent = str(src.parent)
            desired = item["new"]
            try:
                dst = src.with_name(desired)
                # Kollision mit existierender Datei?
                if dst.exists() and str(dst) != str(src):
                    # Nummer anhängen
                    stem = Path(desired).stem
                    ext = Path(desired).suffix if state["mode"] == "file" else ""
                    key = (parent, desired.lower())
                    n = counter_per_parent.get(key, 0) + 1
                    counter_per_parent[key] = n
                    num = str(n).zfill(max(0, leading_zeros))
                    candidate = f"{stem}{suffix}{num}{ext}"
                    dst = src.with_name(candidate)
                    # Falls weiterhin existierend: hochzählen
                    while dst.exists() and str(dst) != str(src):
                        n += 1
                        counter_per_parent[key] = n
                        num = str(n).zfill(max(0, leading_zeros))
                        candidate = f"{stem}{suffix}{num}{ext}"
                        dst = src.with_name(candidate)
                os.rename(src, dst)
            except Exception as ex:
                errors.append(f"{src.name}: {ex}")

    def _save_history_entry(rules_data: list[dict]):
        if not rules_data or not any(r["search"] for r in rules_data):
            return
        key = history_key(rules_data)
        # existierenden Eintrag finden
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
            # Eintrag aktualisieren (nach vorne schieben)
            history.pop(existing_idx)
        history.append(entry)
        # Auf 10 begrenzen
        while len(history) > 10:
            history.pop(0)
        save_history(history)
        refresh_history_dropdown()

    # ------------------------------------------------------------------
    # Nachrichten-Dialog
    # ------------------------------------------------------------------
    def show_message(msg: str, error: bool = False):
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Fehler" if error else "Info"),
            content=ft.Text(msg, selectable=True),
            actions=[ft.TextButton("OK", on_click=lambda e: page.close(dlg))],
        )
        page.open(dlg)

    # ------------------------------------------------------------------
    # Buttons unterer Bereich
    # ------------------------------------------------------------------
    apply_button = ft.ElevatedButton(
        "Anwenden",
        icon=ft.Icons.PLAY_ARROW,
        on_click=apply_renames,
        style=ft.ButtonStyle(color=ft.Colors.WHITE, bgcolor=ft.Colors.GREEN_700),
    )

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    top_bar = ft.Column(
        spacing=8,
        controls=[
            ft.Row(
                spacing=8,
                controls=[directory_field, directory_button],
            ),
            ft.Row(
                spacing=24,
                wrap=True,
                controls=[
                    ft.Container(
                        content=mode_segmented,
                        padding=ft.padding.symmetric(vertical=4),
                    ),
                    subfolders_checkbox,
                    case_checkbox,
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

    bottom_bar = ft.Column(
        spacing=8,
        controls=[
            ft.Row(
                spacing=16,
                wrap=True,
                controls=[
                    ft.Text("Kollisionsauflösung – Suffix:", size=12),
                    suffix_field,
                    leading_zeros_field,
                ],
            ),
            ft.Row(
                spacing=16,
                wrap=True,
                controls=[apply_button, history_dropdown],
            ),
        ],
    )

    page.add(
        ft.Column(
            expand=True,
            spacing=12,
            controls=[
                top_bar,
                ft.Divider(),
                middle,
                ft.Divider(),
                bottom_bar,
            ],
        )
    )

    # Initiale Regel: eine leere Regel anlegen
    add_rule()
    refresh_history_dropdown()
    refresh_preview()


def _parse_int(value: str) -> int:
    try:
        v = int(value)
        return max(0, v)
    except (ValueError, TypeError):
        return 0


if __name__ == "__main__":
    ft.app(target=main)