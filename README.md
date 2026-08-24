# Fusion Fire

Everybody has wanted to shoot and beat the hell out of their computer at some point in their lives. Now you can. But watch out, because it can fire back. Best part, you won't need to blow money you don't have and most likely never will have on new hardware afterwards.

Fusion Fire is a fully accessible audio combat game for Windows that puts blind and visually impaired players first. You and a terrible computer that was probably built by a teenager who thinks their CPU is an actual potato take turns trying to destroy each other with a gun, a whip, a bomb, and a power weapon that occasionally blows up in your face; quite literally. It is played entirely by ear, driven from the number row, and every on-screen element is clearly labelled for screen readers such as [NVDA](https://nvaccess.org/about-nvda/) and [JAWS](https://www.freedomscientific.com/products/software/jaws/). Speech and braille both carry the running commentary, a dark mode follows your system theme, and any controller Windows recognises can play it. There is nothing to install: unzip it, run it, and you are in a fight.

Fusion Fire is a modernised port of Acefire, written by Day Garwood of X-sight Interactive and released in 2008. None of the original VB 6 Acefire code was used in this rewrite. Fusion Fire was built entirely from scratch with brand new code. The original wave files of music and sound effects were used to preserve the 2008 vibe at its absolute highest quality. No infringement of rights of any kind is intended and no financial gain shall be obtained from this rewrite. It is strictly for preservation and modernization purposes. The original concept, code, sounds and other assets are properties of their respeective owners.

## Fusion Fire features

Fusion Fire is a turn-based fight against a computer that genuinely does not want to be there. You shoot, lash, bomb and heal; it shoots, lashes, bombs and heals back, and gets better at it as you raise the difficulty. Six opponents run from Coward, who hands you unlimited ammunition and never heals itself, up to Impossible, which denies you bombs, disables every cheat and heals the moment it drops below three quarters health. Between rounds it hides items in an octave of thirteen notes and gives you three seconds to grab them by ear, or as long as thirty if three is not your idea of fair. It can also simply refuse to play, and is measurably grumpier at mealtimes and after midnight, because the original was written that way and it is funnier than a menu.

An action you cannot take says so and leaves it at that. Loading a gun that is already loaded, firing on an empty chamber, healing at full health: each answers with a sentence saying what is wrong, and no buzzer in front of it. The refusal was always the sentence.

Everything that happens is announced through your screen reader and sent to your braille display at the same time. The spoken line waits until the attack and the optional cries of the attacked player have finished, so the commentary is never buried under the sound effects. Braille never waits, because it does not compete with audio. Every announcement is also written to a transcript you can arrow back through, and R repeats the last line, because an audio game that says something once and loses it is an audio game you cannot follow.

The optional power weapon takes two minutes to charge and stays usable for three minutes. Press 6 to fire the power weapon. Upon pressing 6, you will hear the weapon preparing to fire, a drum roll and building music playing all the while. As the weapon fires, you'll hear a massive explosion. Did you blow that bastard computer sky high, did the machine activate its cyber defences and make you blow your own balls off, or did you miss the mark completely? You only get one use of the power weapon. Once it has been fired, it cannot be recharged and is gone for the rest of the match. Use it wisely... or die trying!

Score thirty points in a single match and the cheat codes unlock permanently. The prompt is invisible, exactly as it was in 2008: a short bang tells you it is open, every character you type is spoken back by a recorded voice, Enter submits and Escape backs out. It does not open in an online match, where the other side is a person rather than a machine.

You can play a second person over the internet. By default it is quick play via a room code. An encrypted mode is also available, where a shared passphrase encrypts and authenticates the match.

## Fusion Fire setup

### Compiled releases

* [Download Fusion Fire installer](https://github.com/seediffusion/FusionFire/releases/latest/download/Fusion_Fire_Setup.exe)
* [Download Fusion Fire zip file](https://github.com/seediffusion/FusionFire/releases/latest/download/FusionFire.zip)

### Building it yourself

#### Requirements

* Python 3.13 or later.
* [uv](https://docs.astral.sh/uv/), which handles the virtual environment and the dependencies.

#### Building the binary

1. Clone the repository and step into it.

    ```
    git clone https://github.com/seediffusion/FusionFire
    cd FusionFire
    ```

2. install Python if not already installed.

    ```
    uv python install
    ```

3. Install the dependencies.

    ```
    uv sync
    ```

4. Run it from source, or build a release.

    ```
    uv run run.py
    uv run build.py
    ```

`build.py --onefile` produces a single executable. This is not recommended as the entire program's data has to be extracted to a temp folder on every launch, so that single executable is essentially a self-extracting archive as if you checked the self-extracting archive box in 7-Zip.

## Playing

Upon your first fight, The game asks who you are and your date of birth. This data is entirely optional and is stored locally on your own machine; it is not sent to any external servers for storage and/or processing. Then it's just a matter of choosing which computer you want to fight. After that it is all keyboard, or all controller if you prefer.

| Key | Action |
|---|---|
| `1` | Fire gun |
| `2` | Crack whip |
| `3` | Load gun |
| `4` | Check your status |
| `5` | Check your opponent's status |
| `6` | Fire the power weapon, or send a message online |
| `7` | Restore health |
| `8` | Play an audio taunt |
| `9` | Activate a bomb |
| `L` | Laugh at your opponent |
| `R` | Repeat the last message |
| `C` | Open the cheat prompt |
| `Left Arrow` and `Right Arrow` | Move between notes in the bonus round |
| `Space` | Mark a note in the bonus, or silence the screams during a match |
| `Enter` | Turn the background score on or off |
| `Home` and `End` | Sound volume up and down |
| `Page Up` and `Page Down` | Music volume up and down |
| `Backspace` | Turn statistics recording on or off |
| `Escape` | Leave the match |
| `F1` | The full list, in the game |

### The rules

You and your opponent each score a point for every successful attack, and nothing at all for an attack that misses. Lashes take 1 to 8 health, gunshots 5 to 15, and bombs 15 to 50 percent. Whoever lands the last strike wins, so being knocked to −13 loses exactly as thoroughly as being knocked to 0. Remember, while the gun and the whip are absolute value attacks, the bomb is percentage based. If you use a bomb when your opponent is at 60 health and it deals 50% damage, they will lose 30 health. But that same damage dealt at 10 health will only cost them 5 health.

The gun has to be loaded before it will fire, but loading is free and does not cost you your turn; reloading is the setup for a move, not the move itself. The machine reloads for free as well, and still attacks in the same turn, so the courtesy runs both ways.

A match opens with the whirrs and clicks of the opponent machine booting up, Savour that sound, as it'll be the last sound you hear before either you or the machine are blown to pieces!

### Difficulty

| | |
|---|---|
| Coward | Endless bullets and health restores for you. The machine never heals. It is embarrassing for it. |
| Beginner | Ten of each. The machine heals occasionally and never picks up bombs. |
| Intermediate | The machine heals readily and starts collecting bombs. |
| Advanced | It heals often and throws bombs without hesitation. |
| Expert | Five of each. The machine is harder to hit and you are easier to hit. |
| Impossible | No bombs for you, no cheats at all, and it heals every time it drops below three quarters health. Good luck. |

### Replacing sounds

Open the `sounds` folder beside the game and replace any file in it. That is the whole procedure: `sounds/sfx/usergun.wav` is your gun, `sounds/music/level1.wav` is the healthy-health score.

`.wav`, `.ogg`, `.mp3` and `.flac` all work regardless of what the original file was. Nothing you drop in there can reach outside that folder, so a crafted file name cannot make the game open something elsewhere on your disk.

### Speech

Speech goes to whichever screen reader you are running, and the reader owns its own voice, rate and pitch. The game does not override them, because you set them where you already set them.

With no screen reader running on Windows 10 or 11, the game falls back to a platform voice, OneCore or SAPI, and there the voice, rate and pitch are the game's to set, so Settings offers them. Each control enables itself according to what the chosen voice actually supports rather than being present and inert.

### Dark mode

On Windows 10 and 11 the game follows your Windows light and dark setting, and follows it live: change the system setting and the game changes with it. You can also pin it to light or dark regardless. On Windows 8.1 and 7 there is no system setting to follow, so those two choices are all that is offered rather than offering a third that would do nothing.

Changing the setting mid-session repaints the game immediately, but the title bar and scroll bars are drawn by Windows itself and keep the appearance they were created with until the next launch. Windows only lets a program choose those before it opens a window. The game tells you this when it happens instead of leaving you wondering why half the window changed.

### Controllers

Any pad Windows recognises works, and can be plugged in mid-match. The weapons are on the triggers, because they are the two things you do most and the only two that are aimed.

| Control | Action |
|---|---|
| Right and left trigger | Shoot, lash |
| X and Y | Load, heal |
| B and A | Laugh, taunt |
| Left and right shoulder | Bomb, power weapon |
| Back and Start | Opponent status, your status |
| Stick clicks | Laugh, audio comment |
| D-pad and left stick | Move in the bonus round |

The taunt is one button and an immediate insult, picked for you from the same recordings the audio comment dialog lists. It is meant for the beat between your turn ending and theirs starting, when there is no time to choose.

Away from a match the pad drives the interface instead, so the game can be played from the menu to the rematch prompt without touching the keyboard. The stick and D-pad move, A chooses, B goes back, and the shoulders step between the controls of a dialog. It works by pressing the key you would have pressed, so every menu, tab and message box answers the pad exactly as it answers a finger and says so to your screen reader.

| Control | At a menu |
|---|---|
| Left stick and D-pad | Move |
| A, or Start | Choose |
| B, or Back | Go back |
| Left and right shoulder | Previous and next control |

Assignments are rebindable in Settings, and the stick dead zone is adjustable for a worn thumbstick. The menu controls are deliberately fixed, so that rebinding A cannot leave you with no way to answer a dialog.

### Vibration

A pad that can vibrate does when something lands on you: a short flick for a lash and a heavier, longer one for a gunshot, so the two are tellable apart by feel before the status line has said which it was. It is a third channel, and the only one not queueing behind whatever is already speaking. Bombs and the power weapon are heavier still. Only what lands on *you* is felt, and the whole thing has an off switch in Settings → Controller.

### Playing online

The recommended way to fight over the internet is through a relay server. Both players dial the same relay, and the relay pairs them by the shared passphrase and forwards their traffic byte for byte. The first player to join becomes the host and moves first. Nobody forwards a port and nobody needs to know their public address. A relay server's address can be typed by hand or picked from a list publicized through a relay spy service, which is configured under Settings → Online. The relay and the spy are standalone scripts at the repository root and run on any machine with a plain Python 3.13, no game install needed. A relay operator runs `python srv.py <name> <port>`, where `<name>` is the address players dial, and adds `-P` to publicize the server so players can find it; `-P` announces to the service in the `FUSION_FIRE_SPY_URL` environment variable, or failing that the one set in the game's settings, and on a machine with neither you can pass the address directly: `python srv.py <name> <port> -P https://spy.example.org/servers`. If `<name>` is just a label rather than a dialable address, add `-A <address>` so the spy list points players at the real one (e.g. `python srv.py test 6001 -A fusion.seedy.cc -P ...`). The reference spy service is `python spy.py <port>`, which serves the list over HTTPS when given a certificate: `python spy.py <port> --ssl-cert fullchain.pem --ssl-key privkey.pem`.

One relay carries as many matches at once as people want to start on it, and the rooms do not touch: a match in progress is not disturbed by anyone else arriving, starting or finishing. A room will not hold three people, so dialling a passphrase or room code that already has two tells you to pick a different one, and the pair already playing never notice.

The relay spy service is filled in for you, so the server list works on a first run. Point it elsewhere under Settings → Online, or clear it to turn the list off. Nothing is contacted until you ask for it.

Direct peer to peer still works the old way: one player hosts and the other joins, and the host reads their address out to the other player. Hosting asks for a port and nothing else, because you are the one being dialled. Your machine's addresses are a list you can arrow through, with a Copy address button. It listens on all of them unless you pick one; picking a single address is there for the machine where that matters, such as a VPN you would rather nobody arrived over. Over the internet you need your public address and a forwarded port, which your router knows and your computer does not.

By default the game is quick play: no passphrase at all. Both players type the same short room code, which the dialog shows pre-filled. It is an identifier, not a secret, so read it out or paste it freely. The first player in the room is the host and moves first. The match runs over a plain TCP connection: anyone who knows the code can join, and the traffic is not encrypted. That is exactly the "open socket, first player in" behaviour of the original, minus the guesswork about which room you are in.

Encrypted mode adds a passphrase of twelve characters or more, and the passphrase is not a formality. It encrypts the match with TLS 1.3 and authenticates both ends, so somebody who does not have it cannot connect at all. The original simply opened a raw TCP socket and played against whoever reached it first, in the clear; anyone on the path could read the match or rewrite it. TLS 1.2 and AES256-GCM were a thing in 2008, guys. The passphrase is stretched with scrypt before it becomes the key, so guessing at it costs real work rather than a hash.

The relay does not weaken that: the TLS handshake runs between the two players, through the relay, so the relay only ever sees ciphertext. It is trusted for availability, never for privacy. It can drop a match, but it cannot read one and cannot join one without the passphrase. In quick play the room code fills the same slot as the passphrase. It is hashed into the same 16-byte room token the relay pairs by, so the relay does not even know which mode a room is in. Relay spy lists are validated before they are shown, and nothing a spy reports is trusted: a hostile spy can only waste your time, not read your match.

The bonus round happens online too. Both players get one at the same moment and each picks from their own thirteen notes; the host rolls for it and sets the length, because two engines asked independently would disagree and a round only one player is in is worse than none. Neither side ever sees the other's notes, only what they came to.

Both players start an online match with the same finite supplies, and the host decides how many. Bullets each and restores each are set at the bottom of the Play online dialog. The relay only decides who hosts once both players have dialled in, so both of you fill the numbers in and the host's are the ones used. Nobody has an endless magazine online: an opponent who can never run out can never be worn down.

Messages are length-prefixed JSON checked against a strict schema. Unknown message types, unknown fields, missing fields and out-of-range values are all rejected, frame sizes are capped before anything is allocated, the listener accepts exactly one opponent and then closes, and a peer that floods the connection is disconnected.

### Updating

Fusion Fire checks GitHub for a newer release when it starts, and says nothing unless there is one. When there is, it names the version, shows that release's notes in a box you can arrow through, and asks. Saying no changes nothing.

Saying yes downloads and unpacks the new build under your own user folder first, so a download that fails or is cancelled leaves the installed game as it was. Only once it has all arrived does the game close, swap itself over and reopen.

That last step is a small helper script, because Windows will not let a running program overwrite its own executable. It is also what makes the update work when the game lives somewhere you cannot write to, such as `C:\Program Files\Fusion Fire`. Windows then asks for permission once, after you have already agreed to update, and the game is handed back to your desktop rather than left running as an administrator.

Turn the startup check off under Settings → Game and the game contacts nobody on its own. Check for updates is on the main menu, and on the Help menu, and still works whenever you ask.

## Accessibility

Speech and braille go through Prism, which talks to whichever screen reader is actually running and falls back to the platform voice when none is. The original required a screen reader and gave you nothing without one; this is playable either way. Combat status lines are short and numeric, which is exactly what a braille display handles better than speech, so status output goes to both by default and braille is never delayed for the sake of the audio.

## Testing

```
uv run pytest
```

682 tests. The rules, the bonus round, the cheat parser, the name generator and the calendar are pure Python and run in a fraction of a second. On top of those, the suite opens real sockets and completes real TLS handshakes to prove a wrong passphrase cannot connect, boots the actual application and plays a match through the same calls the keyboard and gamepad use, drives a stand-in controller to check that the button under your thumb is the one the game thinks it is, builds every dialog to check its labels line up, and asserts that no user-facing text contains a character the speech backend would refuse.

It stays out of your way while it does it. Booting two hundred applications means opening whichever screen reader is running two hundred times and speaking every line of every match through it, and putting two hundred windows in front of whatever you were doing, which between them are enough to need NVDA restarted afterwards. Speech output is dropped for the run, the audio buses are turned down to nothing, the game's window is built but never shown or focused into, and the process asks Windows to schedule it below whatever you are actually doing. A full run samples as never once taking the foreground. A handful of tests that are about the speech layer, the volume keys or the window itself lift those, and `tests/test_harness.py` asserts they are still in place so they cannot be removed without the suite noticing. Set `FUSION_FIRE_TEST_WINDOWS=1` to watch a run instead.

## Credits

Fusion Fire is by Seediffusion.

It would not exist without Acefire by X-Sight Interactive:

* Day Garwood, for the original code, the alpha music and many of the sounds.
* Quinten Pendle, for the music and the install voice.
* Philip Bennefall, for the alphabet and number speech, released into the public domain, and other sounds.
* Samuel Proulx, for the name dictionaries.
* Munawar Bijani, Michael Forzano, Emiliano Scavuzzo and Louis Bryant, for the original randomisation, online and Pacmate code.
* audiogamemaker.com, ljudo.com and the free sound libraries of the early 2000s internet.