# Changelog

## 1.0 - first public release
- **Cast**: Normal or Perfect. Perfect releases in the green zone at any cast
  speed and learns your input delay by itself. Editable step sequences.
- **Shake**: Pixel, Circle, Navigation or Disabled; finds both button styles
  anywhere on the screen.
- **Fish**: keeps the fish inside the bar - Line tracking for any rod, Color
  tracking with per-rod colors; result of every fight detected from the XP popup.
- **Auto select rod** before every cast (fixes Fisch getting stuck on long sessions).
- **Area editor (F2)**: Fish box, Shake box, Cast bar box - fits any resolution.
- Detection FPS presets for weak and strong PCs, custom hotkeys, rod profiles,
  English / Russian interface, status overlay on top of the game.
- Update check via GitHub Releases, debug mode for bug reports (off by default).

---

## Before release (internal test builds)

### 8.6.2
- Shake: the search box now covers almost the whole screen (buttons spawn
  near the edges too; with the old box the macro clicked one or two and
  "lost" the rest). Old default boxes are updated automatically.
- Shake: a button next to other white text is still recognised (the ring is
  checked around nearby centers too).
- New Discord invite link.

### 8.6.1
- Shake (Pixel / Circle) stopped after 3-5 clicks:
  - a button that reappeared on the same spot was skipped for a whole second;
    now the spot is free as soon as the button disappears, and a button that
    stays visible is clicked again after the duplicate timeout;
  - a large white object near the screen center (clothes, snow, the moon)
    used up the search and buttons near the edges were never checked; now
    every white spot in the shake box is checked.

### 8.6
- Welcome screen: a handwritten "hello" draws itself on first launch
  (replay: Extra -> About -> Say hello).
- Debug mode, off by default: no snapshots or trace.csv unless you turn it on.
- `klev.ini` / `klev.log` renamed to `scarlet.ini` / `scarlet.log` (automatic).
- Update check via GitHub Releases, with a download button in the header.

### 8.5
- Perfect cast: fixed the fill reading. When the bar sat between pixels, the
  green zone was 1-2 px wider than the fill and the fill was missed in 2 of 3
  frames; speed came out as 6000-10000 px/s and the rod was released far too
  early. Frames without a readable fill are now skipped, impossible speeds are
  ignored.
- Perfect cast: white UI text below the bar is no longer taken for the fill.
- Discord link in Extra -> Community.

### 8.4
- Perfect cast aims at the green zone itself: measures the fill speed of every
  cast and learns the game's input delay automatically.
- The cast bar is recognised as "green zone + white column of the same width",
  works on dark scenes and over the blue glow.

### 8.3
- `cast_miss.png` snapshot and whole-screen search when the cast bar is not found.
- Cast bar box in the area editor (F2).

### 8.2
- Auto select rod: bag -> rod before every cast (on by default).
- Area editor (F2): Fish box, Shake box, Cast bar box - drag to fit any resolution.

### 8.1
- Cast styles: Normal and Perfect, with editable step sequences.

### 8.0
- Shake: Pixel, Navigation, Circle, Disabled - detection of both button styles.
- Detection FPS per stage (cast / shake / fish) and Low / Medium / High presets.

### 7.x
- Scarlet hub control panel: side menu, English / Russian, rod picker,
  Line and Color tracking, on-screen markers, custom hotkeys, mss / dxcam capture.
