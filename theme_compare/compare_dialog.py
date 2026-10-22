"""Window that compares two map themes."""

import csv
import os

from qgis.PyQt.QtCore import QEvent, Qt, QTimer
from qgis.PyQt.QtGui import QBrush, QColor, QGuiApplication
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)
from qgis.core import QgsApplication, QgsMapThemeCollection, QgsProject, QgsSettings

from . import theme_logic as tl

SETTINGS_PREFIX = "theme_compare/"
CURRENT_LABEL = "Current map view (unsaved)"

COL_NAME, COL_A, COL_B, COL_DIFF = range(4)

# Pixel width theme names are elided to inside sentences
NAME_WIDTH = 180

# Translucent so they work with light and dark UI themes
COLOR_ONLY_A = QColor(230, 80, 60, 70)
COLOR_ONLY_B = QColor(40, 140, 230, 70)
COLOR_OTHER = QColor(240, 180, 30, 70)
CSS_A = "rgba(230,80,60,90)"
CSS_B = "rgba(40,140,230,90)"
CSS_OTHER = "rgba(240,180,30,90)"


def _icon(name):
    return QgsApplication.getThemeIcon(name)


def _layer_icon(layer):
    try:
        from qgis.core import QgsIconUtils
        return QgsIconUtils.iconForLayer(layer)
    except ImportError:  # QGIS < 3.20
        return _icon("/mIconLayer.png")


def _html(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class ThemeCompareDialog(QDialog):

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setObjectName("ThemeCompareDialog")
        self.setWindowTitle("Theme Compare")
        # A normal top level window: closable and maximizable, but no minimize button
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowTitleHint
                            | Qt.WindowType.WindowSystemMenuHint | Qt.WindowType.WindowMaximizeButtonHint
                            | Qt.WindowType.WindowCloseButtonHint)
        self.setModal(False)
        self.resize(640, 700)

        self._result = None
        self._connected_collection = None
        self._connected_root = None
        self._connected_model = None

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(250)
        self._refresh_timer.timeout.connect(self.refresh)

        self._build_ui()
        self._load_settings()
        self._connect_project()
        self.reload_themes()

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)

        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        self.combo_a = QComboBox()
        self.combo_b = QComboBox()
        for combo in (self.combo_a, self.combo_b):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(12)
            combo.currentIndexChanged.connect(self._on_selection_changed)
        self.combo_a.setToolTip("First theme to compare (red)")
        self.combo_b.setToolTip("Second theme to compare (blue)")

        swatch_a = QLabel()
        swatch_b = QLabel()
        for swatch, css in ((swatch_a, CSS_A), (swatch_b, CSS_B)):
            swatch.setFixedSize(14, 14)
            swatch.setStyleSheet("background: {}; border-radius: 3px;".format(css))

        self.btn_apply_a = self._tool_button("/mActionShowAllLayers.svg", "Apply this theme to the map",
                                             lambda: self.apply_theme(self.combo_a))
        self.btn_apply_b = self._tool_button("/mActionShowAllLayers.svg", "Apply this theme to the map",
                                             lambda: self.apply_theme(self.combo_b))
        self.btn_swap = self._tool_button("/mActionReload.svg", "Swap the two themes", self.swap)
        self.btn_swap.setText("⇅")
        self.btn_swap.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.btn_swap.setStyleSheet("font-size: 18px; font-weight: bold;")

        grid.addWidget(swatch_a, 0, 0)
        grid.addWidget(self.combo_a, 0, 1)
        grid.addWidget(self.btn_apply_a, 0, 2)
        grid.addWidget(self.btn_swap, 0, 3, 2, 1)
        grid.addWidget(swatch_b, 1, 0)
        grid.addWidget(self.combo_b, 1, 1)
        grid.addWidget(self.btn_apply_b, 1, 2)
        lay.addLayout(grid)

        # Toolbar row
        bar = QHBoxLayout()
        self.btn_save = self._tool_button("/mActionFileSave.svg",
                                          "Save the current map view as a new theme…", self.save_current_as_theme)
        self.btn_refresh = self._tool_button("/mActionRefresh.svg", "Refresh comparison", self.refresh)
        self.btn_expand = self._tool_button("/mActionExpandTree.svg", "Expand all", lambda: self.tree.expandAll())
        self.btn_collapse = self._tool_button("/mActionCollapseTree.svg", "Collapse all",
                                              lambda: self.tree.collapseAll())
        self.btn_copy = self._tool_button("/mActionEditCopy.svg", "Copy the comparison to the clipboard",
                                          self.copy_to_clipboard)
        self.btn_export = self._tool_button("/mActionFileSaveAs.svg", "Export the comparison to CSV…",
                                            self.export_csv)

        self.btn_options = QToolButton()
        self.btn_options.setIcon(_icon("/mActionOptions.svg"))
        self.btn_options.setToolTip("Choose what to compare")
        self.btn_options.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_options.setAutoRaise(True)
        self.options_menu = QMenu(self)
        self.category_actions = {}
        self.options_menu.addSection("Compare")
        for cat in tl.ALL_CATEGORIES:
            act = self.options_menu.addAction(tl.CATEGORY_LABELS[cat])
            act.setCheckable(True)
            act.toggled.connect(self._on_options_changed)
            self.category_actions[cat] = act
        self.category_actions[tl.DIFF_VISIBILITY].setEnabled(False)  # always compared
        self.options_menu.addSeparator()
        self.act_auto = self.options_menu.addAction("Refresh automatically")
        self.act_auto.setCheckable(True)
        self.act_auto.toggled.connect(self._on_options_changed)
        self.btn_options.setMenu(self.options_menu)

        for b in (self.btn_save, self.btn_refresh, self.btn_expand, self.btn_collapse,
                  self.btn_copy, self.btn_export):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(self.btn_options)
        lay.addLayout(bar)

        # Filter row
        frow = QHBoxLayout()
        self.chk_only_diff = QCheckBox("Only differences")
        self.chk_only_diff.setToolTip("Hide layers and groups that are the same in both themes")
        self.chk_only_diff.toggled.connect(self._on_options_changed)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter layers…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._populate_tree)
        frow.addWidget(self.chk_only_diff)
        frow.addWidget(self.search, 1)
        lay.addLayout(frow)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.summary)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Layer", "", "", "Difference"])
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setUniformRowHeights(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        header = self.tree.header()
        header.setStretchLastSection(True)
        for col in (COL_NAME, COL_A, COL_B):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        self.tree.setColumnWidth(COL_NAME, 220)
        self.tree.setColumnWidth(COL_A, 130)
        self.tree.setColumnWidth(COL_B, 130)
        header.sectionResized.connect(self._update_headers)
        lay.addWidget(self.tree, 1)

        self.legend = QLabel()
        self.legend.setTextFormat(Qt.TextFormat.RichText)
        self.legend.setWordWrap(True)
        lay.addWidget(self.legend)

    def _tool_button(self, icon, tip, slot):
        b = QToolButton()
        b.setIcon(_icon(icon))
        b.setToolTip(tip)
        b.setAutoRaise(True)
        b.clicked.connect(slot)
        return b

    # -------------------------------------------------------------- names

    def _elide(self, text, width=NAME_WIDTH):
        return self.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, width)

    def _name(self, combo, width=NAME_WIDTH):
        """Theme name shown in texts, elided with "…" when too long."""
        key = combo.currentData()
        return self._elide(tl.display_name(key), width) if key is not None else ""

    def _update_headers(self, *args):
        header_item = self.tree.headerItem()
        for col, combo in ((COL_A, self.combo_a), (COL_B, self.combo_b)):
            full = tl.display_name(combo.currentData()) if combo.currentData() is not None else ""
            # leave room for the header padding / sort indicator
            header_item.setText(col, self._elide(full, max(self.tree.columnWidth(col) - 16, 20)))
            header_item.setToolTip(col, full)

    # ------------------------------------------------------------ settings

    def _load_settings(self):
        s = QgsSettings()
        defaults = {tl.DIFF_VISIBILITY: True, tl.DIFF_CHECKED: True, tl.DIFF_STYLE: True,
                    tl.DIFF_LEGEND: True, tl.DIFF_EXPANDED: False}
        for cat, act in self.category_actions.items():
            act.blockSignals(True)
            act.setChecked(s.value(SETTINGS_PREFIX + "compare_" + cat, defaults[cat], type=bool)
                           or cat == tl.DIFF_VISIBILITY)
            act.blockSignals(False)
        self.chk_only_diff.blockSignals(True)
        self.chk_only_diff.setChecked(s.value(SETTINGS_PREFIX + "only_differences", False, type=bool))
        self.chk_only_diff.blockSignals(False)
        self.act_auto.blockSignals(True)
        self.act_auto.setChecked(s.value(SETTINGS_PREFIX + "auto_refresh", True, type=bool))
        self.act_auto.blockSignals(False)
        geometry = s.value(SETTINGS_PREFIX + "geometry")
        if geometry:
            self.restoreGeometry(geometry)

    def _save_settings(self):
        s = QgsSettings()
        for cat, act in self.category_actions.items():
            s.setValue(SETTINGS_PREFIX + "compare_" + cat, act.isChecked())
        s.setValue(SETTINGS_PREFIX + "only_differences", self.chk_only_diff.isChecked())
        s.setValue(SETTINGS_PREFIX + "auto_refresh", self.act_auto.isChecked())

    def _on_options_changed(self, *args):
        self._save_settings()
        self._populate_tree()

    def categories(self):
        return [cat for cat, act in self.category_actions.items() if act.isChecked()]

    def hideEvent(self, event):
        QgsSettings().setValue(SETTINGS_PREFIX + "geometry", self.saveGeometry())
        super().hideEvent(event)

    def changeEvent(self, event):
        # No minimize button, but the window manager may still minimize (e.g. Win+Down)
        if event.type() == QEvent.Type.WindowStateChange and self.windowState() & Qt.WindowState.WindowMinimized:
            QTimer.singleShot(0, lambda: self.setWindowState(
                self.windowState() & ~Qt.WindowState.WindowMinimized))
        super().changeEvent(event)

    # ------------------------------------------------------ project wiring

    def _layer_tree_model(self):
        view = self.iface.layerTreeView()
        if hasattr(view, "layerTreeModel"):
            return view.layerTreeModel()
        return view.model()

    def _connect_project(self):
        project = QgsProject.instance()
        project.readProject.connect(self._on_project_changed)
        project.cleared.connect(self._on_project_changed)
        project.layersRemoved.connect(self._schedule_refresh)
        if hasattr(project, "mapThemeCollectionChanged"):
            project.mapThemeCollectionChanged.connect(self._on_project_changed)
        self._connect_collection()
        self._connect_tree()

    def _connect_collection(self):
        self._disconnect_collection()
        collection = QgsProject.instance().mapThemeCollection()
        collection.mapThemesChanged.connect(self.reload_themes)
        collection.mapThemeChanged.connect(self._schedule_refresh)
        if hasattr(collection, "mapThemeRenamed"):
            collection.mapThemeRenamed.connect(self._on_theme_renamed)
        self._connected_collection = collection

    def _disconnect_collection(self):
        c = self._connected_collection
        self._connected_collection = None
        if c is None:
            return
        for sig, slot in ((c.mapThemesChanged, self.reload_themes),
                          (c.mapThemeChanged, self._schedule_refresh),
                          (getattr(c, "mapThemeRenamed", None), self._on_theme_renamed)):
            if sig is None:
                continue
            try:
                sig.disconnect(slot)
            except (TypeError, RuntimeError):
                pass

    def _connect_tree(self):
        self._disconnect_tree()
        root = QgsProject.instance().layerTreeRoot()
        root.visibilityChanged.connect(self._on_tree_changed)
        root.addedChildren.connect(self._on_tree_changed)
        root.removedChildren.connect(self._on_tree_changed)
        root.nameChanged.connect(self._on_tree_changed)
        root.expandedChanged.connect(self._on_tree_changed)
        model = self._layer_tree_model()
        model.dataChanged.connect(self._on_tree_changed)
        self._connected_root = root
        self._connected_model = model

    def _disconnect_tree(self):
        root, model = self._connected_root, self._connected_model
        self._connected_root = self._connected_model = None
        pairs = []
        if root is not None:
            pairs += [root.visibilityChanged, root.addedChildren, root.removedChildren,
                      root.nameChanged, root.expandedChanged]
        if model is not None:
            pairs.append(model.dataChanged)
        for sig in pairs:
            try:
                sig.disconnect(self._on_tree_changed)
            except (TypeError, RuntimeError):
                pass

    def disconnect_all(self):
        self._refresh_timer.stop()
        project = QgsProject.instance()
        sigs = [(project.readProject, self._on_project_changed),
                (project.cleared, self._on_project_changed),
                (project.layersRemoved, self._schedule_refresh)]
        if hasattr(project, "mapThemeCollectionChanged"):
            sigs.append((project.mapThemeCollectionChanged, self._on_project_changed))
        for sig, slot in sigs:
            try:
                sig.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._disconnect_collection()
        self._disconnect_tree()

    def _on_project_changed(self, *args):
        self._connect_collection()
        self._connect_tree()
        self.reload_themes()

    def _on_theme_renamed(self, old, new):
        for combo in (self.combo_a, self.combo_b):
            if combo.currentData() == old:
                combo.setProperty("pending_theme", new)

    def _on_tree_changed(self, *args):
        if self.act_auto.isChecked() and self.isVisible():
            self._schedule_refresh()

    def _schedule_refresh(self, *args):
        if self.act_auto.isChecked():
            self._refresh_timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_refresh()

    # -------------------------------------------------------------- combos

    def reload_themes(self, *args):
        themes = QgsProject.instance().mapThemeCollection().mapThemes()
        prev = []
        for combo in (self.combo_a, self.combo_b):
            pending = combo.property("pending_theme")
            combo.setProperty("pending_theme", None)
            prev.append(pending or combo.currentData())
        if prev[0] == prev[1] == tl.CURRENT_STATE:
            prev[1] = None  # nothing useful selected yet: pick a theme for the second side once one exists

        for combo in (self.combo_a, self.combo_b):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(_icon("/mActionShowAllLayers.svg"), CURRENT_LABEL, tl.CURRENT_STATE)
            if themes:
                combo.insertSeparator(1)
            for name in themes:
                combo.addItem(_icon("/mActionShowPresets.svg"), name, name)
                combo.setItemData(combo.count() - 1, name, Qt.ItemDataRole.ToolTipRole)
            combo.blockSignals(False)

        defaults = [tl.CURRENT_STATE, themes[0] if themes else tl.CURRENT_STATE]
        for combo, key, default in zip((self.combo_a, self.combo_b), prev, defaults):
            idx = combo.findData(key) if key else -1
            if idx < 0:
                idx = combo.findData(default)
            combo.blockSignals(True)
            combo.setCurrentIndex(max(idx, 0))
            combo.blockSignals(False)
        self._update_buttons()
        self.refresh()

    def _on_selection_changed(self, *args):
        self._update_buttons()
        self.refresh()

    def _update_buttons(self):
        for combo, btn in ((self.combo_a, self.btn_apply_a), (self.combo_b, self.btn_apply_b)):
            key = combo.currentData()
            btn.setEnabled(key != tl.CURRENT_STATE)
            btn.setToolTip("Apply theme '{}' to the map".format(key) if key != tl.CURRENT_STATE
                           else "The current view is already shown on the map")
        self._update_headers()

    def swap(self):
        ia, ib = self.combo_a.currentIndex(), self.combo_b.currentIndex()
        for combo, idx in ((self.combo_a, ib), (self.combo_b, ia)):
            combo.blockSignals(True)
            combo.setCurrentIndex(idx)
            combo.blockSignals(False)
        self._on_selection_changed()

    # ------------------------------------------------------------ compare

    def refresh(self):
        self._refresh_timer.stop()
        project = QgsProject.instance()
        key_a, key_b = self.combo_a.currentData(), self.combo_b.currentData()
        if key_a is None or key_b is None:
            return
        try:
            self._result = tl.compare_themes(project, project.layerTreeRoot(), self._layer_tree_model(),
                                             key_a, key_b, self._name(self.combo_a), self._name(self.combo_b))
        except KeyError:
            self._result = None
        self._populate_tree()

    def _row_matches_filter(self, node, text):
        return not text or text in node.name.lower()

    def _populate_tree(self, *args):
        tree = self.tree
        expanded = self._expanded_paths()
        first_fill = tree.topLevelItemCount() == 0
        selected = set(self._selected_keys())
        tree.setUpdatesEnabled(False)
        tree.clear()
        result = self._result
        cats = self.categories()
        only_diff = self.chk_only_diff.isChecked()
        text = self.search.text().strip().lower()
        self._update_headers()

        if result is None:
            self.summary.setText("<i>Select two themes to compare.</i>")
            self.legend.clear()
            tree.setUpdatesEnabled(True)
            return

        def include(node, parent_matched):
            if only_diff and not node.subtree_has_difference(cats):
                return False
            if text and not parent_matched and not self._row_matches_filter(node, text):
                return any(include(c, False) for c in node.children)
            return True

        def add(node, parent_item, parent_matched=False):
            if not include(node, parent_matched):
                return
            matched = parent_matched or (bool(text) and self._row_matches_filter(node, text))
            item = self._make_item(node, cats)
            if parent_item is None:
                tree.addTopLevelItem(item)
            else:
                parent_item.addChild(item)
            for child in node.children:
                add(child, item, matched)
            key = self._node_key(node)
            if key in selected:
                item.setSelected(True)
            if node.kind == "group":
                item.setExpanded(first_fill or bool(text) or key in expanded or only_diff)

        for child in result.children:
            add(child, None)
        tree.setUpdatesEnabled(True)
        self._update_summary(result, cats)

    def _node_key(self, node):
        return node.layer_id if node.kind == "layer" else "group:" + (node.path + "/" + node.name).strip("/")

    def _expanded_paths(self):
        out = set()

        def walk(item):
            for i in range(item.childCount()):
                child = item.child(i)
                node = child.data(COL_NAME, Qt.ItemDataRole.UserRole)
                if node is not None and child.isExpanded():
                    out.add(self._node_key(node))
                walk(child)
        walk(self.tree.invisibleRootItem())
        return out

    def _selected_keys(self):
        return [self._node_key(it.data(COL_NAME, Qt.ItemDataRole.UserRole))
                for it in self.tree.selectedItems()
                if it.data(COL_NAME, Qt.ItemDataRole.UserRole) is not None]

    def _state_cell(self, item, col, state, node):
        if state.visible:
            item.setIcon(col, _icon("/mActionShowAllLayers.svg"))
            item.setText(col, "Visible")
        elif state.checked:
            item.setIcon(col, _icon("/mActionHideAllLayers.svg"))
            item.setText(col, "Hidden (group)")
            item.setToolTip(col, "Checked, but a parent group is switched off")
        else:
            item.setIcon(col, _icon("/mActionHideAllLayers.svg"))
            item.setText(col, "Hidden")
        tips = []
        if node.kind == "layer" and state.checked:
            if state.style:
                tips.append("Style: {}".format(state.style))
            if state.legend is not None and node.legend_labels:
                on = [node.legend_labels.get(k, k) for k in node.legend_labels if k in state.legend]
                tips.append("Legend items on: {} of {}".format(len(on), len(node.legend_labels)))
        if state.expanded is not None:
            tips.append("Expanded" if state.expanded else "Collapsed")
        if tips:
            item.setToolTip(col, "\n".join(filter(None, [item.toolTip(col)] + tips)))
        if not state.visible:
            item.setForeground(col, QBrush(QColor(128, 128, 128)))

    def _make_item(self, node, cats):
        item = QTreeWidgetItem()
        item.setData(COL_NAME, Qt.ItemDataRole.UserRole, node)
        item.setText(COL_NAME, node.name)
        if node.kind == "group":
            item.setIcon(COL_NAME, _icon("/mActionFolder.svg"))
            font = item.font(COL_NAME)
            font.setBold(True)
            item.setFont(COL_NAME, font)
        else:
            item.setIcon(COL_NAME, _layer_icon(node.layer))
            item.setToolTip(COL_NAME, "{}\n{}".format(node.name, node.layer.source()))
        self._state_cell(item, COL_A, node.a, node)
        self._state_cell(item, COL_B, node.b, node)

        diffs = [(c, t) for c, t in node.differences if c in cats]
        if diffs:
            item.setText(COL_DIFF, "; ".join(t for _, t in diffs))
            item.setToolTip(COL_DIFF, "\n".join(t for _, t in diffs))
            if any(c == tl.DIFF_VISIBILITY for c, _ in diffs):
                color = COLOR_ONLY_A if node.a.visible else COLOR_ONLY_B
            else:
                color = COLOR_OTHER
            for col in range(4):
                item.setBackground(col, QBrush(color))
        else:
            item.setText(COL_DIFF, "Same")
            item.setForeground(COL_DIFF, QBrush(QColor(128, 128, 128)))
        return item

    def _update_summary(self, result, cats):
        c = tl.summarize(result, cats)
        name_a = _html(self._name(self.combo_a))
        name_b = _html(self._name(self.combo_b))
        chip = "<span style='background: {}'>&nbsp;{}&nbsp;</span>"
        self.legend.setText(" &nbsp; ".join([
            chip.format(CSS_A, "&nbsp;") + " only in <b>{}</b>".format(name_a),
            chip.format(CSS_B, "&nbsp;") + " only in <b>{}</b>".format(name_b),
            chip.format(CSS_OTHER, "&nbsp;") + " other difference",
        ]))
        same_theme = self.combo_a.currentData() == self.combo_b.currentData()
        if c["layers"] == 0:
            self.summary.setText("<i>The project has no layers.</i>")
            return
        parts = ["Comparing <b>{}</b> with <b>{}</b>".format(name_a, name_b)]
        if same_theme:
            parts.append("<i>Both sides show the same theme.</i>")
        stats = [
            chip.format(CSS_A, "{} only in {}".format(c["only_a"], name_a)),
            chip.format(CSS_B, "{} only in {}".format(c["only_b"], name_b)),
        ]
        if c["other"]:
            stats.append(chip.format(CSS_OTHER, "{} with other differences".format(c["other"])))
        stats.append("{} visible in both, {} hidden in both".format(c["same_visible"], c["same_hidden"]))
        if c["groups_diff"]:
            stats.append("{} group(s) differ".format(c["groups_diff"]))
        parts.append(" · ".join(stats))
        if not same_theme and c["only_a"] == c["only_b"] == c["other"] == c["groups_diff"] == 0:
            parts.append("<b>✔ No differences found.</b>")
        self.summary.setText("<br>".join(parts))

    # ------------------------------------------------------------ actions

    def apply_theme(self, combo):
        key = combo.currentData()
        if key in (None, tl.CURRENT_STATE):
            return
        project = QgsProject.instance()
        project.mapThemeCollection().applyTheme(key, project.layerTreeRoot(), self._layer_tree_model())
        self.iface.mapCanvas().refreshAllLayers()
        self.iface.messageBar().pushSuccess("Theme Compare", "Applied theme '{}'".format(key))

    def save_current_as_theme(self):
        project = QgsProject.instance()
        collection = project.mapThemeCollection()
        existing = collection.mapThemes()
        n = len(existing) + 1
        default = "Theme {}".format(n)
        while default in existing:
            n += 1
            default = "Theme {}".format(n)
        name, ok = QInputDialog.getText(self, "Save Current View as Theme", "Theme name:", text=default)
        name = name.strip()
        if not ok or not name:
            return
        record = QgsMapThemeCollection.createThemeFromCurrentState(project.layerTreeRoot(),
                                                                   self._layer_tree_model())
        if collection.hasMapTheme(name):
            answer = QMessageBox.question(
                self, "Replace Theme", "A theme named '{}' already exists. Replace it with the current view?"
                .format(name))
            if answer != QMessageBox.StandardButton.Yes:
                return
            collection.update(name, record)
        else:
            collection.insert(name, record)
        # Show the new theme on the side that was showing the current view, if any
        for combo in (self.combo_b, self.combo_a):
            if combo.currentData() == tl.CURRENT_STATE:
                break
        else:
            combo = self.combo_b
        self.reload_themes()
        idx = combo.findData(name)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        self.iface.messageBar().pushSuccess("Theme Compare", "Saved theme '{}'".format(name))

    def _selected_nodes(self):
        return [it.data(COL_NAME, Qt.ItemDataRole.UserRole) for it in self.tree.selectedItems()
                if it.data(COL_NAME, Qt.ItemDataRole.UserRole) is not None]

    def _context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None:
            return
        if not item.isSelected():
            self.tree.clearSelection()
            item.setSelected(True)
        nodes = self._selected_nodes()
        if not nodes:
            return
        menu = QMenu(self)
        node = item.data(COL_NAME, Qt.ItemDataRole.UserRole)
        act = menu.addAction(_icon("/mActionShowSelectedLayers.svg"), "Show in Layers Panel")
        act.triggered.connect(lambda: self.select_in_layer_tree(node))
        if node.kind == "layer":
            act = menu.addAction(_icon("/mActionZoomToLayer.svg"), "Zoom to Layer")
            act.triggered.connect(lambda: self.zoom_to_layer(node))
        menu.addSeparator()
        key_a, key_b = self.combo_a.currentData(), self.combo_b.currentData()
        name_a, name_b = self._name(self.combo_a, 140), self._name(self.combo_b, 140)
        what = "{} items".format(len(nodes)) if len(nodes) > 1 else "'{}'".format(self._elide(node.name, 160))
        act = menu.addAction("Copy State of {} from {} to {}".format(what, name_a, name_b))
        act.setEnabled(key_b != tl.CURRENT_STATE and key_a != key_b)
        act.triggered.connect(lambda: self.copy_state(nodes, self.combo_a, self.combo_b))
        act = menu.addAction("Copy State of {} from {} to {}".format(what, name_b, name_a))
        act.setEnabled(key_a != tl.CURRENT_STATE and key_a != key_b)
        act.triggered.connect(lambda: self.copy_state(nodes, self.combo_b, self.combo_a))
        if tl.CURRENT_STATE in (key_a, key_b):
            hint = menu.addAction("(The current view can't be edited here)")
            hint.setEnabled(False)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _on_double_click(self, item, column):
        node = item.data(COL_NAME, Qt.ItemDataRole.UserRole)
        if node is not None:
            self.select_in_layer_tree(node)

    def select_in_layer_tree(self, node):
        if node.tree_node is None:
            return
        view = self.iface.layerTreeView()
        if node.kind == "layer":
            view.setCurrentLayer(node.layer)
        else:
            model = self._layer_tree_model()
            index = model.node2index(node.tree_node)
            if hasattr(view, "node2index"):  # proxy model in QGIS >= 3.18
                index = view.node2index(node.tree_node)
            view.setCurrentIndex(index)
        view.scrollTo(view.currentIndex())

    def zoom_to_layer(self, node):
        if node.layer is None:
            return
        canvas = self.iface.mapCanvas()
        extent = canvas.mapSettings().layerExtentToOutputExtent(node.layer, node.layer.extent())
        if extent.isNull():
            return
        if extent.isEmpty():  # e.g. a single point
            canvas.setCenter(extent.center())
        else:
            extent.scale(1.05)
            canvas.setExtent(extent)
        canvas.refresh()

    def copy_state(self, nodes, src_combo, dst_combo):
        src, dst = src_combo.currentData(), dst_combo.currentData()
        if dst == tl.CURRENT_STATE:
            return
        names = ", ".join(n.name for n in nodes[:5]) + (" …" if len(nodes) > 5 else "")
        src_label = "the current view" if src == tl.CURRENT_STATE else "theme '{}'".format(src)
        answer = QMessageBox.question(
            self, "Modify Theme",
            "Copy the state of {} from {} into theme '{}'?\n\nThis modifies the saved theme '{}'."
            .format(names, src_label, dst, dst))
        if answer != QMessageBox.StandardButton.Yes:
            return
        project = QgsProject.instance()
        try:
            count = tl.copy_node_state(project, project.layerTreeRoot(), self._layer_tree_model(),
                                       src, dst, nodes)
        except (KeyError, ValueError) as e:
            QMessageBox.warning(self, "Theme Compare", "Could not update the theme: {}".format(e))
            return
        project.setDirty(True)
        self.iface.messageBar().pushSuccess("Theme Compare", "Updated {} item(s) in theme '{}'".format(count, dst))
        self.refresh()

    # ------------------------------------------------------------ export

    def _rows(self, only_shown=True):
        """Rows for export: (path, name, type, state 1, state 2, difference)."""
        cats = self.categories()
        rows = []

        def emit(node):
            diffs = [t for c, t in node.differences if c in cats]
            rows.append([node.path, node.name, node.kind, tl.visibility_text(node.a),
                         tl.visibility_text(node.b), "; ".join(diffs) if diffs else "same"])

        if only_shown:
            def walk(item):
                for i in range(item.childCount()):
                    child = item.child(i)
                    emit(child.data(COL_NAME, Qt.ItemDataRole.UserRole))
                    walk(child)
            walk(self.tree.invisibleRootItem())
        elif self._result is not None:
            for node in self._result.iter_all():
                emit(node)
        return rows

    def _header(self):
        return ["Group", "Name", "Type",
                tl.display_name(self.combo_a.currentData()),
                tl.display_name(self.combo_b.currentData()),
                "Difference"]

    def copy_to_clipboard(self):
        rows = self._rows()
        lines = ["\t".join(self._header())] + ["\t".join(r) for r in rows]
        QGuiApplication.clipboard().setText("\n".join(lines))
        self.iface.messageBar().pushInfo("Theme Compare", "Copied {} row(s) to the clipboard".format(len(rows)))

    def export_csv(self):
        def safe(key):
            return "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in tl.display_name(key))
        a, b = safe(self.combo_a.currentData()), safe(self.combo_b.currentData())
        last_dir = QgsSettings().value(SETTINGS_PREFIX + "last_dir", "")
        filename = "theme_compare_{}_vs_{}.csv".format(a, b)
        default = os.path.join(last_dir, filename) if last_dir else filename
        path, _ = QFileDialog.getSaveFileName(self, "Export Comparison", default, "CSV files (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(self._header())
                writer.writerows(self._rows())
        except OSError as e:
            QMessageBox.warning(self, "Theme Compare", "Could not write file:\n{}".format(e))
            return
        QgsSettings().setValue(SETTINGS_PREFIX + "last_dir", os.path.dirname(path))
        self.iface.messageBar().pushSuccess("Theme Compare", "Exported comparison to {}".format(path))
