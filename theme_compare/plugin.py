import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .compare_dialog import ThemeCompareDialog

PLUGIN_NAME = "Theme Compare"


class ThemeComparePlugin:

    def __init__(self, iface):
        self.iface = iface
        self.dialog = None
        self.action = None

    def initGui(self):
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.svg"))
        self.action = QAction(icon, "Compare Map Themes", self.iface.mainWindow())
        self.action.setObjectName("ThemeCompareAction")
        self.action.setToolTip("Compare the layer visibility (and more) of two map themes")
        self.action.setCheckable(True)
        self.action.toggled.connect(self._toggle_dialog)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu(PLUGIN_NAME, self.action)

    def _ensure_dialog(self):
        if self.dialog is None:
            self.dialog = ThemeCompareDialog(self.iface, self.iface.mainWindow())
            self.dialog.finished.connect(self._on_dialog_closed)
        return self.dialog

    def _toggle_dialog(self, checked):
        if checked:
            dialog = self._ensure_dialog()
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
            dialog.refresh()
        elif self.dialog is not None:
            self.dialog.hide()

    def _on_dialog_closed(self, *args):
        # Keep the toolbar button in sync when the window is closed with its X button or Esc
        self.action.blockSignals(True)
        self.action.setChecked(False)
        self.action.blockSignals(False)

    def unload(self):
        if self.dialog is not None:
            self.dialog.disconnect_all()
            self.dialog.hide()
            self.dialog.deleteLater()
            self.dialog = None
        if self.action is not None:
            self.iface.removePluginMenu(PLUGIN_NAME, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action.deleteLater()
            self.action = None
