"""Character setup, and the opponent picker.

The naming flow keeps the original's manners: type a full name and it is
yours, type one word and the machine takes the mickey before naming you
itself, leave it blank and it names you quietly. Gender is a real choice
because it selects which set of recorded reaction sounds voices your
character.
"""

from __future__ import annotations

import wx

from ..game import names
from ..game.difficulty import ordered
from .dialog_base import finish_dialog
from .widgets import field_label, stack


class SetupDialog(wx.Dialog):
    """Ask for a name and a gender before the first match."""

    def __init__(self, parent: wx.Window, settings) -> None:
        super().__init__(parent, title="Who are you?", style=wx.DEFAULT_DIALOG_STYLE)
        self.settings = settings
        self.choice: names.NameChoice | None = None

        panel = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)

        intro = wx.StaticText(
            panel,
            label=(
                "First and last name. Type one word and the machine names "
                "you, with commentary. Leave it empty and it names you "
                "quietly."
            ),
        )
        intro.Wrap(430)
        outer.Add(intro, 0, wx.ALL, 10)

        name_label = field_label(panel, "Your name:")
        self.name_field = wx.TextCtrl(panel, value=settings.player_name, size=(320, -1))
        self.name_field.SetMaxLength(40)
        outer.Add(stack(name_label, self.name_field), 0, wx.ALL | wx.EXPAND, 10)

        gender_label = field_label(panel, "Your character's voice:")
        self.gender_choice = wx.Choice(panel, choices=["Male", "Female"])
        self.gender_choice.SetSelection(1 if settings.player_gender == "female" else 0)
        outer.Add(stack(gender_label, self.gender_choice), 0, wx.ALL | wx.EXPAND, 10)

        note = wx.StaticText(
            panel,
            label=(
                f"There are {names.combinations('male'):,} male and "
                f"{names.combinations('female'):,} female name combinations, "
                "so a repeat is unlikely."
            ),
        )
        note.Wrap(430)
        outer.Add(note, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        finish_dialog(self, panel, outer, focus=self.name_field)
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)

    def _on_ok(self, event: wx.CommandEvent) -> None:
        gender = "female" if self.gender_choice.GetSelection() == 1 else "male"
        self.choice = names.decide(self.name_field.GetValue(), gender)
        event.Skip()

    def apply_to(self, settings) -> None:
        """Persist whatever was decided, so the next launch remembers it."""
        if self.choice is None:
            return
        settings.player_name = self.choice.name
        settings.player_gender = self.choice.gender


class OpponentDialog(wx.Dialog):
    """Pick which terrible computer you are fighting."""

    def __init__(self, parent: wx.Window, settings) -> None:
        super().__init__(parent, title="Choose your opponent", style=wx.DEFAULT_DIALOG_STYLE)
        self.settings = settings
        self._levels = ordered()
        self.selected = None

        panel = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)

        difficulty_label = field_label(panel, "Difficulty:")
        self.list = wx.ListBox(
            panel,
            choices=[level.label for level in self._levels],
            style=wx.LB_SINGLE,
            size=(320, 170),
        )
        current = next(
            (i for i, level in enumerate(self._levels) if level.key == settings.difficulty), 2
        )
        self.list.SetSelection(current)
        outer.Add(stack(difficulty_label, self.list), 0, wx.ALL | wx.EXPAND, 10)

        description_label = field_label(panel, "What this opponent does:")
        self.description = wx.TextCtrl(
            panel,
            value=self._levels[current].description,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_NO_VSCROLL,
            size=(320, 70),
        )
        outer.Add(stack(description_label, self.description), 0, wx.ALL | wx.EXPAND, 10)

        self.power_weapon = wx.CheckBox(panel, label="Bring the &power weapon into this match")
        self.power_weapon.SetValue(settings.use_power_weapon)
        outer.Add(self.power_weapon, 0, wx.ALL, 10)

        hint = wx.StaticText(
            panel,
            label=(
                "Two minutes to charge, then usable for three. It can "
                "backfire onto you."
            ),
        )
        hint.Wrap(430)
        outer.Add(hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        finish_dialog(self, panel, outer, focus=self.list)
        self.list.Bind(wx.EVT_LISTBOX, self._on_select)
        self.list.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self.EndModal(wx.ID_OK))
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)

    def _on_select(self, event: wx.CommandEvent) -> None:
        index = self.list.GetSelection()
        if index == wx.NOT_FOUND:
            return
        level = self._levels[index]
        self.description.SetValue(level.description)
        event.Skip()

    def _on_ok(self, event: wx.CommandEvent) -> None:
        index = self.list.GetSelection()
        self.selected = self._levels[index if index != wx.NOT_FOUND else 2]
        self.settings.difficulty = self.selected.key
        self.settings.use_power_weapon = self.power_weapon.GetValue()
        event.Skip()
