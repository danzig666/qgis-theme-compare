import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .compare_dock import ThemeCompareDock

PLUGIN_NAME = "Theme Compare"


class ThemeComparePlugin:

    def __init__(self, iface):
        self.iface = iface
        self.dock = None
        self.action = None

    def initGui(self):
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.svg"))
        self.action = QAction(icon, "Compare Map Themes", self.iface.mainWindow())
        self.action.setObjectName("ThemeCompareAction")
        self.action.setToolTip("Compare the layer visibility (and more) of two map themes")
        self.action.setCheckable(True)
        self.action.toggled.connect(self._toggle_dock)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu(PLUGIN_NAME, self.action)

    def _ensure_dock(self):
        if self.dock is None:
            self.dock = ThemeCompareDock(self.iface, self.iface.mainWindow())
            self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
            self.dock.visibilityChanged.connect(self._on_dock_visibility)
        return self.dock

    def _toggle_dock(self, checked):
        if checked:
            dock = self._ensure_dock()
            dock.show()
            dock.raise_()
            dock.refresh()
        elif self.dock is not None:
            self.dock.hide()

    def _on_dock_visibility(self, visible):
        # Keep the toolbar button in sync when the dock is closed with its X button.
        # Ignore "hidden" while it is merely a non-current tab of a tabified dock area.
        if not visible and self.dock is not None and not self.dock.isHidden():
            return
        self.action.blockSignals(True)
        self.action.setChecked(visible)
        self.action.blockSignals(False)

    def unload(self):
        if self.dock is not None:
            self.dock.disconnect_all()
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        if self.action is not None:
            self.iface.removePluginMenu(PLUGIN_NAME, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action.deleteLater()
            self.action = None
