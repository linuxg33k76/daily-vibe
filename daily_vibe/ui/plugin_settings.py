"""Preferences → Plugins page: plugin list + schema-rendered settings forms."""
from __future__ import annotations

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSpinBox, QSplitter, QStackedWidget, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from daily_vibe import secrets
from daily_vibe.config import user_plugins_dir
from daily_vibe.plugin_manager import Plugin, PluginManager, SettingField
from daily_vibe.workers import TaskRunner

SOURCE_LABEL = {"builtin": "built-in", "user": "user", "package": "package"}


def _hint(text: str, name: str = "hint") -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def _column(first: QWidget) -> QWidget:
    col = QWidget()
    v = QVBoxLayout(col)
    v.setContentsMargins(0, 0, 0, 4)
    v.setSpacing(3)
    v.addWidget(first)
    return col


class PluginsPage(QWidget):
    def __init__(self, plugins: PluginManager, tasks: TaskRunner, parent=None, journal_mode: bool = False):
        super().__init__(parent)
        self.journal_mode = journal_mode  # per-journal overrides: no folder buttons, no secrets
        self.plugins = plugins
        self.tasks = tasks
        self.items: dict[str, QTreeWidgetItem] = {}
        self.fields: dict[str, dict[str, tuple[SettingField, QWidget]]] = {}
        self.check_labels: dict[tuple[str, str], QLabel] = {}
        # type "action" rows: (plugin id, key) -> (field, status label, buttons)
        self.action_rows: dict[tuple[str, str], tuple[SettingField, QLabel, list[QPushButton]]] = {}
        self._data: dict = {}

        v = QVBoxLayout(self)
        if journal_mode:
            v.setContentsMargins(0, 0, 0, 0)
        top_w = QWidget()
        top = QHBoxLayout(top_w)
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(_hint(f"User plugins folder: {user_plugins_dir()}"), 1)
        self.open_btn = QPushButton("Open plugins folder")
        self.open_btn.clicked.connect(self._open_folder)
        self.reload_btn = QPushButton("Reload plugins")
        self.reload_btn.clicked.connect(self.reload)
        top.addWidget(self.open_btn)
        top.addWidget(self.reload_btn)
        v.addWidget(top_w)
        top_w.setVisible(not journal_mode)

        split = QSplitter(Qt.Orientation.Vertical)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Plugin", "Version", "Source", "Status"])
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(0, 230)
        self.tree.setColumnWidth(1, 70)
        self.tree.setColumnWidth(2, 80)
        self.tree.currentItemChanged.connect(self._select)
        split.addWidget(self.tree)
        details = QWidget()
        dv = QVBoxLayout(details)
        dv.setContentsMargins(0, 6, 0, 0)
        self.info = _hint("")
        self.info.setObjectName("")
        dv.addWidget(self.info)
        self.error_label = _hint("", "warning")
        dv.addWidget(self.error_label)
        self.forms = QStackedWidget()
        dv.addWidget(self.forms, 1)
        split.addWidget(details)
        split.setSizes([170, 260])
        v.addWidget(split, 1)
        v.addWidget(_hint(
            "Check the plugins this journal uses. Settings changed here apply to this journal only; "
            "anything left as-is follows Preferences. Secret settings (API keys) are always global."
            if journal_mode else
            "Check a plugin to enable it. Enabled plugins run when a new entry is created "
            "and on Entry → Refresh Plugin Blocks."))

    # Build ------------------------------------------------------------------------
    def load(self, data: dict) -> None:
        self._data = data
        self.tree.clear()
        self.items.clear()
        self.fields.clear()
        self.action_rows.clear()
        self.check_labels.clear()
        while self.forms.count():
            w = self.forms.widget(0)
            self.forms.removeWidget(w)
            w.deleteLater()
        enabled = set(data.get("plugins", {}).get("enabled", []))
        for plugin in self.plugins.records:
            status = {"ok": "OK", "warning": "warning", "error": "error"}[plugin.status]
            if plugin.status == "ok" and not plugin.implemented:
                status = "stub"
            item = QTreeWidgetItem([plugin.display_name, plugin.version, SOURCE_LABEL.get(plugin.source, plugin.source), status])
            item.setData(0, Qt.ItemDataRole.UserRole, plugin)
            if plugin.status != "error":
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Checked if plugin.id in enabled else Qt.CheckState.Unchecked)
            else:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            self.tree.addTopLevelItem(item)
            if plugin.status != "error":
                self.items[plugin.id] = item
            item.setData(1, Qt.ItemDataRole.UserRole, self.forms.addWidget(self._build_form(plugin, data)))
        if self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))

    def _build_form(self, plugin: Plugin, data: dict) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 6, 0)
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(form)
        outer.addStretch(1)  # keep rows compact; long forms scroll
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(w)
        if not plugin.settings:
            form.addRow(_hint("This plugin has no settings."))
            return scroll
        raw = data.get("plugins", {}).get(plugin.id, {})
        raw = raw if isinstance(raw, dict) else {}
        self.fields[plugin.id] = {}
        for f in plugin.settings:
            if f.type == "action":
                self._add_action_row(form, plugin, f)
                continue
            if f.type == "secret" and self.journal_mode:
                form.addRow(f.label + ":", _hint("(global only: set it in Preferences → Plugins)"))
                continue
            if f.type == "secret":
                value = secrets.get_secret(plugin.id, f.key, raw)
            else:
                value = f.coerce(raw.get(f.key, f.default))
            widget = self._make_widget(f, value)
            self.fields[plugin.id][f.key] = (f, widget)
            row_widget: QWidget = widget
            if f.check is not None:
                row = QWidget()
                h = QHBoxLayout(row)
                h.setContentsMargins(0, 0, 0, 0)
                h.addWidget(widget, 1)
                btn = QPushButton(f.check_label)
                btn.clicked.connect(lambda _c=False, p=plugin.id, fld=f: self._run_check(p, fld))
                h.addWidget(btn)
                row_widget = row
            # Field + its notes in one column widget: QFormLayout over-reserves height
            # for word-wrapped labels placed in their own rows.
            col = _column(row_widget)
            if f.check is not None:
                result = _hint("")
                self.check_labels[(plugin.id, f.key)] = result
                col.layout().addWidget(result)
            if f.help:
                col.layout().addWidget(_hint(f.help))
            if f.type == "secret":
                where = ("stored in the system keyring" if secrets.keyring_available() else
                         "⚠ no keyring available: stored in plain text in config.toml "
                         "(pip install keyring to use the system keyring)")
                col.layout().addWidget(_hint(where, "hint" if secrets.keyring_available() else "warning"))
            form.addRow(f.label + ":", col)
        return scroll

    def _add_action_row(self, form: QFormLayout, plugin: Plugin, f: SettingField) -> None:
        if self.journal_mode:  # accounts etc. are app-wide
            form.addRow(f.label + ":", _hint("(global only: use Preferences → Plugins)"))
            return
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        buttons = []
        for i, action in enumerate(f.actions):
            btn = QPushButton(action["label"])
            btn.setObjectName(f"action_{plugin.id}_{f.key}_{i}")
            btn.clicked.connect(lambda _c=False, p=plugin.id, fld=f, a=action: self._run_action(p, fld, a))
            h.addWidget(btn)
            buttons.append(btn)
        h.addStretch(1)
        col = _column(row)
        status = _hint("")
        col.layout().addWidget(status)
        if f.help:
            col.layout().addWidget(_hint(f.help))
        form.addRow(f.label + ":", col)
        self.action_rows[(plugin.id, f.key)] = (f, status, buttons)
        self._refresh_action_status(plugin.id, f.key)

    def form_values(self, plugin_id: str) -> dict:
        """Current (unsaved) values of a plugin's form, secrets included."""
        return {key: self.widget_value(f, w) for key, (f, w) in self.fields.get(plugin_id, {}).items()}

    def _refresh_action_status(self, plugin_id: str, key: str, prefix: str = "") -> None:
        f, label, _buttons = self.action_rows[(plugin_id, key)]
        text = prefix
        if f.status is not None:
            try:
                status = f.status(self.form_values(plugin_id))
            except Exception as exc:  # a status callback must never break the dialog
                status = f"⚠ {exc}"
            text = f"{prefix}\n{status}" if prefix else status
        label.setText(text.strip())

    def _run_action(self, plugin_id: str, f: SettingField, action: dict) -> None:
        _f, label, buttons = self.action_rows[(plugin_id, f.key)]
        values = self.form_values(plugin_id)
        label.setText(f"{action['label']}… (see your web browser if a sign-in page opened)")

        def done(text, ok=True):
            try:
                self._refresh_action_status(plugin_id, f.key, str(text) if ok else f"⚠ {text}")
            except RuntimeError:  # dialog closed meanwhile
                pass

        self.tasks.submit(lambda: action["run"](values), done, lambda exc: done(exc, False))

    @staticmethod
    def _make_widget(f: SettingField, value) -> QWidget:
        if f.type == "bool":
            w = QCheckBox()
            w.setChecked(bool(value))
        elif f.type == "int":
            w = QSpinBox()
            w.setRange(int(f.min if f.min is not None else -1_000_000), int(f.max if f.max is not None else 1_000_000))
            w.setValue(int(value))
        elif f.type == "float":
            w = QDoubleSpinBox()
            w.setDecimals(4)
            w.setRange(float(f.min if f.min is not None else -1e9), float(f.max if f.max is not None else 1e9))
            w.setValue(float(value))
        elif f.type == "choice":
            w = QComboBox()
            w.addItems([str(c) for c in f.choices])
            w.setCurrentIndex(max(0, [str(c) for c in f.choices].index(str(value)) if str(value) in map(str, f.choices) else 0))
        else:
            w = QLineEdit(str(value))
            if f.type == "secret":
                w.setEchoMode(QLineEdit.EchoMode.Password)
        return w

    @staticmethod
    def widget_value(f: SettingField, w: QWidget):
        if f.type == "bool":
            return w.isChecked()
        if f.type in ("int", "float"):
            return w.value()
        if f.type == "choice":
            return f.choices[w.currentIndex()] if f.choices else ""
        return w.text().strip() if f.type != "secret" else w.text()

    # Interaction --------------------------------------------------------------------
    def _select(self, item: QTreeWidgetItem, _prev=None) -> None:
        if item is None:
            return
        plugin: Plugin = item.data(0, Qt.ItemDataRole.UserRole)
        where = str(plugin.path) if plugin.path else "(installed package)"
        self.info.setText(f"<b>{plugin.display_name}</b> <span>v{plugin.version}</span> · id <code>{plugin.id}</code>"
                          f" · API {plugin.api_version}{' · by ' + plugin.author if plugin.author else ''}<br>"
                          f"{plugin.description}<br><small>{where}</small>")
        self.error_label.setText(plugin.error or "")
        self.error_label.setVisible(bool(plugin.error))
        self.forms.setCurrentIndex(item.data(1, Qt.ItemDataRole.UserRole))

    def _run_check(self, plugin_id: str, f: SettingField) -> None:
        label = self.check_labels[(plugin_id, f.key)]
        value = self.widget_value(f, self.fields[plugin_id][f.key][1])
        label.setText("Checking…")
        self.tasks.submit(lambda: f.check(value), lambda text: label.setText(f"✓ {text}"),
                          lambda exc: label.setText(f"⚠ {exc}"))

    def _open_folder(self) -> None:
        d = user_plugins_dir()
        d.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))

    def reload(self) -> None:
        pending = self.collect_into({"plugins": {k: (dict(v) if isinstance(v, dict) else v)
                                                 for k, v in self._data.get("plugins", {}).items()}},
                                    write_secrets=False)
        self.plugins.discover()
        self.load(pending)

    # API used by the dialog and tests ------------------------------------------------
    def set_enabled(self, plugin_id: str, on: bool) -> None:
        self.items[plugin_id].setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def is_enabled(self, plugin_id: str) -> bool:
        return self.items[plugin_id].checkState(0) == Qt.CheckState.Checked

    def field(self, plugin_id: str, key: str) -> QWidget:
        return self.fields[plugin_id][key][1]

    def collect_into(self, data: dict, write_secrets: bool = True) -> dict:
        plugins = data.setdefault("plugins", {})
        current = plugins.get("enabled", [])
        order = [n for n in current if n in self.items and self.is_enabled(n)]
        order += [n for n in self.items if self.is_enabled(n) and n not in order]
        order += [n for n in current if n not in self.items]  # keep ids of plugins not loaded now
        plugins["enabled"] = order
        for pid, fields in self.fields.items():
            section = plugins.get(pid)
            section = dict(section) if isinstance(section, dict) else {}
            for key, (f, w) in fields.items():
                value = self.widget_value(f, w)
                if f.type == "secret":
                    if write_secrets:
                        secrets.set_secret(pid, key, value, section)
                    else:
                        section[key] = value
                else:
                    section[key] = value
            plugins[pid] = section
        return data
