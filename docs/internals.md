# DeepSeek Peak / Off-Peak — Windows glass widget

A small always-on-top desktop card that answers one question: **is DeepSeek
charging me peak or off-peak rates right now, and how long until it changes?**

![the widget on the desktop](doc/preview-glass.png)

**Drag anywhere on the card to move it**, drag the corner grip to resize, `⚙`
opens the options, `−` hides it to the tray, `×` quits. Position, size and every
switch are remembered, so a restart puts the card back exactly as you left it.

The widget deliberately has **no taskbar button and no Alt+Tab entry** (it is a
Windows tool window). The tray icon is how you reach it — left-click to show or
hide, right-click for the menu.

## What it shows

* **The countdown, and nothing else.** Off-peak / peak, the time left in the
  current price window, and where the next one starts, in your own timezone.
* **Everything else is behind the gear** — timezone and the four switches
  (Always on top, Start with Windows, Notify, Glass), so the collapsed widget
  stays a countdown:

![the options sheet](doc/preview-settings.png)

* **Neutral glass, no colour in the material.** The window is transparent and
  Windows' acrylic blur shows the desktop through it; the card itself is a
  colourless dark glass with **white** text, a hairline rim and one faint
  highlight along the top. Nothing in the material is tinted — colour appears in
  three small places only, where it carries meaning: the status dot, the badge,
  and the arrow (green for off-peak, red for peak).

  There are two densities of the same glass, and the host picks one by sampling
  the desktop around the card (every 1.5 s, with a dead zone so a window moving
  behind it cannot make it flicker):

  | backdrop | glass | measured contrast of white text |
  | --- | --- | --- |
  | dark (under 0.40 luminance) | `clear` — thin, most see-through | **20.5:1** |
  | mid | keeps what it has | |
  | bright (over 0.55) | `dense` — the same glass, thicker | **6.7:1** |

  The densities exist because white text over a white window needs a darker
  backdrop: densifying is the only way to keep the text readable *and* the card
  colourless. Set `"auto_density": false` in `config.json` to pin `clear`.
* **It stays on the desktop.** "Show desktop" (Win+D, or the three-finger swipe)
  is two separate attacks on a window, and both are handled with "Always on top"
  **off**: the shell's minimize is refused by a window-procedure hook, and when the
  shell raises the desktop layer *over* the card (which is what actually makes it
  look like the widget "went with the desktop"), the card borrows topmost for as
  long as the desktop covers it and hands it straight back — so it reappears on the
  wallpaper and then goes behind your windows again. `--ui-test` presses Win+D for
  real to check this. Turn it off from the tray menu (*Stay on desktop*) if you
  would rather it disappear with everything else.
* **One copy.** A named mutex means a second launch exits instead of adding a
  second identical window (two copies share the title, the preferences and the log,
  and end up fighting over the size).

The card opens at **404 × 176 CSS px** and is resizable from **200 × 84** up to
the size of your desktop, by dragging the grip in its bottom-right corner. It
adapts to whatever size it is given — the type steps down and the elements
re-space via container queries — and the host re-checks that the page's viewport
matches that size (see *Notes*). At the smallest sizes the options sheet covers
the card and scrolls, and gets its own close button.

## The rule this implements

Taken verbatim from [DeepSeek's pricing page](https://api-docs.deepseek.com/quick_start/pricing):

> Off-peak rates are half of the peak rates. Peak hours are **01:00 - 04:00 and
> 06:00 - 10:00 UTC, Monday through Friday**, excluding **Chinese public
> holidays**. All other hours are off-peak, including weekends and Chinese public
> holidays in full.

Three consequences the code is built around (all covered by tests):

1. The windows are anchored to **UTC**, so changing the timezone moves the
   labels, never the pricing. Peak is 06:30–09:30 and 11:30–15:30 in India,
   04:00–07:00 and 09:00–13:00 in Qatar, 02:00–05:00 and 07:00–11:00 in the UK
   (summer) — the same two UTC windows every time.
2. Weekends are never peak, so a Friday evening countdown runs to **Monday**.
3. Chinese public holidays are off-peak all day, which merges into one very long
   window (National Day 2026: Wed 30 Sep 15:30 IST → Thu 8 Oct 06:30 IST).

## Running it

| Command | What it does |
| --- | --- |
| `run.cmd` | Starts the widget with no console window |
| `run-debug.cmd` | Starts it with a console + devtools, for troubleshooting |
| `install-autostart.ps1` | Adds it to your Startup folder |
| `uninstall-autostart.ps1` | Removes it again |

The tray icon is a status dot (mint = off-peak, coral = peak) whose tooltip
carries the live countdown; its menu can show/hide the widget, toggle
always-on-top, autostart and notifications, open this folder, and quit.

One-time setup (already done in this folder):

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Verified on Windows 11 with Python 3.14.6, pywebview 6.2.1 (Edge WebView2) and
pystray 0.19.5. The display this was tuned on runs at 150% scaling.

## Timezones

| Option | Zone | Notes |
| --- | --- | --- |
| Auto | your Windows timezone | Windows ids are mapped to IANA (`Arab Standard Time` → `Asia/Qatar`, `India Standard Time` → `Asia/Kolkata`, `GMT Standard Time` → `Europe/London`, …); if the id is unknown it falls back to the current UTC offset |
| Qatar | `Asia/Qatar` | UTC+3, no DST |
| India | `Asia/Kolkata` | UTC+5:30, no DST |
| UK | `Europe/London` | follows GMT/BST |
| Beijing | `Asia/Shanghai` | UTC+8 — the calendar the holiday rule is published on |
| UTC | `UTC` | as DeepSeek documents it |

`tzdata` is a dependency because Windows ships no IANA database.

## Keeping the holiday calendar current

`holidays.json` holds the Chinese public holidays used by the rule, generated
from the State Council announcements (via the
[holiday-cn](https://github.com/NateScarlet/holiday-cn) dataset, which records
the gov.cn paper URL for each year). Only real public holidays (`isOffDay`) are
kept — the make-up working weekends (调休) are not holidays to DeepSeek and are
deliberately excluded.

```bat
.venv\Scripts\python.exe tools\build_holidays.py --fetch
```

Ships 2025 + 2026 (61 days). 2027 is not published yet; re-run the command after
the State Council announces it and the new dates arrive with the dataset.

## Layout

```
app.py                    window host: glass, tray, notifications, prefs, geometry, autostart
schedule.py               THE engine: peak/off-peak windows, countdown, payload, formatting
holidays.json             CN public holidays (generated, re-runnable)
ui/index.html             the card and the options sheet
ui/styles.css             the design (glass tints, mode cross-fade)
ui/widget.js              renderer, local countdown, and the boot/interaction trace
tools/build_holidays.py   regenerates holidays.json
tools/capture-window.ps1  grabs the widget's on-screen pixels (verifies the glass)
tools/notify-test.py      raises a tray notification on demand
tests/test_schedule.py    24 engine tests
config.json               your preferences (created on first run)
widget.log                runtime log — look here first when something is wrong
```

`schedule.py` is the single source of truth: the countdown, the tray tooltip and
the notifications all come from it, and `ui/widget.js` only counts seconds
locally between the payloads it receives. It has no third-party imports and
makes no network calls.

### Useful commands

```bat
.venv\Scripts\python.exe app.py --self-check          :: environment + one line per timezone
.venv\Scripts\python.exe app.py --state --tz UK       :: the whole payload as JSON
.venv\Scripts\python.exe app.py --state --at 2026-10-02T02:00:00Z
.venv\Scripts\python.exe app.py --dump-preview        :: refresh the browser preview payload
.venv\Scripts\python.exe app.py --ui-test             :: open it, click every switch, report, exit
.venv\Scripts\python.exe -m unittest discover -s tests -t .
```

`--at` pins the instant, which is how the peak card, holiday weeks and long
weekends were checked without waiting for them to happen. `--ui-test` clicks the
switches through the real DOM (the exact path a user's click takes), checks from
the OS that there is no taskbar button, drives the move and resize gestures
through the same loop the mouse drives (with synthetic input, so it never touches
your cursor), and finally minimizes the window two ways — the shell's message and
a direct minimize — to prove the card stays on screen.

## Verifying a change

* **Engine** — `python -m unittest discover -s tests -t .` (24 tests: window
  boundaries, weekends, holidays, UK DST, the payload, plus a golden test that
  reproduces the reference card frame for frame).
* **Layout** — `_ref\pw\widget-shots.mjs` renders the real page in Chrome at the
  shipping size, the minimum size and a large size, in both modes with the sheet
  open and closed, and asserts nothing overflows / clips / truncates / falls
  outside the window (including that the options sheet always fits inside the
  card). The same run measures **text legibility**: it screenshots the card over
  a pure white and a pure black desktop at both glass densities, decodes the
  pixels, and requires the digits to clear 4.5:1 (3:1 for the thick glass over
  white, where the 28-54 px digits count as large text). Serve `ui/` first:
  `python -m http.server 3010 --directory ui`.
* **Glass** — only a window capture can show it, because DWM draws the blur
  behind the web content: `powershell -File tools\capture-window.ps1 -Out shot.png`.
  The widget normally sits *behind* other windows ("Always on top" is off by
  default), so use `tools\capture-topmost.ps1` instead: it raises the card for a
  moment, grabs it over the real wallpaper, and puts it back.
* **Density** — `tools\probe-vibrancy.py` prints the luminance the sampler reads
  around the window, the raw pixel samples, and the glass density that follows.
* **Desktop** — `tools\probe-desktop.py [--send]` reports what is under the card's
  own centre (and therefore whether the desktop is covering it); `--send` presses
  Win+D, reports again, and presses it again to put your desktop back.
* **Log** — the widget runs windowless, so it writes a boot and interaction trace
  to `widget.log` (viewport size, every switch and chip click, price flips, and
  anything that failed). The log is truncated at 256 KB.

## Notes and limits

* Only the **schedule** is modelled. No prices are shown and no API key is needed
  or read; if DeepSeek changes the windows, `PEAK_WINDOWS_UTC` at the top of
  `schedule.py` is the one place to edit.
* Off-peak is reported as "half price" because that is exactly what the
  documentation states.
* **Window sizing is not something to trust.** pywebview asks WinForms for
  `size × dpi_scale`, WinForms then applies its own DPI auto-scaling, and the
  first build of this widget ended up with a 390 × 139 viewport when 404 × 176
  was requested — which silently pushed the controls at the bottom of the card
  outside the window, where they could not be clicked. The host now (a) sets the
  client area exactly with `AdjustWindowRectEx` + `SetWindowPos`, (b) reads the
  page's real `innerWidth/innerHeight` back and corrects if it disagrees, and
  (c) re-checks after any resize. The log prints which of those happened.
* **Always-on-top is re-asserted** after the window appears: WinForms drops
  `TopMost` when the frameless border style is applied.
* **Moving and resizing are done by the host, not by pywebview.** Its `easy_drag`
  never actually moved this window, and the OS resize loop cannot run on a
  frameless window either (`WS_THICKFRAME` is absent, so the `HT…` resize
  hit-tests do nothing). Instead a worker thread polls the cursor while the left
  button is held and repositions — or resizes in whole CSS pixels — with
  `SetWindowPos`. The corner grip exists because a borderless window has no edges
  to grab, and resizing is clamped to `MIN_SIZE` and the desktop.
* `MIN_SIZE` is `(200, 84)`: the height is set by the options sheet. Below that
  the switches would have nowhere to go, so there is a compact sheet layout for
  short cards (see the `max-height` query in `ui/styles.css`).
* The card cannot use `WS_EX_LAYERED`/`TransparencyKey` tricks for the glass
  because WebView2 renders through DirectComposition; acrylic is the only blur
  that works, and it is applied to the whole window, which is why the card has to
  be exactly the size of the window.
* **Staying on the desktop** is two things, because "show desktop" attacks twice: a
  window-procedure hook that refuses `SC_MINIMIZE`, and a 4 Hz watcher that (a)
  un-hides the card with `SW_SHOWNOACTIVATE` if the shell got there another way and
  (b) notices when the shell has raised the *desktop layer* over the card — which
  leaves it visible, un-minimized and hidden behind the wallpaper — and lifts it
  back by borrowing topmost until the desktop is gone again. Nothing can be
  inserted "just above the desktop": the icon host sits above the desktop window
  that owns it (measured, not assumed). Detection asks what is under several points
  of the card, so an app window covering it is never mistaken for show-desktop. Both
  are testable — `--ui-test` exercises them, including a real Win+D — and the watcher
  only acts while the card is *meant* to be visible, so hiding it to the tray still
  works.
* The size guard re-reads the size it is enforcing on every pass and stands down
  while a gesture is active. It used to capture the expected size once, so a resize
  that landed while it was sleeping was treated as a mistake and reverted.
* Window lookups match the process id as well as the title (`find_own_window`): with
  two copies the plain title lookup hands back the other one's window, and the UI
  test then drove one window while reading the page of another.
* Tuning the glass: `GLASS_TINT_CLEAR` / `GLASS_TINT_DENSE` (ABGR — alpha plus
  blue/green/red, the native acrylic tints) in `app.py`, and the `--shade-*`
  stops in `ui/styles.css`. More alpha = thicker and more readable, less = more
  legible; less = more desktop showing through. The current values were picked so
  that dark text clears 4.5:1 over any desktop, which is what the harness checks.
* Unhandled errors go to `widget.log`; under `pythonw` there is nowhere else for
  them to go.
