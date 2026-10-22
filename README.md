# Theme Compare (QGIS plugin)

Compare two map themes side by side and see at a glance which layers are
visible in one but not in the other. Either side can be the **current map
view**: no need to save it as a theme first.

## Install

Download `theme_compare.zip` from the [latest release](https://github.com/danzig666/qgis-theme-compare/releases/latest) and use *Plugins ▸ Manage and Install Plugins ▸ Install from ZIP*. Or copy the `theme_compare` folder into your QGIS profile's plugin folder, e.g.

- Windows: `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`
- Linux: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
- macOS: `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`

Then enable **Theme Compare** in the Plugin Manager.

Works with QGIS 3.16+ and QGIS 4 (Qt6).

## Use

Click the toolbar button (or *Plugins ▸ Theme Compare ▸ Compare Map Themes*).
A dock opens with two pickers, **A** and **B**. Each lists
*Current map view (unsaved)* plus every map theme of the project.

The tree mirrors the Layers panel:

| Column     | Meaning |
|------------|---------|
| Layer      | Layer or group name |
| A / B      | `Visible`, `Hidden`, or `Hidden (group)` (checked but a parent group is off) |
| Difference | What differs, or `Same` |

Row colors: red = visible only in A, blue = visible only in B, amber = other
difference (style, legend classes, check state, expanded state).

### Features

- **Visibility diff** that respects group check states (the real, rendered visibility).
- Optional comparison of **named styles**, **legend classes** (categories / rules switched on),
  **check state** and **expanded/collapsed state** (⚙ menu).
- **Only differences** filter and a text filter.
- **Swap** A and B; **apply** either theme to the map with one click.
- **Save the current map view as a theme** right from the dock.
- **Right click** a row (multi-select works) to show it in the Layers panel, zoom to it, or
  **copy its state from A to B** (or B to A) to reconcile two themes.
- **Copy to clipboard** (tab separated, pastes into Excel) or **export to CSV**.
- Refreshes automatically when themes or the Layers panel change (can be turned off).

## License

GPL-2.0-or-later, see [LICENSE](LICENSE).
