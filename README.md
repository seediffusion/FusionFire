# Fusion Fire

Beat the hell out of your computer. Fair warning: it beats back.

Everybody has wanted to shoot their computer at some point in their lives. Now you can, and you won't need to blow money you don't have and most likely never will have on new hardware afterwards.

Fusion Fire is a fully accessible audio combat game for Windows that puts blind and visually impaired players first. You and a terrible computer that was probably built by a teenager who thinks their CPU is an actual potato take turns trying to destroy each other with a gun, a whip, a bomb, and a power weapon that occasionally blows up in your face; quite literally. It is played entirely by ear and driven from the number row, with on-screen elements that are clearly labelled for screen readers such as [NVDA](https://nvaccess.org/about-nvda/) and [JAWS](https://www.freedomscientific.com/products/software/jaws/). Speech and braille both carry the commentary, dark mode follows your system theme, and any controller Windows recognises can play it. There is nothing to install: unzip it, run it, and you are in a fight.

Fusion Fire is a modernised port of Acefire, written by Day Garwood of X-sight Interactive and released in 2008. None of the original VB 6 Acefire code was used in this rewrite. Fusion Fire was built entirely from scratch with brand new code. The original wave files of music and sound effects were used to preserve the 2008 vibe at its absolute highest quality. No infringement of rights of any kind is intended and no financial gain shall be obtained from this rewrite. It is strictly for preservation and modernization purposes. The original concept, code, sounds and other assets are properties of their respective owners.

## Fusion Fire features

Fusion Fire is a turn-based fight against a computer that genuinely does not want to be there. You shoot, lash, bomb and heal; it shoots, lashes, bombs and heals back, and gets better at it as you raise the difficulty. Six opponents run from Coward, who hands you unlimited ammunition and never heals itself, all the way up to Impossible, which denies you bombs, disables every cheat and heals the moment it drops below three quarters health.

Between rounds the machine hides items in an octave of thirteen notes and gives you a few seconds to grab them by ear. It can also just refuse to play, and is measurably grumpier at mealtimes and after midnight, because the original was written that way and it is funnier than a menu.

You can fight another person over the internet, with or without encryption. Everything that happens is spoken and brailled at the same time, and written to a transcript you can arrow back through, because an audio game that says something once and loses it is an audio game you cannot follow.

## Running Fusion Fire

### Compiled

* [Download the Fusion Fire installer](https://github.com/seediffusion/FusionFire/releases/latest/download/Fusion_Fire_Setup.exe)
* [Download the Fusion Fire zip file](https://github.com/seediffusion/FusionFire/releases/latest/download/FusionFire.zip)

If you grabbed the zip, extract it and run FusionFire.exe. If file extensions don't show on your system, the filename will just be FusionFire.

### From source

Fusion Fire is written in Python and uses [UV](https://docs.astral.sh/uv/), a fast, modern Python package manager written in Rust. You will also need [Git for Windows](https://gitforwindows.org/) installed.

1. Press Windows + R, type powershell, and press Enter to launch PowerShell.
2. Install UV.
```
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```
3. Use UV to install Python if it is not already installed.
```
uv python install
```
4. Clone this repository with git.
```
git clone https://github.com/seediffusion/FusionFire
```
5. Move into the Fusion Fire folder and install the libraries needed.
```
cd FusionFire
uv sync
```
6. Run Fusion Fire.
```
uv run run.py
```

### Compiling

```
uv run build.py
```

The output lands in the `dist` folder.

There is also `uv run build.py --onefile`, which produces a single executable. It is not recommended: the entire program has to be extracted to a temp folder on every launch, so what you get is essentially a self-extracting archive with delusions of grandeur.

## Playing

The first time you launch, the game asks who you are and your date of birth. This is entirely optional and stored locally on your own machine; nothing is sent anywhere. After that it is just a matter of choosing which computer you want to fight.

1. Choose Play offline from the main menu.
2. Pick a difficulty from the list and press Enter.
3. Listen to the machine boot up. Savour it, because it is the last calm sound you will hear before one of you is blown to pieces.
4. Start hitting the number row.

### Keyboard shortcuts

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

You and your opponent each score a point for every attack that lands, and nothing at all for one that misses. Whoever lands the last strike wins, so being knocked to −13 loses exactly as thoroughly as being knocked to 0.

| Weapon | Damage |
|---|---|
| Whip | 1 to 8 health |
| Gun | 5 to 15 health |
| Bomb | 15 to 50 percent of what the target has left |
| Power weapon | 15 to 50 health |

The gun and the whip are absolute value attacks. The bomb is percentage based, so a 50% bomb takes 30 health off an opponent sitting on 60, but only 5 off the same opponent sitting on 10. Bombs soften people up; they do not finish them.

The gun has to be loaded before it will fire, but loading is free and does not cost you your turn. Reloading is the setup for a move, not the move itself. The machine reloads for free as well and still attacks in the same turn, so the courtesy runs both ways.

An action you cannot take just says so. Loading a gun that is already loaded, firing on an empty chamber, healing at full health: each answers with a sentence telling you what is wrong, and no buzzer in front of it.

### Difficulty

| Difficulty | What you are up against |
|---|---|
| Coward | Endless bullets and health restores for you. The machine never heals. It is embarrassing for it. |
| Beginner | Ten of each. The machine heals occasionally and never picks up bombs. |
| Intermediate | The machine heals readily and starts collecting bombs. |
| Advanced | It heals often and throws bombs without hesitation. |
| Expert | Five of each. The machine is harder to hit and you are easier to hit. |
| Impossible | No bombs for you, no cheats at all, and it heals every time it drops below three quarters health. Good luck. |

### The power weapon

The power weapon is optional, takes two minutes to charge and stays usable for three. Press `6` to fire it.

You will hear the weapon preparing to fire, a drum roll and building music playing all the while. Then a massive explosion. Did you blow that bastard computer sky high, did the machine activate its cyber defences and make you blow your own balls off, or did you miss the mark completely?

You get one use. Once it has been fired it cannot be recharged and is gone for the rest of the match. Use it wisely... or die trying.

### The bonus round

Every so often the machine hides items in an octave of thirteen notes. Some help you, some help it, and some do nothing at all. What is behind each note is rolled fresh every time, so "note seven is always a bomb" is not a strategy.

1. Use `Left Arrow` and `Right Arrow` to move between the notes. The pitch rises left to right, so you can hear where you are without counting.
2. Press `Space` to mark a note. Mark as many as you dare.
3. When the horn goes, everything you marked is applied and read out.

The round is three seconds long by default, which is not enough time to think and is not meant to be. If that is not your idea of fair, Settings → Game will stretch it to as much as thirty seconds.

### Cheat codes

Score thirty points in a single match and the cheat codes unlock permanently. Fusion Fire writes them to a text file and tells you where it put it.

The prompt is invisible, exactly as it was in 2008. Press `C` during a match; a short bang tells you it is open, every character you type is spoken back by a recorded voice, Enter submits and Escape backs out. Type a quantity, a space, then the code, E.G. `15 bullets`.

Cheats do not work on Impossible, and they do not work online, where the other side is a person rather than a machine.

## Controllers

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

The taunt is one button and an immediate insult, picked for you from the same recordings the audio comment dialog lists. It is for the beat between your turn ending and theirs starting, when there is no time to choose.

Away from a match the pad drives the interface instead, so you can get from the menu to the rematch prompt without touching the keyboard.

| Control | At a menu |
|---|---|
| Left stick and D-pad | Move |
| A, or Start | Choose |
| B, or Back | Go back |
| Left and right shoulder | Previous and next control |

Assignments are rebindable in Settings, and the stick dead zone is adjustable for a worn thumbstick. The menu controls are deliberately fixed, so rebinding A cannot leave you with no way to answer a dialog.

### Vibration

A pad that can vibrate does when something lands on you: a short flick for a lash, a heavier and longer one for a gunshot, so you can tell the two apart by feel before the status line has said which it was. Bombs and the power weapon are heavier still.

Only what lands on *you* is felt. There is an off switch in Settings → Controller.

## Playing online

Two people, one match, over the internet. You can go through a relay server (recommended) or connect directly, and either way you can play with or without encryption.

### Quick play

The default. No passphrase at all.

1. Choose Play online from the main menu.
2. Leave Connection type on Relay server and Connection security on Quick play.
3. The dialog fills in a short room code for you. Read it out to the other player or paste it to them.
4. Both of you press OK. The first one in is the host and moves first.

The room code is an identifier, not a secret, so share it freely. Anyone who knows it can join, and the match is not encrypted. That is exactly the "open socket, first player in" behaviour of the original, minus the guesswork about which room you are in.

### Encrypted

Set Connection security to Encrypted and both players type the same passphrase of twelve characters or more. The New passphrase button will think one up if you would rather not.

The passphrase is not a formality. It encrypts the match with TLS 1.3 and authenticates both ends, so somebody who does not have it cannot connect at all. It is stretched with scrypt first, so guessing at it costs real work rather than a hash.

The original simply opened a raw TCP socket and played against whoever reached it first, in the clear; anyone on the path could read the match or rewrite it. TLS 1.2 and AES256-GCM were a thing in 2008, guys.

### Relay servers

Both players dial the same relay, and it pairs you by your room code or passphrase and forwards your traffic byte for byte. Nobody forwards a port and nobody needs to know their public address.

The relay never sees anything but ciphertext in encrypted mode, because the TLS handshake runs between the two players, through it. It is trusted for availability, never for privacy: it can drop a match, but it cannot read one and cannot join one without the passphrase.

Press the Get a list of publicized servers button to pick one, or type an address by hand. The list comes from a relay spy service, which is filled in for you already and only contacted when you ask for the list. Point it elsewhere or clear it under Settings → Online.

One relay carries as many matches at once as people want to start on it, and the rooms do not touch. A room will not hold three people, so dialling a code that already has two tells you to pick a different one; the pair already playing never notice.

### Direct peer to peer

One player hosts and the other joins.

1. The host chooses Direct peer to peer, then Host the game.
2. The host picks a port and reads their address out to the other player. The Copy address button puts it on the clipboard.
3. The other player chooses Join someone else's game, types that address and port, and presses OK.

Hosting asks for a port and nothing else, because you are the one being dialled. Your machine's addresses are a list you can arrow through; it listens on all of them unless you pick one, which is only worth doing on a machine where it matters, such as one with a VPN you would rather nobody arrived over.

Over the internet you need your public address and a forwarded port. That is not in the list, because your router knows it and your computer does not.

### Match supplies

Nobody has an endless magazine online. An opponent who can never run out can never be worn down, which is fine for a machine and miserable for a person.

Both players get the same bullets and health restores, set at the bottom of the Play online dialog. The relay only decides who hosts once both of you have dialled in, so you both fill the numbers in and the host's are the ones used.

The bonus round happens online too. Both players get one at the same moment, each picking from their own thirteen notes, and the host sets the length. Neither of you ever sees the other's notes, only what they came to.

### Running your own relay

The relay and the spy service are standalone scripts at the repository root. They import no game code and need no game dependencies, so they run on any machine with a plain Python 3.13.

1. Run the relay, where `<name>` is the address players dial.
```
python srv.py <name> <port>
```
2. Add `-P` to publicize it so players can find it in the server list.
```
python srv.py <name> <port> -P
```

`-P` announces to whatever is in the `FUSION_FIRE_SPY_URL` environment variable, or failing that the address set in the game's own settings. On a machine with neither, pass it directly:

```
python srv.py <name> <port> -P https://spy.example.org/servers
```

If `<name>` is a label rather than a dialable address, add `-A` so the list points players at the real one:

```
python srv.py test 6001 -A fusion.seedy.cc -P https://spy.example.org/servers
```

The reference spy service is `spy.py`, which serves the list over HTTPS when you give it a certificate:

```
python spy.py <port> --ssl-cert fullchain.pem --ssl-key privkey.pem
```

## Settings

Press Control + comma, or choose Settings from the main menu.

### Speech

Speech goes to whichever screen reader you are running, and the reader owns its own voice, rate and pitch. Fusion Fire does not override them, because you set them where you already set them.

With no screen reader running on Windows 10 or 11, the game falls back to a platform voice, OneCore or SAPI, and there the voice, rate and pitch are the game's to set, so Settings offers them. Each control enables itself according to what the chosen voice actually supports rather than sitting there doing nothing.

### Dark mode

On Windows 10 and 11 Fusion Fire follows your Windows light and dark setting, and follows it live. You can also pin it to light or dark regardless. On Windows 8.1 and 7 there is no system setting to follow, so those two choices are all that is offered.

Changing the setting mid-session repaints the game immediately, but the title bar and scroll bars are drawn by Windows itself and keep the appearance they were created with until the next launch. Fusion Fire tells you when that happens, instead of leaving you wondering why half the window changed.

### Replacing sounds

Open the `sounds` folder beside the game and replace any file in it. That is the whole procedure. `sounds/sfx/usergun.wav` is your gun, `sounds/music/level1.wav` is the healthy-health score.

`.wav`, `.ogg`, `.mp3` and `.flac` all work regardless of what the original file was. Nothing you drop in there can reach outside that folder, so a crafted filename cannot make the game open something else on your disk.

### Updating

Fusion Fire checks GitHub for a newer release when it starts, and says nothing unless there is one. When there is, it names the version, shows the release notes in a box you can arrow through, and asks. Saying no changes nothing.

Say yes and the new build is downloaded and unpacked under your own user folder first, so a download that fails or is cancelled leaves your game exactly as it was. Only once it has all arrived does Fusion Fire close, swap itself over and reopen.

That last step is a small helper script, because Windows will not let a running program overwrite its own executable.

It also means the update works when the game lives somewhere you cannot write to, such as `C:\Program Files\Fusion Fire`. Windows asks for permission once, after you have already agreed to update, then hands the game back to your desktop rather than leaving it running as an administrator.

Check for updates is on the main menu and on the Help menu whenever you want it. Turn the startup check off under Settings → Game and Fusion Fire contacts nobody on its own.

## Accessibility

Speech and braille go through Prism, which talks to whichever screen reader is actually running and falls back to the platform voice when none is. The original required a screen reader and gave you nothing without one; this is playable either way.

Combat status lines are short and numeric, which is exactly what a braille display handles better than speech, so status output goes to both by default. Braille is never delayed for the sake of the audio.

The spoken line waits until the attack and the screams have finished, so the commentary is never buried under the sound effects. Press `R` at any time to hear the last line again, or arrow back through the transcript.

## Testing

```
uv run pytest
```

682 tests, covering:

* the rules, the bonus round, the cheat parser and the name generator, in pure Python;
* real sockets and real TLS handshakes, proving a wrong passphrase cannot connect;
* a whole match played through the same calls the keyboard and gamepad use;
* a stand-in controller, checking the button under your thumb is the one the game thinks it is;
* every dialog, checking its labels line up.

The suite stays out of your way while it runs. Two hundred applications booting would put two hundred windows in front of whatever you were doing and speak every line of every match through your screen reader, which is enough to need NVDA restarted afterwards.

So speech is dropped, audio goes to nothing, the window is built but never shown, and the whole thing runs below whatever you are actually doing. Set `FUSION_FIRE_TEST_WINDOWS=1` if you would rather watch.

## Credits

Fusion Fire is by Seediffusion.

It would not exist without Acefire by X-Sight Interactive:

* Day Garwood, for the original code, the alpha music and many of the sounds.
* Quinten Pendle, for the music and the install voice.
* Philip Bennefall, for the alphabet and number speech, released into the public domain, and other sounds.
* Samuel Proulx, for the name dictionaries.
* Munawar Bijani, Michael Forzano, Emiliano Scavuzzo and Louis Bryant, for the original randomisation, online and Pacmate code.
* audiogamemaker.com, ljudo.com and the free sound libraries of the early 2000s internet.
