"""Pure comparison logic for map themes.

Nothing in here touches widgets, so it can be used (and tested) with any
QgsProject / layer tree / layer tree model combination.
"""

from qgis.PyQt.QtCore import Qt
from qgis.core import (
    QgsLayerTree,
    QgsLayerTreeModelLegendNode,
    QgsMapThemeCollection,
)

#: Combo box key used for "the current state of the Layers panel".
CURRENT_STATE = "__theme_compare_current_state__"

# Difference categories
DIFF_VISIBILITY = "visibility"   # layer/group is rendered in one theme only
DIFF_CHECKED = "checked"         # check box differs, but rendering does not
DIFF_STYLE = "style"             # different named style
DIFF_LEGEND = "legend"           # different legend classes switched on
DIFF_EXPANDED = "expanded"       # expanded / collapsed in the Layers panel

ALL_CATEGORIES = (DIFF_VISIBILITY, DIFF_CHECKED, DIFF_STYLE, DIFF_LEGEND, DIFF_EXPANDED)

CATEGORY_LABELS = {
    DIFF_VISIBILITY: "Visibility",
    DIFF_CHECKED: "Check state",
    DIFF_STYLE: "Style",
    DIFF_LEGEND: "Legend items",
    DIFF_EXPANDED: "Expanded state",
}


def _rule_key_role():
    role = getattr(QgsLayerTreeModelLegendNode, "RuleKeyRole", None)
    if role is None:  # scoped enum in newer bindings
        role = QgsLayerTreeModelLegendNode.CustomRole.RuleKey
    return int(role) if not hasattr(role, "value") else role.value


def legend_items(model, node_layer):
    """Return an ordered list of (rule_key, label) of checkable legend items."""
    if model is None or node_layer is None:
        return []
    role = _rule_key_role()
    items = []
    try:
        nodes = model.layerLegendNodes(node_layer, False)
    except TypeError:
        nodes = model.layerLegendNodes(node_layer)
    for legend_node in nodes:
        if not legend_node.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            continue
        key = legend_node.data(role)
        if not key:
            continue
        label = legend_node.data(Qt.ItemDataRole.DisplayRole) or key
        items.append((key, label))
    return items


def theme_record(project, theme_key, root, model):
    """Return a MapThemeRecord for a theme name or for CURRENT_STATE."""
    if theme_key == CURRENT_STATE:
        return QgsMapThemeCollection.createThemeFromCurrentState(root, model)
    collection = project.mapThemeCollection()
    if not collection.hasMapTheme(theme_key):
        return None
    return collection.mapThemeState(theme_key)


class NodeState:
    """State of one layer tree node inside one theme."""

    __slots__ = ("checked", "visible", "expanded", "style", "legend", "styles_available")

    def __init__(self):
        self.checked = False
        self.visible = False
        self.expanded = None     # None = unknown (theme has no info)
        self.style = None        # None = theme does not fix a style
        self.legend = None       # None = not a layer / no checkable items
        self.styles_available = 1


class DiffNode:
    """One row of the comparison result (a group or a layer)."""

    def __init__(self, kind, name, path, layer=None, tree_node=None):
        self.kind = kind            # "group" or "layer"
        self.name = name
        self.path = path            # "Group/Subgroup"
        self.layer = layer
        self.tree_node = tree_node
        self.a = NodeState()
        self.b = NodeState()
        self.differences = []       # list of (category, text)
        self.legend_labels = {}
        self.children = []

    @property
    def layer_id(self):
        return self.layer.id() if self.layer is not None else None

    def categories(self):
        return {cat for cat, _ in self.differences}

    def has_difference(self, categories):
        return bool(self.categories() & set(categories))

    def subtree_has_difference(self, categories):
        if self.has_difference(categories):
            return True
        return any(c.subtree_has_difference(categories) for c in self.children)

    def iter_all(self):
        for child in self.children:
            yield child
            yield from child.iter_all()


def _group_full_name(group):
    # QgsLayerTreeGroup.fullName() was only added in 3.24
    if hasattr(group, "fullName"):
        return group.fullName()
    parts = []
    node = group
    while node is not None and node.parent() is not None:
        parts.append(node.name())
        node = node.parent()
    return "/".join(reversed(parts))


class _RecordView:
    """Convenient lookups on a MapThemeRecord."""

    def __init__(self, record):
        self.record = record
        self.layers = {}
        if record is not None:
            for lr in record.layerRecords():
                layer = lr.layer()
                if layer is not None:
                    self.layers[layer.id()] = lr
        self.has_checked_info = record is not None and record.hasCheckedStateInfo()
        self.has_expanded_info = record is not None and record.hasExpandedStateInfo()
        self.checked_groups = set(record.checkedGroupNodes()) if self.has_checked_info else set()
        self.expanded_groups = set(record.expandedGroupNodes()) if self.has_expanded_info else set()

    def group_state(self, group, parent_visible):
        st = NodeState()
        full = _group_full_name(group)
        st.checked = full in self.checked_groups if self.has_checked_info else True
        st.visible = parent_visible and st.checked
        st.expanded = (full in self.expanded_groups) if self.has_expanded_info else None
        return st

    def layer_state(self, layer, parent_visible, all_legend_keys):
        st = NodeState()
        lr = self.layers.get(layer.id())
        st.styles_available = len(layer.styleManager().styles()) if layer.styleManager() else 1
        if lr is None:
            return st
        st.checked = bool(getattr(lr, "isVisible", True))
        st.visible = parent_visible and st.checked
        st.style = lr.currentStyle if lr.usingCurrentStyle else None
        if all_legend_keys:
            st.legend = frozenset(lr.checkedLegendItems) if lr.usingLegendItems else frozenset(all_legend_keys)
        if self.has_expanded_info:
            st.expanded = bool(lr.expandedLayerNode)
        return st


def _vis_word(state):
    if state.visible:
        return "visible"
    if state.checked:
        return "checked, but hidden by a parent group"
    return "hidden"


def _compare(node, label_a, label_b):
    a, b = node.a, node.b
    diffs = node.differences
    what = "Group" if node.kind == "group" else "Layer"

    if a.visible != b.visible:
        side = label_a if a.visible else label_b
        diffs.append((DIFF_VISIBILITY, "Visible only in {}".format(side)))
    elif a.checked != b.checked:
        diffs.append((DIFF_CHECKED, "Checked only in {} (hidden by group in both)".format(
            label_a if a.checked else label_b)))

    if node.kind == "layer" and a.checked and b.checked:
        if (a.style or b.style) and a.style != b.style:
            diffs.append((DIFF_STYLE, "Style: '{}' vs '{}'".format(
                a.style or "(not set)", b.style or "(not set)")))
        if a.legend is not None and b.legend is not None and a.legend != b.legend:
            only_a = [node.legend_labels.get(k, k) for k in a.legend - b.legend]
            only_b = [node.legend_labels.get(k, k) for k in b.legend - a.legend]
            parts = []
            if only_a:
                parts.append("on only in {}: {}".format(label_a, ", ".join(sorted(only_a))))
            if only_b:
                parts.append("on only in {}: {}".format(label_b, ", ".join(sorted(only_b))))
            diffs.append((DIFF_LEGEND, "Legend items " + "; ".join(parts)))

    if a.expanded is not None and b.expanded is not None and a.expanded != b.expanded:
        diffs.append((DIFF_EXPANDED, "{} expanded only in {}".format(
            what, label_a if a.expanded else label_b)))


def compare_themes(project, root, model, key_a, key_b, label_a="A", label_b="B"):
    """Compare two themes and return the root DiffNode (kind "root").

    key_a / key_b are theme names or CURRENT_STATE.
    Raises KeyError when a theme does not exist.
    """
    rec_a = theme_record(project, key_a, root, model)
    rec_b = theme_record(project, key_b, root, model)
    if rec_a is None:
        raise KeyError(key_a)
    if rec_b is None:
        raise KeyError(key_b)
    view_a = _RecordView(rec_a)
    view_b = _RecordView(rec_b)

    result = DiffNode("root", "", "")
    result.a.visible = result.b.visible = True

    def walk(group, out, path, vis_a, vis_b):
        for child in group.children():
            if QgsLayerTree.isGroup(child):
                full = _group_full_name(child)
                node = DiffNode("group", child.name(), path, tree_node=child)
                node.a = view_a.group_state(child, vis_a)
                node.b = view_b.group_state(child, vis_b)
                _compare(node, label_a, label_b)
                walk(child, node, full, node.a.visible, node.b.visible)
                out.children.append(node)
            elif QgsLayerTree.isLayer(child):
                layer = child.layer()
                if layer is None:
                    continue
                node = DiffNode("layer", child.name(), path, layer=layer, tree_node=child)
                items = legend_items(model, child)
                node.legend_labels = dict(items)
                keys = [k for k, _ in items]
                node.a = view_a.layer_state(layer, vis_a, keys)
                node.b = view_b.layer_state(layer, vis_b, keys)
                _compare(node, label_a, label_b)
                out.children.append(node)

    walk(root, result, "", True, True)
    return result


def summarize(result, categories):
    """Return a dict with counters used by the summary line."""
    counts = {
        "layers": 0,
        "only_a": 0,
        "only_b": 0,
        "same_visible": 0,
        "same_hidden": 0,
        "other": 0,
        "groups_diff": 0,
    }
    for node in result.iter_all():
        cats = node.categories() & set(categories)
        if node.kind == "group":
            if cats:
                counts["groups_diff"] += 1
            continue
        counts["layers"] += 1
        if DIFF_VISIBILITY in cats:
            if node.a.visible:
                counts["only_a"] += 1
            else:
                counts["only_b"] += 1
        else:
            if node.a.visible:
                counts["same_visible"] += 1
            else:
                counts["same_hidden"] += 1
            if cats:
                counts["other"] += 1
    return counts


def visibility_text(state):
    return _vis_word(state)


# ---------------------------------------------------------------------------
# Editing themes
# ---------------------------------------------------------------------------

def _all_group_names(root):
    names = []
    for group in root.findGroups(True):
        names.append(_group_full_name(group))
    return names


def copy_node_state(project, root, model, source_key, target_theme, nodes):
    """Copy the state of the given DiffNodes from source theme to target theme.

    For layers the full layer record (check state, style, legend items,
    expanded state) is copied; for groups the check (and expanded) state.
    Returns the number of nodes updated.
    """
    if target_theme == CURRENT_STATE:
        raise ValueError("The current map view cannot be modified")
    collection = project.mapThemeCollection()
    src = theme_record(project, source_key, root, model)
    dst = theme_record(project, target_theme, root, model)
    if src is None or dst is None:
        raise KeyError(source_key if src is None else target_theme)
    src_view = _RecordView(src)

    checked_groups = set(dst.checkedGroupNodes()) if dst.hasCheckedStateInfo() else set(_all_group_names(root))
    expanded_groups = set(dst.expandedGroupNodes()) if dst.hasExpandedStateInfo() else None
    groups_touched = False
    count = 0

    for node in nodes:
        if node.kind == "layer" and node.layer is not None:
            dst.removeLayerRecord(node.layer)
            src_lr = src_view.layers.get(node.layer.id())
            if src_lr is not None:
                dst.addLayerRecord(src_lr)
            count += 1
        elif node.kind == "group" and node.tree_node is not None:
            full = _group_full_name(node.tree_node)
            src_checked = full in src_view.checked_groups if src_view.has_checked_info else True
            if src_checked:
                checked_groups.add(full)
            else:
                checked_groups.discard(full)
            if expanded_groups is not None and src_view.has_expanded_info:
                if full in src_view.expanded_groups:
                    expanded_groups.add(full)
                else:
                    expanded_groups.discard(full)
            groups_touched = True
            count += 1

    if groups_touched:
        dst.setHasCheckedStateInfo(True)
        dst.setCheckedGroupNodes(checked_groups)
        if expanded_groups is not None:
            dst.setExpandedGroupNodes(expanded_groups)
    collection.update(target_theme, dst)
    return count
