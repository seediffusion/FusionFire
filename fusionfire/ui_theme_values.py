"""The theme names, kept away from wx.

``config`` validates the setting and must stay importable without a
display, so the list of legal values lives here rather than in
``fusionfire.ui.theme`` which imports wx.
"""

THEME_MODES = ("system", "light", "dark")
