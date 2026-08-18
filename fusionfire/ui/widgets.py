"""Small accessible building blocks shared by the dialogs and panels."""

from __future__ import annotations

import wx


def field_label(parent: wx.Window, text: str) -> wx.StaticText:
    """Create the label for a field. **Call this before creating the control.**

    A tooltip is not a label, and neither is ``SetName``. On Windows a
    control's accessible name is derived by the screen reader from the static
    text that precedes it in *z-order*, and native z-order follows creation
    order. So the label window has to exist before the control it names,
    regardless of where the sizer later puts it on screen.

    Getting this backwards does not fail loudly: every control simply picks
    up the previous field's label, and the whole dialog reads one field out
    of step. Building the label first is the entire fix, which is why it is
    a separate call at the call site rather than something :func:`stack`
    could quietly do for you -- by the time ``stack`` runs, the control has
    already been constructed and the z-order is settled.

    Not every control type performs that lookup; see :func:`announce_label`
    for the ones that have to be told their name outright. The ordering rule
    holds regardless, so it is enforced for all of them.
    """
    return wx.StaticText(parent, label=text)


class _NamedByItsLabel(wx.Accessible):
    """Makes a control report its field label as its accessible name.

    Windows lets a control answer for itself through MSAA, and that answer
    beats anything a screen reader would otherwise infer from the layout.
    Attaching one of these is how a control stops depending on the guesswork
    described in :func:`announce_label`.

    Only :meth:`GetName` is overridden. Every other query -- role, state,
    value, child count, selection -- is left to :class:`wx.Accessible`, whose
    default answer is ``wx.ACC_NOT_IMPLEMENTED``, which wx passes to Windows
    as "no opinion, ask the control". That fallback is the entire reason this
    class is three lines long: a slider that announced its name but stopped
    announcing its percentage would be a worse bug than the unnamed slider it
    replaced.

    The label text is read at question time rather than copied in, so the
    name a screen reader hears cannot drift from the text on screen if a
    dialog ever relabels a field.
    """

    def __init__(self, label: wx.StaticText) -> None:
        super().__init__()
        self._label = label

    def GetName(self, childId):
        # Only the control itself gets renamed. A control's *parts* are asked
        # for their names through this same method, with the part number as
        # childId: answering the field label for those would rename a
        # slider's thumb and both its page areas to "Sound volume:", and
        # would rename every row of a list box to the list's own label.
        if childId != wx.ACC_SELF:
            return (wx.ACC_NOT_IMPLEMENTED, "")
        try:
            return (wx.ACC_OK, self._label.GetLabelText())
        except RuntimeError:  # the label window has already been destroyed
            return (wx.ACC_NOT_IMPLEMENTED, "")


#: Control types that do not adopt the static text before them, and so have
#: to be told their own name. Everything else in this application -- edits,
#: rich edits, combo boxes, list boxes -- already reports the label correctly.
#:
#: This list is deliberately short, and widening it is not free. Attaching a
#: wx.Accessible replaces the whole native accessible object, not just its
#: name: wx answers the queries it implements and hands the rest to
#: CreateStdAccessibleObject, which is *not* always the proxy Windows would
#: otherwise have used. On the rich edit built by review_box() the substitute
#: is markedly worse -- the statistics box goes from role "text" with its
#: contents readable as a value, to role "client" with no value at all. So a
#: control is named this way only where doing so gains something. Slider and
#: the spin controls lose nothing measurable; their role, value, state and
#: children come back identical either way.
#:
#: Nothing here has to be kept in step by hand: the test suite walks every
#: field of every dialog and asserts that what Windows announces matches the
#: label beside it, whichever mechanism supplies it. A control type added
#: later that does not name itself fails that test rather than reaching a
#: player.
_STATES_ITS_OWN_NAME = (wx.Slider, wx.SpinCtrl, wx.SpinCtrlDouble, wx.SpinButton)


def announce_label(control: wx.Window, label: wx.StaticText) -> None:
    """Make ``control`` tell Windows that ``label`` is its name.

    Placing a static text before a control is enough for an edit, a combo
    box or a list box: the oleacc proxy behind those native controls looks
    backwards through the z-order and adopts the text it finds. A trackbar
    does no such lookup. Left to itself a slider answers the name query with
    its own position, so the settings dialog announced "95, slider" and gave
    no clue which of the two volumes it was -- which is what reached us as
    the sliders being unlabelled. A spin button answers with nothing at all.

    For those, the label is stated outright rather than left to be inferred.
    """
    accessible = _NamedByItsLabel(label)
    control.SetAccessible(accessible)
    # wx owns the C++ half of that object, but the Python half has to outlive
    # this function or it can be collected out from under the wrapper. Park it
    # on the control it names, so it lives exactly as long as it is needed.
    control._label_accessible = accessible


def stack(label: wx.StaticText, control: wx.Window) -> wx.BoxSizer:
    """Lay an already-created ``label`` above the ``control`` it names.

    The label must have been made by :func:`field_label` *before* ``control``
    was constructed; this checks that and refuses to build the pair if not,
    so the off-by-one cannot creep back in unnoticed.

    Creation order settles what the platform can *infer*; for the control
    types that infer nothing, :func:`announce_label` states the name outright.
    Both are needed, and the z-order rule stays checked here regardless.
    """
    _require_label_first(label, control)
    # Not what a screen reader reads. This is here so the control identifies
    # itself in logs, inspectors and UI automation dumps.
    control.SetName(label.GetLabel().replace("&", "").rstrip(":"))
    # Remembered on every control, named or not, so the accessibility tests
    # can find each field and check what Windows really announces for it.
    control._field_label = label
    if isinstance(control, _STATES_ITS_OWN_NAME):
        announce_label(control, label)

    sizer = wx.BoxSizer(wx.VERTICAL)
    sizer.Add(label, 0, wx.BOTTOM, 2)
    sizer.Add(control, 0, wx.EXPAND)
    return sizer


def _require_label_first(label: wx.StaticText, control: wx.Window) -> None:
    """Raise if ``label`` was created after ``control`` under the same parent.

    ``GetChildren`` is in creation order, which on MSW is the z-order a
    screen reader walks to find a control's label.
    """
    parent = control.GetParent()
    if parent is None or label.GetParent() is not parent:
        return
    children = list(parent.GetChildren())
    try:
        label_at, control_at = children.index(label), children.index(control)
    except ValueError:  # one of them has been reparented; nothing to check
        return
    if label_at > control_at:
        raise ValueError(
            f"The label {label.GetLabel()!r} was created after the control it "
            f"names ({type(control).__name__}), so a screen reader will read "
            "it against the previous field instead. Call field_label() before "
            "constructing the control."
        )


def review_box(parent: wx.Window, name: str = "Transcript") -> wx.TextCtrl:
    """A read-only multiline box holding everything that has been announced.

    Audio games lose information the moment it is spoken. A focusable,
    arrow-navigable transcript means a player can go back and read a status
    line they missed rather than losing the thread of the match.
    """
    box = wx.TextCtrl(
        parent,
        style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2 | wx.TE_DONTWRAP,
    )
    box.SetName(name)
    return box


def append_line(box: wx.TextCtrl, text: str) -> None:
    """Append a transcript line without stealing the caret from the reader."""
    if not box or not text:
        return
    try:
        at_end = box.GetInsertionPoint() >= box.GetLastPosition() - 1
        box.AppendText(("" if box.IsEmpty() else "\n") + text)
        if at_end:
            box.SetInsertionPointEnd()
    except RuntimeError:
        pass  # window already destroyed


def message(parent: wx.Window | None, text: str, caption: str, style: int = wx.OK) -> int:
    """A modal message box that always names itself in the title bar."""
    dialog = wx.MessageDialog(parent, text, caption, style | wx.CENTRE)
    try:
        return dialog.ShowModal()
    finally:
        dialog.Destroy()


def confirm(parent: wx.Window | None, text: str, caption: str) -> bool:
    return message(parent, text, caption, wx.YES_NO | wx.ICON_QUESTION) == wx.ID_YES


class ScrollFreeListBox(wx.ListBox):
    """A list box that reports its selection to the screen reader on change.

    wx announces selection changes on Windows already; this exists so the
    caller has one place to hook extra announcements (a sound, a longer
    description) without repeating the binding everywhere.
    """

    def __init__(self, parent: wx.Window, choices: list[str], name: str, on_change=None):
        super().__init__(parent, choices=choices, style=wx.LB_SINGLE)
        self.SetName(name)
        self._on_change = on_change
        if choices:
            self.SetSelection(0)
        self.Bind(wx.EVT_LISTBOX, self._changed)

    def _changed(self, event: wx.CommandEvent) -> None:
        if self._on_change is not None:
            index = self.GetSelection()
            if index != wx.NOT_FOUND:
                self._on_change(index)
        event.Skip()
