#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

import sys
import os
import shutil
import tempfile
import subprocess
from datetime import datetime
from pathlib import Path
import configparser
import ctypes
import ctypes.util


def _ensure_libgthread_alias():
    """Make PySide6 importable on systems without libgthread-2.0.so.0.

    Recent glib releases folded libgthread into libglib and no longer ship
    the old soname, but PySide6 still links against it. Create an alias and
    re-exec once with LD_LIBRARY_PATH pointing at it: the dynamic linker
    caches that variable when the process starts, so setting it in-process
    would be too late.
    """
    if os.environ.get("_SMB_UI_GTHREAD_COMPAT") == "1":
        return

    try:
        ctypes.CDLL("libgthread-2.0.so.0")
        return
    except OSError:
        pass

    glib = ctypes.util.find_library("glib-2.0") or "libglib-2.0.so.0"
    glib_path = glib
    for directory in (
        "/usr/lib64", "/usr/lib", "/lib64", "/lib",
        "/usr/local/lib64", "/usr/local/lib",
    ):
        candidate = Path(directory) / glib
        if candidate.exists():
            glib_path = str(candidate.resolve())
            break
    else:
        # Fallback: let the loader locate it, then read the real path back.
        try:
            ctypes.CDLL(glib)
            with open("/proc/self/maps", encoding="utf-8") as maps:
                for line in maps:
                    if glib in line:
                        glib_path = line.split()[-1]
                        break
        except OSError:
            pass

    compat_dir = Path(tempfile.gettempdir()) / (
        f"smb-ui-gthread-{os.getuid()}"
    )
    compat_dir.mkdir(parents=True, exist_ok=True)
    alias = compat_dir / "libgthread-2.0.so.0"

    # The loader will not chase a relative symlink for a library soname,
    # so the alias must point at an absolute path.
    if alias.exists() or alias.is_symlink():
        alias.unlink()
    alias.symlink_to(glib_path)

    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = os.pathsep.join(
        p for p in (str(compat_dir), env.get("LD_LIBRARY_PATH", "")) if p
    )
    env["_SMB_UI_GTHREAD_COMPAT"] = "1"
    os.execve(sys.executable, [sys.executable, *sys.argv], env)


def _restore_sudo_session_env():
    """Recover the desktop session variables that sudo/pkexec strip.

    Running `sudo python3 smb-ui.py` leaves the process as root but without
    DISPLAY/XAUTHORITY, so Qt aborts before showing any window. The original
    user's environment is still available in the parent process' /proc
    entry; copy the session variables from there.
    """
    if os.geteuid() != 0:
        return

    original_uid = (
        os.environ.get("SUDO_UID") or os.environ.get("PKEXEC_UID")
    )
    if not original_uid:
        return

    keys = (
        "DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS", "XDG_SESSION_TYPE", "QT_QPA_PLATFORM",
        "LANG", "LANGUAGE", "LC_ALL", "HOME", "USER", "LOGNAME",
    )

    pid = os.getppid()
    for _ in range(10):
        try:
            with open(f"/proc/{pid}/status", encoding="utf-8") as status:
                lines = status.readlines()

            uid = None
            parent = None
            for line in lines:
                if line.startswith("Uid:"):
                    uid = int(line.split()[1])
                elif line.startswith("PPid:"):
                    parent = int(line.split()[1])

            if uid is not None and uid == int(original_uid):
                with open(f"/proc/{pid}/environ", "rb") as environ:
                    data = environ.read()

                for item in data.split(b"\0"):
                    if b"=" not in item:
                        continue
                    key, value = item.split(b"=", 1)
                    key = key.decode(errors="replace")
                    if key in keys and not os.environ.get(key):
                        os.environ[key] = value.decode(errors="replace")
                return

            if not parent or parent <= 1:
                return
            pid = parent

        except (OSError, ValueError):
            return


_restore_sudo_session_env()
_ensure_libgthread_alias()


from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QListWidget, QTableWidget,
    QTableWidgetItem, QPushButton, QLabel, QLineEdit,
    QMessageBox, QInputDialog, QHeaderView, QSplitter,
    QAbstractItemView, QStatusBar,
    QDialog, QDialogButtonBox, QFormLayout, QComboBox,
    QCheckBox, QFileDialog
)
from PySide6.QtCore import Qt  # noqa: E402


def _require_root():
    """Ask for the root password and relaunch the app with root rights."""
    if os.geteuid() == 0:
        return

    password, ok = QInputDialog.getText(
        None,
        "Administratorrechte erforderlich",
        "Zum Ändern der Samba-Konfiguration werden Root-Rechte "
        "benötigt.\nBitte das Root-Passwort eingeben:",
        QLineEdit.EchoMode.Password,
    )
    if not ok:
        sys.exit(1)
    if not password:
        QMessageBox.critical(
            None, "Fehler", "Es wurde kein Passwort eingegeben."
        )
        sys.exit(1)

    # Pass the session environment through sudo's env_reset so the
    # elevated process can still connect to the display and Qt platform.
    env_keys = (
        "DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS", "XDG_SESSION_TYPE", "QT_QPA_PLATFORM",
        "LANG", "LANGUAGE", "LC_ALL",
    )
    env_args = [
        f"{key}={os.environ[key]}"
        for key in env_keys
        if os.environ.get(key)
    ]
    env_bin = shutil.which("env") or "/usr/bin/env"
    command = [
        "sudo", "-S", "-p", "", env_bin, *env_args,
        sys.executable, *sys.argv,
    ]

    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        QMessageBox.critical(
            None, "Fehler",
            f"sudo konnte nicht gestartet werden:\n{exc}"
        )
        sys.exit(1)

    try:
        try:
            process.stdin.write((password + "\n").encode("utf-8"))
            process.stdin.flush()
        except OSError:
            # sudo may have exited immediately (e.g. missing sudoers
            # entry); the error is reported below from its stderr.
            pass
    finally:
        process.stdin.close()
    del password

    stderr = process.stderr.read().decode("utf-8", errors="replace")
    returncode = process.wait()
    if returncode != 0:
        details = stderr.strip() or f"Exit-Code {returncode}"
        QMessageBox.critical(
            None,
            "Root-Rechte fehlgeschlagen",
            "Das Programm konnte nicht mit Root-Rechten gestartet "
            "werden.\n\n" + details,
        )
        sys.exit(1)
    sys.exit(0)


CONFIG_FILE = "/etc/samba/smb.conf"

# Parameters owned by the guided share dialog. Everything else in a share
# is kept untouched when the dialog is used to edit an existing share.
GUIDED_SHARE_OPTIONS = (
    "comment",
    "path",
    "read only",
    "valid users",
    "force user",
    "force group",
    "guest ok",
    "browseable",
    "create mask",
    "directory mask",
)


def _is_yes(value):
    return str(value or "").strip().lower() in ("yes", "true", "1")


class ShareDialog(QDialog):
    """Guided dialog for the most common Samba share parameters."""

    def __init__(self, parent=None, section=None, options=None):
        super().__init__(parent)
        self.setWindowTitle(
            "Freigabe bearbeiten" if section else "Neue Freigabe"
        )
        self.resize(540, 520)
        options = options or {}

        form = QFormLayout(self)

        self.name_edit = QLineEdit(section or "")
        self.name_edit.setPlaceholderText("z. B. freigabe")
        form.addRow("Name:", self.name_edit)

        self.comment_edit = QLineEdit(options.get("comment", ""))
        form.addRow("Kommentar:", self.comment_edit)

        path_layout = QHBoxLayout()
        self.path_edit = QLineEdit(options.get("path", ""))
        self.path_edit.setPlaceholderText("/srv/freigabe")
        browse = QPushButton("Durchsuchen…")
        browse.clicked.connect(self.choose_path)
        path_layout.addWidget(self.path_edit)
        path_layout.addWidget(browse)
        form.addRow("Pfad:", path_layout)

        self.access_combo = QComboBox()
        self.access_combo.addItem("Lesen und Schreiben", "no")
        self.access_combo.addItem("Nur Lesen", "yes")
        read_only = _is_yes(options.get("read only", "no"))
        index = self.access_combo.findData("yes" if read_only else "no")
        self.access_combo.setCurrentIndex(max(index, 0))
        form.addRow("Zugriff:", self.access_combo)

        self.users_edit = QLineEdit(options.get("valid users", ""))
        self.users_edit.setPlaceholderText("z. B. alice,bob")
        form.addRow("Berechtigte Benutzer:", self.users_edit)

        self.force_user_edit = QLineEdit(options.get("force user", ""))
        form.addRow("force user:", self.force_user_edit)

        self.force_group_edit = QLineEdit(options.get("force group", ""))
        form.addRow("force group:", self.force_group_edit)

        self.guest_check = QCheckBox("Gastzugriff erlauben")
        self.guest_check.setChecked(_is_yes(options.get("guest ok", "no")))
        form.addRow("Gäste:", self.guest_check)

        self.browse_check = QCheckBox("Im Netzwerk sichtbar")
        self.browse_check.setChecked(
            _is_yes(options.get("browseable", "yes"))
        )
        form.addRow("Sichtbarkeit:", self.browse_check)

        mask_layout = QHBoxLayout()
        self.create_mask_edit = QLineEdit(
            str(options.get("create mask") or "0775")
        )
        self.dir_mask_edit = QLineEdit(
            str(options.get("directory mask") or "0775")
        )
        mask_layout.addWidget(QLabel("Dateien"))
        mask_layout.addWidget(self.create_mask_edit)
        mask_layout.addWidget(QLabel("Ordner"))
        mask_layout.addWidget(self.dir_mask_edit)
        form.addRow("Rechte-Masken:", mask_layout)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    @property
    def name(self):
        return self.name_edit.text().strip()

    def choose_path(self):
        directory = QFileDialog.getExistingDirectory(
            self, "Verzeichnis wählen", self.path_edit.text() or "/"
        )
        if directory:
            self.path_edit.setText(directory)

    def options(self):
        result = {
            "path": self.path_edit.text().strip(),
            "read only": self.access_combo.currentData(),
            "guest ok": "yes" if self.guest_check.isChecked() else "no",
            "browseable": "yes" if self.browse_check.isChecked() else "no",
            "create mask": self.create_mask_edit.text().strip() or "0775",
            "directory mask": self.dir_mask_edit.text().strip() or "0775",
        }
        for widget, key in (
            (self.comment_edit, "comment"),
            (self.users_edit, "valid users"),
            (self.force_user_edit, "force user"),
            (self.force_group_edit, "force group"),
        ):
            value = widget.text().strip()
            if value:
                result[key] = value
        return result

    def accept(self):
        name = self.name
        if not name:
            QMessageBox.warning(
                self, "Eingabe fehlt",
                "Bitte einen Namen für die Freigabe angeben."
            )
            return
        if name.lower() == "global" or any(
            char in name for char in "[]=#;\\/\r\n"
        ):
            QMessageBox.warning(
                self, "Ungültiger Name",
                "Der Freigabename enthält ungültige Zeichen oder ist "
                "'global'."
            )
            return
        if not self.path_edit.text().strip():
            QMessageBox.warning(
                self, "Eingabe fehlt",
                "Bitte einen Pfad für die Freigabe angeben."
            )
            return
        super().accept()


class SambaConfig:
    def __init__(self, filename):
        self.filename = filename
        self.config = configparser.ConfigParser(
            interpolation=None,
            strict=False,
            allow_no_value=True
        )
        self.config.optionxform = str
        self.load()

    def load(self):
        self.config.clear()
        self.config.optionxform = str

        if os.path.exists(self.filename):
            self.config.read(self.filename, encoding="utf-8")

    def sections(self):
        return ["global"] + [
            s for s in self.config.sections()
            if s.lower() != "global"
        ]

    def get_options(self, section):
        if not self.config.has_section(section):
            return {}

        return dict(self.config.items(section))

    def add_section(self, name):
        if not name.strip():
            raise ValueError("Der Name darf nicht leer sein.")

        if name.lower() == "global":
            raise ValueError("Global existiert bereits.")

        if self.config.has_section(name):
            raise ValueError("Diese Freigabe existiert bereits.")

        self.config.add_section(name)

    def delete_section(self, name):
        if name.lower() == "global":
            raise ValueError("Global kann nicht gelöscht werden.")

        self.config.remove_section(name)

    def set_option(self, section, key, value):
        if not key.strip():
            raise ValueError("Der Parametername darf nicht leer sein.")

        if "\n" in key or "=" in key or "[" in key:
            raise ValueError("Ungültiger Parametername.")

        if not self.config.has_section(section):
            raise ValueError("Abschnitt nicht gefunden.")

        self.config.set(section, key, value if value else None)

    def delete_option(self, section, key):
        self.config.remove_option(section, key)

    def save(self):
        filename = Path(self.filename)
        filename.parent.mkdir(parents=True, exist_ok=True)

        # Temporäre Datei im gleichen Verzeichnis
        fd, temp_name = tempfile.mkstemp(
            prefix=".smb.conf.",
            dir=str(filename.parent)
        )

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                self.config.write(f)

            # Vorhandene Dateirechte übernehmen
            if filename.exists():
                shutil.copystat(filename, temp_name)

            # Samba-Konfiguration validieren
            result = subprocess.run(
                ["testparm", "-s", temp_name],
                capture_output=True,
                text=True
            )

            if result.returncode != 0:
                raise RuntimeError(
                    result.stderr or result.stdout
                )

            # Backup erstellen
            if filename.exists():
                timestamp = datetime.now().strftime(
                    "%Y%m%d_%H%M%S"
                )
                backup = str(filename) + "." + timestamp + ".bak"
                shutil.copy2(filename, backup)

            # Atomar speichern
            os.replace(temp_name, filename)

        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)


class SambaManager(QMainWindow):

    def __init__(self):
        super().__init__()

        self.setWindowTitle("Samba Configuration Manager")
        self.resize(1100, 700)

        self.samba = SambaConfig(CONFIG_FILE)
        self.current_section = None

        self.init_ui()
        self.load_sections()

    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)

        # Kopfbereich
        header = QLabel("Samba Configuration Manager")
        main_layout.addWidget(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Linke Navigation
        left = QWidget()
        left_layout = QVBoxLayout(left)

        left_layout.addWidget(QLabel("Konfigurationsbereiche"))

        self.section_list = QListWidget()
        self.section_list.currentTextChanged.connect(
            self.select_section
        )
        left_layout.addWidget(self.section_list)

        self.btn_add_share = QPushButton("+ Neue Freigabe")
        self.btn_add_share.clicked.connect(self.add_share)
        left_layout.addWidget(self.btn_add_share)

        self.btn_edit_share = QPushButton("Freigabe bearbeiten")
        self.btn_edit_share.clicked.connect(self.edit_share)
        left_layout.addWidget(self.btn_edit_share)

        self.btn_delete_share = QPushButton(
            "Freigabe löschen"
        )
        self.btn_delete_share.clicked.connect(
            self.delete_share
        )
        left_layout.addWidget(self.btn_delete_share)

        # Rechte Seite
        right = QWidget()
        right_layout = QVBoxLayout(right)

        self.section_title = QLabel("Bitte Bereich auswählen")
        right_layout.addWidget(self.section_title)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(
            ["Parameter", "Wert"]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        right_layout.addWidget(self.table)

        # Parameteraktionen
        buttons = QHBoxLayout()

        self.btn_add = QPushButton("+ Parameter")
        self.btn_add.clicked.connect(self.add_option)

        self.btn_edit = QPushButton("Bearbeiten")
        self.btn_edit.clicked.connect(self.edit_option)

        self.btn_remove = QPushButton("Entfernen")
        self.btn_remove.clicked.connect(self.remove_option)

        buttons.addWidget(self.btn_add)
        buttons.addWidget(self.btn_edit)
        buttons.addWidget(self.btn_remove)

        right_layout.addLayout(buttons)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([280, 820])

        main_layout.addWidget(splitter)

        # Unterer Bereich
        footer = QHBoxLayout()

        self.btn_reload = QPushButton("Neu laden")
        self.btn_reload.clicked.connect(self.reload)

        self.btn_save = QPushButton("Konfiguration speichern")
        self.btn_save.clicked.connect(self.save)

        footer.addWidget(self.btn_reload)
        footer.addStretch()
        footer.addWidget(self.btn_save)

        main_layout.addLayout(footer)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage(
            f"Konfigurationsdatei: {CONFIG_FILE}"
        )

    def load_sections(self):
        self.section_list.blockSignals(True)
        self.section_list.clear()

        for section in self.samba.sections():
            self.section_list.addItem(section)

        self.section_list.blockSignals(False)

        self.section_list.setCurrentRow(0)

    def select_section(self, section):
        if not section:
            return

        self.current_section = section
        self.section_title.setText(
            f"Bereich: [{section}]"
        )

        self.table.setRowCount(0)

        options = self.samba.get_options(section)

        for key, value in options.items():
            row = self.table.rowCount()
            self.table.insertRow(row)

            self.table.setItem(
                row, 0, QTableWidgetItem(key)
            )

            self.table.setItem(
                row, 1,
                QTableWidgetItem(
                    "" if value is None else str(value)
                )
            )

        is_global = section.lower() == "global"
        self.btn_delete_share.setEnabled(not is_global)
        self.btn_edit_share.setEnabled(not is_global)

    def add_share(self):
        dialog = ShareDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        name = dialog.name
        try:
            self.samba.add_section(name)
            for key, value in dialog.options().items():
                self.samba.set_option(name, key, value)

            self.load_sections()
            self.select_share(name)
            self.statusBar().showMessage(
                f"Freigabe [{name}] hinzugefügt."
            )

        except Exception as e:
            self.error(str(e))

    def edit_share(self):
        section = self.current_section
        if not section or section.lower() == "global":
            return

        dialog = ShareDialog(
            self,
            section=section,
            options=self.samba.get_options(section)
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        new_name = dialog.name
        new_options = dialog.options()

        try:
            if new_name != section:
                # Keep parameters that are not managed by the dialog.
                options = dict(self.samba.get_options(section))
                options.update(new_options)
                for key in GUIDED_SHARE_OPTIONS:
                    if key not in new_options:
                        options.pop(key, None)

                self.samba.delete_section(section)
                self.samba.add_section(new_name)
                for key, value in options.items():
                    self.samba.set_option(new_name, key, value)
            else:
                for key in GUIDED_SHARE_OPTIONS:
                    if key not in new_options:
                        self.samba.delete_option(section, key)
                for key, value in new_options.items():
                    self.samba.set_option(section, key, value)

            self.load_sections()
            self.select_share(new_name)
            self.statusBar().showMessage(
                f"Freigabe [{new_name}] aktualisiert."
            )

        except Exception as e:
            self.error(str(e))

    def select_share(self, name):
        items = self.section_list.findItems(
            name, Qt.MatchFlag.MatchExactly
        )
        if items:
            self.section_list.setCurrentItem(items[0])

    def delete_share(self):
        section = self.current_section

        if not section or section.lower() == "global":
            return

        answer = QMessageBox.question(
            self,
            "Freigabe löschen",
            f"Freigabe [{section}] wirklich löschen?"
        )

        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            self.samba.delete_section(section)
            self.load_sections()
        except Exception as e:
            self.error(str(e))

    def add_option(self):
        if not self.current_section:
            return

        key, ok = QInputDialog.getText(
            self, "Parameter hinzufügen",
            "Parametername:"
        )

        if not ok or not key.strip():
            return

        value, ok = QInputDialog.getText(
            self, "Parameter hinzufügen",
            "Wert:"
        )

        if not ok:
            return

        try:
            self.samba.set_option(
                self.current_section,
                key.strip(),
                value
            )
            self.select_section(self.current_section)

        except Exception as e:
            self.error(str(e))

    def edit_option(self):
        row = self.table.currentRow()

        if row < 0:
            return

        key = self.table.item(row, 0).text()
        old_value = self.table.item(row, 1).text()

        value, ok = QInputDialog.getText(
            self,
            "Parameter bearbeiten",
            f"Wert für {key}:",
            QLineEdit.EchoMode.Normal,
            old_value
        )

        if not ok:
            return

        try:
            self.samba.set_option(
                self.current_section, key, value
            )
            self.select_section(self.current_section)

        except Exception as e:
            self.error(str(e))

    def remove_option(self):
        row = self.table.currentRow()

        if row < 0:
            return

        key = self.table.item(row, 0).text()

        answer = QMessageBox.question(
            self,
            "Parameter entfernen",
            f"Parameter '{key}' entfernen?"
        )

        if answer != QMessageBox.StandardButton.Yes:
            return

        self.samba.delete_option(
            self.current_section, key
        )
        self.select_section(self.current_section)

    def reload(self):
        answer = QMessageBox.question(
            self,
            "Neu laden",
            "Alle ungespeicherten Änderungen verwerfen?"
        )

        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            self.samba.load()
            self.load_sections()
            self.statusBar().showMessage(
                "Konfiguration neu geladen."
            )
        except Exception as e:
            self.error(str(e))

    def save(self):
        try:
            self.samba.save()

            QMessageBox.information(
                self,
                "Erfolgreich",
                "Die Samba-Konfiguration wurde "
                "validiert und gespeichert."
            )

            self.statusBar().showMessage(
                "Konfiguration erfolgreich gespeichert."
            )

        except Exception as e:
            self.error(
                "Speichern fehlgeschlagen:\n\n" + str(e)
            )

    def error(self, message):
        QMessageBox.critical(
            self, "Fehler", message
        )


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Samba Config Manager")

    _require_root()

    window = SambaManager()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
