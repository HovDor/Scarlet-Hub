<p align="center">
  <img src="assets/logo.png" width="96" alt="Scarlet hub">
</p>

<h1 align="center">Scarlet hub</h1>
<p align="center">Fishing macro for <b>Fisch</b> (Roblox) &nbsp;·&nbsp;
<a href="https://discord.gg/FJPQTUX2yT">Discord</a></p>

---

Scarlet hub plays all three stages of fishing for you: **cast**, **shake** and
**the reeling minigame** - in a red-black control panel with a status overlay
on top of the game.

## Features

| Stage | What it does |
|---|---|
| **Cast** | *Normal* (hold for a set time) or *Perfect* (releases in the green zone, learns your input delay). Editable step sequence: delays, zoom, camera tilt. |
| **Shake** | *Pixel*, *Circle* (click the buttons), *Navigation* (UI navigation + Enter) or *Disabled*. |
| **Fish** | Keeps the fish inside the bar. *Line* tracking works with any rod; *Color* tracking uses per-rod colors. |
| **Extras** | Auto select rod, per-rod profiles, detection FPS presets for weak and strong PCs, custom hotkeys, English / Russian UI. |

## Requirements

- Windows 10 / 11
- Roblox in **fullscreen**, Windows scale **100%**
- 1920x1080 works out of the box; on other resolutions set the areas with **F2**

## Install

1. Download `ScarletHub-<version>.zip` from [Releases](../../releases).
2. Check it: the SHA-256 is published next to the download.
3. Extract the folder anywhere (not into Program Files) and run `ScarletHub.exe`.

**"Windows protected your PC"** - that is SmartScreen: the program is new and
not signed yet. Click *More info -> Run anyway*.
**Antivirus warning** - macros press keys and the mouse for you, so some
antivirus engines flag them. Download only from this page or our Discord.

## First run

1. **Fish** tab -> *Change rod* - pick the rod you are holding.
2. **F2** - drag the boxes over the fishing bar, the shake area and the cast bar, *Enter* to save.
3. Open Roblox, stand at the water, press **F8**.

## Hotkeys

| Key | Action |
|---|---|
| F8 | start / pause |
| F2 | change areas |
| F7 | detector snapshot (for bug reports) |
| F9 | exit |

All keys can be changed in *Main -> Hotkeys*.

## Settings & files

Everything lives next to `ScarletHub.exe`: `scarlet.ini` (settings, rods) and
`scarlet.log` (journal). Older `klev.ini` / `klev.log` are renamed automatically.

**Debug mode** (Extra -> Recording) saves snapshots and `trace.csv` for bug
hunting. It is off by default - turn it on only when we ask for it.

**Update check**: at start the program asks GitHub once for the latest release
and shows a button in the header if there is a newer one. Nothing else is sent.
Turn it off in Extra -> Recording.

## Bug reports

Post in the Discord support forum and attach **`scarlet.log`** (next to the exe)
and a screenshot.

## Build from source

Python 3.10+, then:

```
build\build.bat
```

The result is `dist\ScarletHub\ScarletHub.exe` and a release zip with its SHA-256.

## Is it safe?

Scarlet hub works from the outside, the same way a person at the keyboard does:

- it **looks at the screen** - it takes screenshots of a few areas of the game window;
- it **presses keys and mouse buttons** through normal Windows input.

It does **not** read or change the game's memory, does not inject anything into
Roblox, does not modify game files and does not touch the game's network traffic.
Fisch staff have said that accounts get flagged for *memory-reading macros
(exploits)* - Scarlet hub is not one of them.

The only network request the program makes is the optional update check to
GitHub (see above).

## Disclaimer

Even so, automating gameplay may be against the Roblox and Fisch rules, and those
rules can change at any time. Use Scarlet hub at your own risk - nobody can
guarantee that a macro will never lead to a ban.
