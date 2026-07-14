<div align="center">

# 🎧 SpotiPOD

### Record your Spotify playlists to MP3 and load them straight onto your iPod.

![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)
![Version](https://img.shields.io/badge/version-0.0.6-orange)

</div>

---

**Spotify keeps deleting songs from your playlists?** SpotiPOD records them to high-quality MP3 on
your machine — a backup you own forever — and can load them **directly onto a classic iPod** without
even opening the Music app. And if you ever quit Spotify, your music comes with you.

```text
   SPOTIFY
      S  Explorar y grabar mis playlists
      B  Buscar en Spotify y grabar
   GRABAR
      1  Playlist / álbum / track  → Apple Music
      2  Playlist / álbum / track  → iPod directo
      3  Playlist / álbum / track  → solo local
      4  Actualizar un respaldo    (solo pistas nuevas)
   iPod
      5  Cargar una playlist grabada al iPod
      ...
```

---

## ✨ Features

- 🎵 **Record whole playlists, albums or tracks** — paste a URL/URI/ID, or browse & search your own library.
- 💿 **High quality** — MP3 320 kbps with rich ID3 metadata (cover, track number, disc, year, album artist).
- 🧹 **Clean files** — silence detection warning + automatic trim; volume set to 100% for even levels.
- 📊 **Progress & ETA** while recording, and safe resume (already-recorded tracks are skipped).
- 📱 **Load straight to the iPod database** (no Music.app needed) on classic disk-mode iPods.
- 🔄 **Back up & restore** the iPod database, and copy the music **off** an iPod back to your Mac.
- 🎛️ **Interactive menu** or plain CLI flags — plus a config file for your defaults.
- 🌐 **Offline forever** — your backup plays without internet, on the iPod or any player (M3U export).

## 📋 Requirements

| | |
|---|---|
| **OS** | macOS, Windows 10/11 or Linux |
| **Python** | 3.9+ (3.11+ for the config file) |
| **Spotify** | Premium account (the Web API only controls playback on Premium) |
| **Credentials** | A Spotify app: Client ID + Client Secret |
| **ffmpeg** | For MP3 conversion |
| **Virtual audio** | BlackHole (macOS) / VB-Cable (Windows) set as Spotify's output |

## 🚀 Installation

### 1. Dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

> [!NOTE]
> **Python 3.13+**: pydub needs the `audioop` module, removed from the stdlib in 3.13.
> It's already in `requirements.txt` (`audioop-lts`), but install it manually if needed.

**macOS** — install BlackHole (stereo is enough) and ffmpeg:

```bash
brew install blackhole-2ch ffmpeg
```

### 2. Route the audio (macOS)

SpotiPOD records whatever the desktop plays, so the audio must reach BlackHole:

1. In **Audio MIDI Setup**, create a **Multi-Output Device** with BlackHole **and** your speakers.
2. In **System Settings → Sound → Output**, select that **Multi-Output Device** (so you hear it *and* it records).
3. Keep BlackHole, the Multi-Output and your speakers at the **same sample rate** (SpotiPOD auto-detects it).
4. Turn on **Do Not Disturb** so notification "dings" don't leak into a track.

> [!WARNING]
> Selecting only the plain speakers is the #1 cause of **silent recordings** — the Multi-Output must be the system output. Verify with menu option *Test audio capture* while music is playing.

### 3. Spotify credentials

1. Create an app at the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
2. Add the Redirect URI **`http://127.0.0.1:8080`** (Spotify no longer accepts `http://localhost`).
3. Put your credentials in a `.env` file (auto-loaded) — or use menu option *Configure credentials*:

```env
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
# optional:
# SPOTIFY_REDIRECT_URI=http://127.0.0.1:8080
```

## 🎛️ Usage

Run with **no arguments** for the interactive menu:

```bash
python spotipod.py
```

<div align="center">

| SPOTIFY | GRABAR | iPod | LOCAL | UTILIDADES |
|---|---|---|---|---|
| Browse my playlists | → Apple Music | Load recorded playlist | Export M3U | Test capture |
| Search & record | → iPod directo | Back up / restore DB | Verify recordings | Configure creds |
| | → local only | Manage playlists | Manage recordings | Diagnostics |
| | Update a backup | Back up iPod music · Eject | | |

</div>

Or drive it directly from the CLI:

```bash
python spotipod.py https://open.spotify.com/playlist/XXXX   # a playlist
python spotipod.py https://open.spotify.com/album/XXXX      # an album
python spotipod.py spotify:track:XXXX                        # a single track
python spotipod.py --ipod  <url>       # record + load straight to the iPod (no Music.app)
python spotipod.py --no-apple-music <url>   # record locally only
python spotipod.py --bitrate 256k -v <url>  # custom bitrate + verbose
python spotipod.py --help                    # all options
```

> [!TIP]
> **Record now, load later:** use *record → local only* without the iPod plugged in
> (files land in `Playlist/<name>/`). Later, plug the iPod in and use *Load a recorded
> playlist to the iPod* to pick it and name the playlist on the device.

## 🎚️ Audio quality

Files are saved (and copied to the iPod **as-is**, no re-encoding) as **MP3 320 kbps CBR, 16-bit
stereo** at the capture device's sample rate. Capture is a **bit-perfect digital loopback** through
BlackHole — no analog degradation.

> [!NOTE]
> The real ceiling is the source: Spotify streams lossy Ogg Vorbis (~320 kbps on Premium), so a
> 320 kbps MP3 preserves essentially everything Spotify delivers, but it isn't lossless. Enable
> **"Normalize volume"** in Spotify for even loudness. YouTube-recovered fallbacks are 192 kbps.

## ⚙️ Config file (optional)

Drop a `spotipod.toml` (current folder) or `~/.spotipod.toml` to set your defaults.
Precedence: **CLI flag > config file > built-in default** (requires Python 3.11+).

```toml
output_dir = "Playlist"
bitrate = "320k"
# sample_rate = 48000     # omit to auto-detect
apple_music = true
ipod = false              # true = load straight to the iPod, or "/Volumes/iPod"
```

## 📦 Install as a command

```bash
pip install -e .
spotipod            # interactive menu, from anywhere
spotipod --help
```

## 🧪 Tests

The iTunesDB reader/writer — the riskiest part, since it writes to your iPod — is covered by tests:

```bash
pip install -e ".[dev]"
pytest
```

They run on a synthetic in-memory database (no personal data); if an iPod is mounted, an extra
**byte-for-byte round-trip** test runs against its real `iTunesDB`.

## 📱 iPod compatibility

Direct loading (`--ipod`) writes the iPod's `iTunesDB` in pure Python. It works **only** on models
whose database needs **no signature** — the older click-wheel iPods (pre‑2007). Newer models store
a checksum in the database that SpotiPOD can't reproduce, so those must be synced through
**Music.app** instead (SpotiPOD still records the MP3s — you just import them with menu option 1 /
`--no-apple-music` off).

| iPod model | Direct load (`--ipod`) | How to load |
|---|---|---|
| iPod 1G–4G · Photo · Mini | ✅ Yes | Direct |
| **iPod Video (5G / 5.5G)** | ✅ Yes *(tested)* | Direct |
| iPod Nano 1G · 2G | ✅ Yes | Direct |
| iPod Nano 3G · 4G · 5G | ❌ No | **Music.app** |
| iPod Classic (6G · 6.5G · 7G) | ❌ No | **Music.app** |
| iPod Nano 6G · 7G | ❌ No | **Music.app** |
| iPod Touch · iPhone | ❌ No *(not disk mode)* | **Music.app / Finder** |

> [!NOTE]
> You don't need to know your model — SpotiPOD **auto-detects** compatibility from the connected
> iPod's database. It's shown in *Diagnostics*, and direct load **refuses unsupported models**
> (pointing you to Music.app) instead of writing a database the iPod would reject. It also backs
> up the `iTunesDB` before any write.

## 🛠️ Extra tools (`tools/`)

**Load straight to the iPod database (no Music.app)** — for the supported models in the
**iPod compatibility** table above. Backs up the `iTunesDB` before writing.

```bash
python tools/ipod_sync.py "My Playlist" --dir Playlist/MyPlaylist
python tools/ipod_sync.py "Test" --dry-run *.mp3     # preview, writes nothing
```

**Back up the music FROM an iPod to your Mac** — rebuilds a tidy `Artist/Album/Title` library from
the ID3 tags (something iTunes/Music can't do):

```bash
python tools/ipod_backup.py                    # auto-detect → ./iPod_Backup
python tools/ipod_backup.py --dest ~/Music/ipod
```

> [!IMPORTANT]
> After writing to the iPod, eject it (`diskutil eject /Volumes/iPod` or Finder) so it reloads the database.

## ❓ Troubleshooting

<details>
<summary><b>Recordings are silent</b></summary>

Make sure the **Multi-Output Device** (with BlackHole) is the **system output**, not the plain
speakers. Verify with `python tools/check_level.py` while music plays.
</details>

<details>
<summary><b>ffmpeg not found (pydub error)</b></summary>

Install ffmpeg — `brew install ffmpeg` (macOS), `sudo apt install ffmpeg` (Linux), or from
<https://ffmpeg.org/download.html> (Windows, add to PATH).
</details>

<details>
<summary><b>ModuleNotFoundError: No module named 'audioop' (Python 3.13+)</b></summary>

`pip install audioop-lts` (already in `requirements.txt`).
</details>

<details>
<summary><b>redirect_uri: Insecure / authentication error</b></summary>

Spotify no longer accepts `http://localhost`. Register **`http://127.0.0.1:8080`** in the
dashboard, matching `SPOTIFY_REDIRECT_URI`, and check your Client ID/Secret.
</details>

<details>
<summary><b>Audio sounds wrong / drifts out of sync</b></summary>

Sample-rate mismatch. Set BlackHole, the Multi-Output and your speakers to the same rate in
Audio MIDI Setup (or force it with `--sample-rate 48000`).
</details>

## 📜 Changelog

<details>
<summary><b>v0.0.6</b> — Spotify browser, record-only + load-later, updates, M3U, packaging, tests</summary>

- Browse/search your Spotify playlists and record one, several or all
- Record-only mode (no iPod needed) + load a recorded playlist to the iPod later, naming it
- Update a backup (record only new tracks); export M3U; manage local recordings
- Auto-detect the sample rate; wait/guide + wake the device when none is active
- Config file (`spotipod.toml`); record albums and tracks, not only playlists
- Redesigned interactive menu (color, live status) in 5 sections
- iPod: restore the database, list/delete playlists, add to an existing playlist
- Free-space check before loading; nicer diagnostics panel
- pytest suite for the iTunesDB reader/writer; packaging (`pip install`, `spotipod` command)
- Atomic MP3 conversion (no partial files on interruption); clearer progress counter
  (`pista X/N · nueva Y/M`); "verify recordings" can delete the silent ones to re-record
- Per-playlist progress file (recorded / remaining / failed) written after each track, with a
  resume banner — so a cut recording picks up right where it left off
- Recording integrity: while capturing, SpotiPOD checks via the API that Spotify is still
  playing the right track in sync; on pause / skip / drift it discards the take and retries
- Auto-detects iPod direct-load compatibility from the connected device and refuses unsupported
  models (Classic 6G/7G, Nano 3G+), pointing to Music.app, instead of writing a rejected DB
</details>

<details>
<summary><b>v0.0.5</b> — Direct iPod database loading</summary>

- `--ipod`: write the `iTunesDB` without Music.app (classic disk-mode iPods, no hash signature)
- Pure-Python iTunesDB reader/writer (`tools/itunesdb.py`)
- `tools/ipod_sync.py` (load to iPod, auto backup) and `tools/ipod_backup.py` (copy music off iPod)
- Per-track recording progress bar
</details>

<details>
<summary><b>v0.0.4</b> — Quality & robustness</summary>

- Record from the start of each track; capture full playlists (>100)
- Rich ID3 metadata; silence detection + auto-trim; volume to 100%; progress & ETA
- Accept IDs/URIs/URLs; YouTube-recovered tracks tagged & synced
- CLI flags, `.env`, logging, Ctrl+C cleanup; fixed env var names & redirect URI
</details>

<details>
<summary><b>v0.0.1 – v0.0.3</b></summary>

- **v0.0.3** — Apple Music support: import MP3s to the library and create a playlist for the iPod.
- **v0.0.2** — YouTube fallback when a track isn't playable on Spotify.
- **v0.0.1** — Record desktop audio to WAV → MP3, per-playlist folders, cover art, exception handling.
</details>

## ⚖️ License & legal

Released under the [MIT License](LICENSE). Intended for **personal use only** — do not redistribute,
share or sell recordings made with this tool. You are responsible for how you use it.

## 📮 Support

- ✉️ support@panicbots.com
- 🌐 [panicbots.com/spotimy](https://www.panicbots.com/spotimy)

<div align="center">
<sub>Built for keeping the music you love. 🎶</sub>
</div>
