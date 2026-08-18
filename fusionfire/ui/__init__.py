"""wxPython interface.

Design rules, all of them consequences of this being a game for blind
players:

* **Standard controls only.** Screen readers already know how to read a
  ``wx.ListBox``, a ``wx.Button`` and a ``wx.TextCtrl``. Custom-drawn
  widgets would need custom accessibility work to reach the same place.
* **Every control has a label.** Not a tooltip, a label — a
  ``wx.StaticText`` bound to the control it names, so the reader announces
  it on focus.
* **Focus is always placed deliberately.** Every dialog and panel puts focus
  on the control the player needs next, because a player who cannot see the
  window has no way to hunt for it.
* **Nothing is conveyed by colour or position alone.** The game is played by
  ear, so anything the eye could pick up is also spoken.
"""
