# Fusion Fire

Beat the hell out of your computer. Fair warning: it beats back.

Everybody has wanted to shoot their computer at some point in their lives. Now you can, and you won't need to blow money you don't have and most likely never will have on new hardware afterwards.

Fusion Fire is an audio combat game for Windows where you and a terrible computer that was probably built by a teenager's mum, who probably thinks the CPU is an actual potato, take turns trying to destroy each other with a gun, a whip, a bomb, and a power weapon that occasionally blows up in your face; quite literally. 

Fusion Fire is a modernised port of Acefire, written by Day Garwood of X-sight Interactive and released in 2008. The original game was not reverse engineered or disassembled, so none of the original VB 6 Acefire code was used in this rewrite. Fusion Fire was built entirely from scratch with brand new code. The original wave files of music and sound effects were used to preserve the 2008 vibe at its absolute highest quality. No infringement of rights of any kind is intended and no financial gain shall be obtained from this rewrite. It is strictly for preservation and modernization purposes. The original concept, code, sounds and other assets are properties of their respective owners.

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

The first time you start a fight, the game asks who you are and your date of birth. This is entirely optional and stored locally on your own machine; nothing is sent anywhere. After that it is just a matter of choosing which computer you want to fight.

1. Choose Play offline from the main menu.
2. Pick a difficulty from the list and press Enter.
3. Listen to the machine boot up. Savour it, because it is the last calm sound you will hear before one of you is blown to pieces.

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
| `Space` | Mark a note in the bonus round, or silence the screams during a match |
| `Enter` | Turn the background score on or off |
| `Home` and `End` | Turn sound effect volume up and down |
| `Page Up` and `Page Down` | Turn music volume up and down |
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
3. When the horn sounds, everything you marked is applied and read out.

The round is three seconds long by default, which is not enough time to think and is not meant to be. If that is not your idea of fair, Settings → Game will stretch it to as much as thirty seconds.

### Cheat codes

Score thirty points in a single match and the cheat codes unlock permanently. Fusion Fire writes them to a text file in %appdata%\FusionFire\cheats.txt.

The prompt is invisible, exactly as it was in 2008. Press `C` during a match; a short bang tells you it is open, every character you type is spoken back by a recorded voice, Enter submits and Escape backs out. Type a quantity, a space, then the code, E.G. `15 bullets`.

Cheats do not work on Impossible, so if you think '60 machinedamage' will get you the upper hand, it won't. Even if it did, 40 is lower than 75, so the computer will just restore its health anyway.

## Controllers

Any pad Windows recognises works, and can be plugged in mid-match. The weapons are on the triggers, because they are the two things you do most and the only two that are aimed.

| Control | Action |
|---|---|
| Right trigger | Shoot |
| Left trigger | Lash |
| X | Load gun |
| Y | Restore health |
| B | Laugh at your opponent |
| A | Taunt your opponent | 
| Left bumper | Throw a bomb |
| Right Bumper | Fire the power weapon or send a message online |
| Start | See your health, points, ammo and health restores |
| Back | See your opponent's health, points, ammo and health restores |
| D-pad and left stick | Move in the bonus round |

The game's menus can also be navigated with a controller.

| Control | At a menu |
|---|---|
| Left stick and D-pad | Move |
| A, or Start | Choose |
| B, or Back | Go back |
| Left and right shoulder | Previous and next control |

Assignments are rebindable in Settings, and the stick dead zone is adjustable for a worn thumbstick. The menu controls are deliberately fixed, so rebinding A cannot leave you with no way to answer a dialog.

### Vibration

Fusion Fire also supports controller haptics, so you can feel your opponent ripping you a new one as well as hear it. The whip is a soft pulse, the gun is a stronger vibration, and the bomb is a long rumble.

## Playing online

You can fight your friend or foe over a LAN or the internet. The recommended way to fight over the internet is with a relay server, though you can also do peer to peer fights; this was your only option in the original game.
There are two security options to choose from, quick play and encrypted mode.

### Quick play

No passphrase, no encryption, and nothing to remember but a room code. This is the quickest way to get two people fighting.

One of you picks the server and the room code and sends both to the other. Whoever presses OK first is the host and moves first, so if you want to go first, do not dawdle.

#### If you are starting the fight

1. Choose Play online from the main menu.
2. Leave Connection type on Relay server and Connection security on Quick play.
3. Tab to the room code field and enter a code that the two of you will remember.
4. Press Alt + L to get a list of public relay servers, or enter the hostname/IP and port of a private server in the respective fields.
5. Enter how many bullets and health restores each player will get in the respective fields.
6. Hit OK to connect to the relay.

#### If you are joining the fight

1. Choose Play online from the main menu.
2. Leave Connection type on Relay server and Connection security on Quick play.
3. Type the room code given to you by the host in the room code fox.
4. Ignore Match supplies. Whoever gets in first is the host, and their numbers are the ones both of you play with.
5. Connect to the server via the publicized servers list (Alt + L) or type the details of a private server in the relay server and port fields.

### Encrypted

Same as quick play, but a shared passphrase takes the place of the room code and the whole fight is TLS 1.3 encrypted. Set Connection security to Encrypted and the dialog swaps the Room code box for a Shared passphrase box.

#### If you are starting the fight

1. Follow the quick play steps, but set Connection security to Encrypted.
2. Press New passphrase to have one thought up for you, or type your own of twelve characters or more.
3. Press Copy passphrase so it can be sent to the other player.
4. Press OK and wait for your opponent to join.

#### If you are joining the fight

1. Follow the quick play steps, but set Connection security to Encrypted.
2. Type or paste the passphrase the host gave you into the Shared passphrase box, exactly as they gave it to you.
3. Press OK.

Get one character of it wrong and you will not join a different room, you simply will not connect, and Fusion Fire will tell you to check that you both typed the same thing.

Your passphrase is scrambled with a key derivation function called scrypt. In order to try and unscramble a scrypt-hashed password, an attacker won't just need a computer that is fast, they'll also need a computer that has a lot of memory. They'd need a stupidly large amount of graphics cards to even think about doing this efficiently which, especially these days is stupidly expensive.

Encrypted mode uses end-to-end encryption, so everything that passes through the relay is scrambled and only the host and their opponent can unscramble it.

### Direct peer to peer

No relay in the middle. The host starts a game server on their own machine and the other player connects to it, which is how Acefire did it. On a LAN this just works; over the internet the host either needs a forwarded port or a mesh networking solution like TailScale.

Here the roles are a choice rather than a race: whoever picks Host the game is the host and moves first.

#### If you are hosting

1. Choose Play online from the main menu.
2. Set Connection type to Direct peer to peer, and leave Your role on Host the game and wait for an opponent.
3. Pick a port, or leave it at 6000.
4. Arrow through the Address to listen on list. Leave it on All addresses unless you have a reason not to.
5. Press Copy address and send that address and the port to the other player.
6. Set Bullets each and Restores each. You are the host, so these are the numbers you both play with.
7. Press OK. Fusion Fire tells you which address it is waiting on.

#### If you are joining

1. Choose Play online from the main menu.
2. Set Connection type to Direct peer to peer, and set Your role to Join someone else's game.
3. Type their address into Opponent's address, and their port into Port.
4. Press OK.

### Match supplies

Nobody has an endless magazine online. An opponent who can never run out can never be worn down, which is fine for a machine and miserable for a person.

Both players get the same bullets and health restores, set at the bottom of the Play online dialog. The relay only decides who hosts once both of you have dialled in, so you both fill the numbers in and the host's are the ones used.

The bonus round happens online too. Both players get one at the same moment, each picking from their own thirteen notes, and the host sets the length. Neither of you ever sees the other's notes, only what they came to.

### The ringside

Up to three people can pull up a chair and watch the two of you knock lumps off each other. Tick "Let people watch (ringside)" before you start and anyone who types your room code once the fight is under way gets a seat instead of being turned away.

#### If you are starting the fight

1. Follow the quick play steps.
2. Tick Let people watch (ringside) before you press OK.
3. Send the room code to your opponent and to anyone you want watching.

You are told when somebody sits down and when a seat empties, so you always know whether there is an audience. There is nothing else to do; the seats look after themselves.

#### If you are taking a seat

1. Choose Play online from the main menu.
2. Leave Connection type on Relay server and Connection security on Quick play.
3. Type the same room code the fighters are using.
4. Type the relay server address and press OK.

If the fight has not started yet you will be the opponent, not a spectator. Seats only exist once both fighters are in.

From a seat you hear the whole fight called by name, since neither of them is you: "Ada Lovelace shoots and hits for 12. Blue Screen is on 29." Press `4` and `5` for each fighter's status, `R` to repeat the last line, and Escape to leave. Everything that would throw a punch tells you that you are watching.

Sit down ten minutes in and you are caught up: the host posts the scoreboard the moment your seat is taken, so you get both fighters' health, points, ammunition and whose turn it is before the next blow lands.

The ringside is one way. Nothing you do in a seat reaches the fight, which is why the fighters can be told there is an audience without having to worry about it.

#### What the ringside will not do

Watch an encrypted fight. The encryption runs between the two fighters and nobody else, and there is no third place to stand, so the tick box is unavailable the moment you choose a passphrase. It is a relay-only feature for the same reason there is no relay in direct peer to peer: there is nothing in the middle to copy the fight to anyone.

### Running your own relay

The relay and the spy service are standalone scripts at the repository root. They import no game code and need no game dependencies, so they run on any machine with a plain Python 3.13.

Update your relay when you update the game. The opening a client sends gained a byte when rooms gained a ringside, so an old relay and a new game will sit there staring at each other.

1. Run the relay, where `<name>` is the name shown in the publicized servers list.
    ```
    python srv.py <name> <port>
```
2. Add `-P` to publicize it to a spy so players can find it in the server list.
    ```
    python srv.py <name> <port> -A <relay_server_hostname> -P
```

The -A argument tells the spy what hostname or IP address the relay server is using so the client knows the right server to dial.
The reference spy service is `spy.py`, which serves the list over HTTPS when you give it a certificate:

```
python spy.py <port> --ssl-cert fullchain.pem --ssl-key privkey.pem
```

The spy service gets its name from QSpy, which allowed Quake players to find and connect to Quake servers. QSpy and the Planet Quake website ultimately evolved into Game Spy, which allowed players to easily find multiplayer servers for many video games until its shutdown in 2014.

## Settings

Press Control + comma, or choose Settings from the main menu.

### Speech

Speech goes to whichever screen reader you are running, and the reader owns its own voice, rate and pitch. Fusion Fire does not override them, because you set them where you already set them.

With no screen reader running on Windows 10 or 11, the game falls back to a platform voice, OneCore or SAPI, and there the voice, rate and pitch are the game's to set, so Settings offers them. Each control enables itself according to what the chosen voice actually supports rather than sitting there doing nothing.

### Dark mode

On Windows 10 and 11, Fusion Fire follows your Windows light and dark setting, and follows it live. You can also pin it to light or dark regardless. On Windows 8.1 and 7 there is no system setting to follow, so those two choices are all that is offered.

Changing the setting mid-session repaints the game immediately, but the title bar and scroll bars are drawn by Windows itself and keep the appearance they were created with until the next launch. Fusion Fire tells you when that happens, instead of leaving you wondering why half the window changed.

### Replacing sounds

Open the `sounds` folder beside the game and replace any file in it. That is the whole procedure. `sounds/sfx/usergun.wav` is your gun, `sounds/music/level1.wav` is the healthy-health score.

The game supports sound files in either Wave, MP3, Ogg Vorbis or FLAC format.

### Updating

Either at startup or via the check for updates option in the main menu, Fusion Fire will check for game updates and offer to download and apply the update for you if one is available.

## Credits

Fusion Fire is by Seediffusion.

It would not exist without Acefire by X-Sight Interactive:

* Day Garwood, for the original code, the alpha music and many of the sounds.
* Quinten Pendle, for the music and the install voice.
* Philip Bennefall, for the alphabet and number speech, released into the public domain, and other sounds.
* Samuel Proulx, for the name dictionaries.
* Munawar Bijani, Michael Forzano, Emiliano Scavuzzo and Louis Bryant, for the original randomisation, online and Pacmate code.
* audiogamemaker.com, ljudo.com and the free sound libraries of the early 2000s internet.
