def classFactory(iface):
    from .plugin import ThemeComparePlugin
    return ThemeComparePlugin(iface)
