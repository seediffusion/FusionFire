"""Settings.

Four tabs, all keyboard-reachable, every control labelled. The audio and
speech tabs both offer a test button, because the only way to know whether
an output device or a speech backend is the right one is to hear it.
"""

from __future__ import annotations

import wx

from ..audio import NULL_HANDLE
from ..game.difficulty import ordered
from ..input.actions import ACTION_LABELS, BINDABLE, Action
from .dialog_base import finish_dialog
from .theme import available_modes
from .widgets import field_label, message, stack

class SettingsDialog(wx.Dialog):
    def __init__(self, parent: wx.Window, ctx) -> None:
        super().__init__(
            parent,
            title="Fusion Fire settings",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
            size=(520, 520),
        )
        self.ctx = ctx
        self.settings = ctx.settings
        #: What was playing when the dialog opened, so Cancel can restore it
        #: after the test button has switched devices.
        self._device_on_open = ctx.audio.current_device_name()

        panel = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)
        self.notebook = wx.Notebook(panel)
        self.notebook.AddPage(self._audio_page(), "Audio")
        self.notebook.AddPage(self._speech_page(), "Speech")
        self.notebook.AddPage(self._game_page(), "Game")
        self.notebook.AddPage(self._pad_page(), "Controller")
        self.notebook.AddPage(self._online_page(), "Online")
        outer.Add(self.notebook, 1, wx.ALL | wx.EXPAND, 8)

        finish_dialog(self, panel, outer, focus=self.notebook, resizable=True)
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)
        self.Bind(wx.EVT_BUTTON, self._on_cancel, id=wx.ID_CANCEL)

    # ------------------------------------------------------------------
    def _audio_page(self) -> wx.Panel:
        page = wx.Panel(self.notebook)
        sizer = wx.BoxSizer(wx.VERTICAL)

        device_label = field_label(page, "Output device:")
        self._device_names = self.ctx.audio.device_names()
        self.device = wx.Choice(page, choices=self._device_names)
        # Select by name. Positions move about when hardware is plugged in,
        # so an index remembered from last time can point at a stranger.
        current = self.ctx.audio.current_device_name()
        self.device.SetSelection(
            self._device_names.index(current) if current in self._device_names else 0
        )
        sizer.Add(stack(device_label, self.device), 0, wx.ALL | wx.EXPAND, 8)

        sound_label = field_label(page, "Sound volume (Home and End in game):")
        self.sound_slider = wx.Slider(
            page, value=int(self.settings.sound_volume * 100), minValue=0, maxValue=100,
            style=wx.SL_HORIZONTAL | wx.SL_LABELS,
        )
        sizer.Add(stack(sound_label, self.sound_slider), 0, wx.ALL | wx.EXPAND, 8)

        music_label = field_label(page, "Music volume (Page Up and Page Down in game):")
        self.music_slider = wx.Slider(
            page, value=int(self.settings.music_volume * 100), minValue=0, maxValue=100,
            style=wx.SL_HORIZONTAL | wx.SL_LABELS,
        )
        sizer.Add(stack(music_label, self.music_slider), 0, wx.ALL | wx.EXPAND, 8)

        self.music_on = wx.CheckBox(page, label="Play background &music")
        self.music_on.SetValue(self.settings.music_enabled)
        sizer.Add(self.music_on, 0, wx.ALL, 8)

        self.screams_on = wx.CheckBox(page, label="Play reaction &screams")
        self.screams_on.SetValue(self.settings.screams_enabled)
        sizer.Add(self.screams_on, 0, wx.ALL, 8)

        self.intro_on = wx.CheckBox(page, label="&Play the intro music on launch")
        self.intro_on.SetValue(self.settings.play_intro_music)
        sizer.Add(self.intro_on, 0, wx.ALL, 8)

        intro_hint = wx.StaticText(
            page,
            label=(
                "The intro music plays instead of the spoken ready message "
                "when the game starts. The first Enter on the main menu "
                "skips it."
            ),
        )
        intro_hint.Wrap(440)
        sizer.Add(intro_hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        test = wx.Button(page, label="&Test this device")
        test.Bind(wx.EVT_BUTTON, self._test_audio)
        sizer.Add(test, 0, wx.ALL, 8)

        hint = wx.StaticText(
            page,
            label=(
                "Testing switches to the device and plays a gunshot through "
                "it. Cancel puts the old device back."
            ),
        )
        hint.Wrap(440)
        sizer.Add(hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        page.SetSizer(sizer)
        return page

    def _speech_page(self) -> wx.Panel:
        page = wx.Panel(self.notebook)
        sizer = wx.BoxSizer(wx.VERTICAL)

        backend_label = field_label(page, "Speech output:")
        self._backends = self.ctx.speech.list_backends()
        self.backend = wx.Choice(page, choices=[label for _, label in self._backends])
        current = next(
            (i for i, (token, _) in enumerate(self._backends)
             if token == self.settings.speech_backend), 0,
        )
        self.backend.SetSelection(current)
        sizer.Add(stack(backend_label, self.backend), 0, wx.ALL | wx.EXPAND, 8)

        detected = wx.StaticText(page, label=f"Currently using: {self.ctx.speech.backend_name}")
        sizer.Add(detected, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        # Voice, rate and pitch. A screen reader owns these -- NVDA reports
        # it cannot set any of them, because the user configured them in NVDA
        # and the game has no business overriding them there. The platform
        # voices are the opposite: OneCore and SAPI expose all three, and
        # somebody playing without a screen reader has nowhere else to set
        # them. So the controls follow whichever backend is highlighted.
        voice_label = field_label(page, "Voice:")
        self.voice = wx.Choice(page, choices=[])
        sizer.Add(stack(voice_label, self.voice), 0, wx.ALL | wx.EXPAND, 8)

        rate_label = field_label(page, "Speech rate, percent:")
        self.rate_slider = wx.Slider(
            page, value=50, minValue=0, maxValue=100,
            style=wx.SL_HORIZONTAL | wx.SL_LABELS,
        )
        sizer.Add(stack(rate_label, self.rate_slider), 0, wx.ALL | wx.EXPAND, 8)

        pitch_label = field_label(page, "Pitch, percent:")
        self.pitch_slider = wx.Slider(
            page, value=50, minValue=0, maxValue=100,
            style=wx.SL_HORIZONTAL | wx.SL_LABELS,
        )
        sizer.Add(stack(pitch_label, self.pitch_slider), 0, wx.ALL | wx.EXPAND, 8)

        self.voice_note = wx.StaticText(page, label="")
        self.voice_note.Wrap(440)
        sizer.Add(self.voice_note, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self.backend.Bind(wx.EVT_CHOICE, lambda e: self._sync_voice_controls())

        self.speak_status = wx.CheckBox(page, label="&Speak status messages")
        self.speak_status.SetValue(self.settings.speak_status)
        sizer.Add(self.speak_status, 0, wx.ALL, 8)

        self.braille_status = wx.CheckBox(page, label="Send status messages to &braille")
        self.braille_status.SetValue(self.settings.braille_status)
        sizer.Add(self.braille_status, 0, wx.ALL, 8)

        note = wx.StaticText(
            page,
            label=(
                "Automatic picks whichever screen reader is running, and falls "
                "back to the system voice if none is. Changing this takes "
                "effect as soon as you press OK."
            ),
        )
        note.Wrap(440)
        sizer.Add(note, 0, wx.ALL, 8)

        test = wx.Button(page, label="&Test speech")
        test.Bind(wx.EVT_BUTTON, self._test_speech)
        sizer.Add(test, 0, wx.ALL, 8)

        page.SetSizer(sizer)
        self._sync_voice_controls()
        return page

    # ------------------------------------------------------------------
    def _selected_backend(self) -> str:
        index = self.backend.GetSelection()
        if 0 <= index < len(self._backends):
            return self._backends[index][0]
        return "auto"

    def _sync_voice_controls(self) -> None:
        """Show what the highlighted backend can actually do.

        Disabled rather than hidden: a control that vanishes as you arrow
        through a list is disorienting, and "Voice, unavailable" tells a
        screen reader user why they cannot set it, which an absent control
        does not.
        """
        token = self._selected_backend()
        offers = self.ctx.speech.describe(token)
        live = token == self.settings.speech_backend

        names = offers["voices"]
        self.voice.Set(names or ["Whatever the backend uses"])
        chosen = self.settings.speech_voice
        self.voice.SetSelection(names.index(chosen) if chosen in names else 0)
        self.voice.Enable(bool(offers["voice"] and names))

        # The slider is always a plain 0 to 100. What that maps onto
        # depends on the backend -- see PITCH_RANGES in fusionfire.speech --
        # and the speech layer does the mapping, so every position here
        # moves the voice.
        self.pitch_slider.SetRange(0, 100)
        raises_only = self.ctx.speech.raises_pitch_only(token)

        for slider, what in ((self.rate_slider, "rate"), (self.pitch_slider, "pitch")):
            slider.Enable(bool(offers[what]))
            stored = getattr(self.settings, f"speech_{what}")
            if stored >= 0:
                slider.SetValue(int(stored))
            elif what == "pitch" and raises_only:
                # OneCore's floor is normal pitch, and 0% is that floor. The
                # generic middle would send 0.755 to Prism and raise the voice
                # the moment the player pressed OK, so for these backends the
                # untouched default is the bottom of the slider, not the
                # middle.
                slider.SetValue(0)
            elif live:
                # Never set. Open where the voice actually is rather than at
                # an arbitrary middle, so leaving it alone changes nothing.
                current = self.ctx.speech.current_value(what)
                slider.SetValue(int(current) if current >= 0 else 50)

        if offers["voice"] or offers["rate"] or offers["pitch"]:
            note = "This voice is the game's own, so you can set it here."
            if raises_only:
                note += (
                    " This voice can only be pitched up, not down, so 0 "
                    "percent is its normal pitch and 100 percent is as high "
                    "as it goes."
                )
            self.voice_note.SetLabel(note)
        else:
            self.voice_note.SetLabel(
                "Your screen reader owns its voice, rate and pitch. Change "
                "them in the screen reader itself and the game will follow."
            )
        self.voice_note.Wrap(440)
        self.voice_note.GetParent().Layout()

    def _test_speech(self, event: wx.CommandEvent) -> None:
        """Apply what is on screen, then speak through the highlighted backend.

        Spoken with :meth:`Speech.speak` rather than :meth:`Speech.report`,
        because report is silent when "Speak status messages" is off -- and
        that is exactly the player most likely to be here. They turned the
        game's spoken status lines off (often because the screen reader reads
        them anyway) but still need to hear whether a backend works.
        """
        self._apply_speech_settings()
        self.ctx.speech.reload()
        if not self.ctx.speech.available:
            message(self, "No speech backend could be opened.", "Speech output")
            return
        self.ctx.speech.speak(
            f"Speaking through {self.ctx.speech.backend_name}. "
            "Fusion Fire speech test. One, two, three."
        )

    def _apply_speech_settings(self) -> None:
        s = self.settings
        s.speech_backend = self._selected_backend()
        s.speak_status = self.speak_status.GetValue()
        s.braille_status = self.braille_status.GetValue()

        if self.voice.IsEnabled():
            s.speech_voice = self.voice.GetStringSelection()
        # A disabled slider means the backend cannot set that value, so
        # leave the sentinel rather than writing a number that would be
        # applied the moment the player switched to a backend that can.
        s.speech_rate = self.rate_slider.GetValue() if self.rate_slider.IsEnabled() else -1.0
        s.speech_pitch = (
            self.pitch_slider.GetValue() if self.pitch_slider.IsEnabled() else -1.0
        )

    def _game_page(self) -> wx.Panel:
        page = wx.Panel(self.notebook)
        sizer = wx.BoxSizer(wx.VERTICAL)

        difficulty_label = field_label(page, "Default difficulty:")
        self._levels = ordered()
        self.difficulty = wx.Choice(page, choices=[level.label for level in self._levels])
        current = next(
            (i for i, level in enumerate(self._levels)
             if level.key == self.settings.difficulty), 2,
        )
        self.difficulty.SetSelection(current)
        sizer.Add(stack(difficulty_label, self.difficulty), 0, wx.ALL | wx.EXPAND, 8)

        name_label = field_label(page, "Your name:")
        self.name_field = wx.TextCtrl(page, value=self.settings.player_name)
        self.name_field.SetMaxLength(40)
        sizer.Add(stack(name_label, self.name_field), 0, wx.ALL | wx.EXPAND, 8)

        gender_label = field_label(page, "Your character's voice:")
        self.gender = wx.Choice(page, choices=["Male", "Female"])
        self.gender.SetSelection(1 if self.settings.player_gender == "female" else 0)
        sizer.Add(stack(gender_label, self.gender), 0, wx.ALL | wx.EXPAND, 8)

        birthday_label = field_label(
            page, "Birthday as month and day, for example 03-14 (optional):"
        )
        self.birthday = wx.TextCtrl(page, value=self.settings.birthday)
        self.birthday.SetMaxLength(5)
        sizer.Add(stack(birthday_label, self.birthday), 0, wx.ALL | wx.EXPAND, 8)
        privacy = wx.StaticText(page, label="Only the month and day are stored. Never the year.")
        sizer.Add(privacy, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        theme_label = field_label(page, "Appearance:")
        self._themes = available_modes()
        self.theme = wx.Choice(page, choices=[label for _, label in self._themes])
        current_theme = next(
            (i for i, (key, _) in enumerate(self._themes)
             if key == self.settings.theme), 0,
        )
        self.theme.SetSelection(current_theme)
        sizer.Add(stack(theme_label, self.theme), 0, wx.ALL | wx.EXPAND, 8)

        theme_hint = wx.StaticText(
            page,
            label=(
                "Windows 10 and 11 have a light and dark setting, and the "
                "game follows it as soon as you change it. Windows 8.1 and 7 "
                "have no such setting, so the choice is made here instead."
            ),
        )
        theme_hint.Wrap(440)
        sizer.Add(theme_hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self.stats_on = wx.CheckBox(page, label="Record &statistics")
        self.stats_on.SetValue(self.settings.stats_enabled)
        sizer.Add(self.stats_on, 0, wx.ALL, 8)

        self.power_weapon_on = wx.CheckBox(page, label="Bring the po&wer weapon by default")
        self.power_weapon_on.SetValue(self.settings.use_power_weapon)
        sizer.Add(self.power_weapon_on, 0, wx.ALL, 8)

        page.SetSizer(sizer)
        return page

    def _online_page(self) -> wx.Panel:
        page = wx.Panel(self.notebook)
        sizer = wx.BoxSizer(wx.VERTICAL)

        spy_label = field_label(page, "Relay spy service (web address):")
        self.spy_url = wx.TextCtrl(page, value=self.settings.relay_spy_url, size=(320, -1))
        self.spy_url.SetMaxLength(512)
        sizer.Add(stack(spy_label, self.spy_url), 0, wx.ALL | wx.EXPAND, 8)

        spy_hint = wx.StaticText(
            page,
            label=(
                "Optional. A relay spy service publishes a list of relay "
                "servers that have chosen to publicize themselves, so the "
                "online dialog can offer them as a pickable list. Leave this "
                "empty and you can still type any relay server's name or "
                "address by hand."
            ),
        )
        spy_hint.Wrap(440)
        sizer.Add(spy_hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        page.SetSizer(sizer)
        return page

    def _pad_page(self) -> wx.Panel:
        page = wx.Panel(self.notebook)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.pad_on = wx.CheckBox(page, label="&Enable controllers and gamepads")
        self.pad_on.SetValue(self.settings.gamepad_enabled)
        sizer.Add(self.pad_on, 0, wx.ALL, 8)

        pads = self.ctx.gamepad.device_names if self.ctx.gamepad else []
        found = ", ".join(pads) if pads else "none detected"
        self.pad_status = wx.StaticText(page, label=f"Connected: {found}")
        sizer.Add(self.pad_status, 0, wx.ALL, 8)

        self.pad_vibration = wx.CheckBox(page, label="&Vibrate when you are hit")
        self.pad_vibration.SetValue(self.settings.gamepad_vibration)
        sizer.Add(self.pad_vibration, 0, wx.ALL, 8)

        vibration_hint = wx.StaticText(
            page,
            label=(
                "A short flick for a lash and a heavier one for a gunshot, "
                "so you can feel which of the two hit you before the status "
                "line has said so. Only pads that can vibrate do."
            ),
        )
        vibration_hint.Wrap(440)
        sizer.Add(vibration_hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        deadzone_label = field_label(page, "Stick dead zone, percent:")
        self.deadzone = wx.Slider(
            page, value=int(self.settings.gamepad_deadzone * 100), minValue=5, maxValue=95,
            style=wx.SL_HORIZONTAL | wx.SL_LABELS,
        )
        sizer.Add(stack(deadzone_label, self.deadzone), 0, wx.ALL | wx.EXPAND, 8)

        bindings_label = field_label(page, "Button assignments:")
        self.binding_list = wx.ListBox(page, choices=self._binding_labels(), size=(-1, 160))
        sizer.Add(stack(bindings_label, self.binding_list), 1, wx.ALL | wx.EXPAND, 8)

        reset = wx.Button(page, label="&Restore default assignments")
        reset.Bind(wx.EVT_BUTTON, self._reset_bindings)
        sizer.Add(reset, 0, wx.ALL, 8)

        note = wx.StaticText(
            page,
            label=(
                "Assignments follow the standard controller layout, with the "
                "two weapons on the triggers. Outside a match the pad drives "
                "the interface instead: the stick and D-pad move, A chooses, "
                "B goes back, and the shoulders step between controls. Plug a "
                "pad in at any time; it is picked up within a couple of "
                "seconds."
            ),
        )
        note.Wrap(440)
        sizer.Add(note, 0, wx.ALL, 8)

        page.SetSizer(sizer)
        return page

    def _binding_labels(self) -> list[str]:
        from ..input.gamepad import (
            DEFAULT_BUTTON_BINDINGS,
            DEFAULT_TRIGGER_BINDINGS,
            TRIGGER_LABELS,
        )

        overrides = {
            v: k for k, v in self.settings.gamepad_bindings.items()
        }
        labels = []
        for action in BINDABLE:
            control = overrides.get(action.value)
            if control is None:
                control = next(
                    (str(b) for b, a in DEFAULT_BUTTON_BINDINGS.items() if a is action),
                    None,
                )
            if control is None:
                control = next(
                    (t for t, a in DEFAULT_TRIGGER_BINDINGS.items() if a is action), None
                )
            if control is None:
                where = "unassigned"
            elif control in TRIGGER_LABELS:
                where = TRIGGER_LABELS[control]
            else:
                where = f"button {control}"
            labels.append(f"{ACTION_LABELS[action]}: {where}")
        return labels

    # ------------------------------------------------------------------
    def _selected_device(self) -> str:
        index = self.device.GetSelection()
        if 0 <= index < len(self._device_names):
            return self._device_names[index]
        return ""

    def _test_audio(self, event: wx.CommandEvent) -> None:
        """Switch to the highlighted device and fire a gunshot through it."""
        name = self._selected_device()
        if not name:
            return
        if not self.ctx.audio.set_device(name):
            message(self, f"{name} could not be opened.", "Audio device")
            return

        handle = self.ctx.audio.play("usergun")
        # Say so as well as playing. If the wrong device is selected the
        # gunshot is inaudible by definition, and silence with no explanation
        # is exactly what makes this button feel broken.
        if handle is not NULL_HANDLE:
            self.ctx.speech.report(f"Playing a test sound through {name}.")
        else:
            self.ctx.speech.report(f"Selected {name}, but the test sound would not play.")
            message(self, "The test sound could not be played.", "Audio device")

    def _reset_bindings(self, event: wx.CommandEvent) -> None:
        self.settings.gamepad_bindings.clear()
        self.binding_list.Set(self._binding_labels())
        self.ctx.speech.speak("Default assignments restored.")

    # ------------------------------------------------------------------
    def _on_cancel(self, event: wx.CommandEvent) -> None:
        """Undo a device switch made by the test button.

        Testing has to open the device to make a sound through it, so it
        changes live state before the player has agreed to anything. Cancel
        must mean cancel.
        """
        if self.ctx.audio.current_device_name() != self._device_on_open:
            self.ctx.audio.set_device(self._device_on_open)
        event.Skip()

    def _on_ok(self, event: wx.CommandEvent) -> None:
        s = self.settings
        previous_backend = s.speech_backend

        chosen = self._selected_device()
        if chosen and chosen != self.ctx.audio.current_device_name():
            if not self.ctx.audio.set_device(chosen):
                message(self, f"{chosen} could not be opened.", "Audio device")
        s.sound_volume = self.sound_slider.GetValue() / 100.0
        s.music_volume = self.music_slider.GetValue() / 100.0
        s.music_enabled = self.music_on.GetValue()
        s.screams_enabled = self.screams_on.GetValue()
        s.play_intro_music = self.intro_on.GetValue()

        self._apply_speech_settings()

        s.difficulty = self._levels[self.difficulty.GetSelection()].key
        s.player_name = self.name_field.GetValue()
        s.player_gender = "female" if self.gender.GetSelection() == 1 else "male"
        s.birthday = self.birthday.GetValue()
        theme_index = self.theme.GetSelection()
        theme_changed = False
        if 0 <= theme_index < len(self._themes):
            chosen_theme = self._themes[theme_index][0]
            theme_changed = chosen_theme != s.theme
            s.theme = chosen_theme
        s.stats_enabled = self.stats_on.GetValue()
        s.use_power_weapon = self.power_weapon_on.GetValue()

        s.gamepad_enabled = self.pad_on.GetValue()
        s.gamepad_vibration = self.pad_vibration.GetValue()
        s.gamepad_deadzone = self.deadzone.GetValue() / 100.0

        s.relay_spy_url = self.spy_url.GetValue().strip()
        s.save()
        self.ctx.audio._reapply_bus("sfx")
        self.ctx.audio._reapply_bus("music")
        if s.speech_backend != previous_backend:
            self.ctx.speech.reload()
        else:
            # Same backend, but the voice, rate or pitch may have moved.
            # Re-applying is cheaper than rebuilding the whole context.
            self.ctx.speech.apply_settings()
        if not self.ctx.apply_theme() and theme_changed:
            # Windows draws the title bar and scroll bars, and only lets an
            # application choose their appearance before it has any windows.
            message(
                self,
                "The new appearance is applied. The title bar and scroll "
                "bars keep the old one until you next start the game, "
                "because Windows only lets a program choose those before it "
                "opens a window.",
                "Appearance",
            )
        self.ctx.apply_gamepad_setting()
        event.Skip()
