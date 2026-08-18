"""Game rules. Deliberately free of wx, sound_lib and Prism imports.

Everything in this package is pure Python operating on plain data, which is
what makes the rules testable without a sound card, a screen reader or a
display. The presentation layer subscribes to the events the engine emits
rather than the engine reaching out to play sounds.
"""
