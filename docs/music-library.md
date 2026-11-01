# Music library & editor audio tracks

The editor timeline can hold any number of **audio tracks** (music or sound effects) under the
built-in video / subtitle / dub / original lanes. Clips on those tracks reference files of a
**music library** that is shared by every project.

## Where things live

| What | Where |
| --- | --- |
| Library index (title, artist, duration, tags, source, license, attribution) | `storage/music-library/index.json` |
| Audio files (normalized to 44.1 kHz stereo MP3 on import) | `storage/music-library/files/<id>.mp3` |
| Waveform peaks for the timeline | `storage/music-library/peaks/<id>.json` |
| Provider settings (Jamendo `client_id`) | `storage/music-library/settings.json` |
| Tracks and clips of a project | `storage/editor/<job>/workspace.json` → `audio_tracks` |

Clips reference assets by id; nothing is copied into the project. Deleting an asset that a
project still uses makes that project's render stop with a message naming the missing track
(“Thiếu file nhạc …”) — re-import the file or remove the clip.

### Workspace schema

```jsonc
"audio_tracks": [{
  "id": "atr-…", "name": "Nhạc nền", "kind": "music" | "sfx",
  "muted": false, "locked": false, "volume": 100,          // percent, 0–200
  "clips": [{
    "id": "clip-…", "asset_id": "mus-…", "title": "…",       // title = display snapshot
    "start": 12.5,      // timeline seconds
    "in": 3.0,          // offset into the file
    "duration": 40.0,
    "volume": 60,       // percent, 0–200
    "fade_in": 1.0, "fade_out": 2.0, "loop": false,
    "ducking": { "enabled": true, "amount_db": -12, "attack": 0.3, "release": 0.6 }
  }]
}]
```

Workspaces saved before this feature have no `audio_tracks`; they load as `[]` and render
exactly as before (the renderer command is unchanged when there is no music). Solo is a
preview-only switch and is never saved. Edits go through the normal `PATCH /jobs/{id}/editor`
(`audio_tracks` = full list, or `audio_ops` = granular `add_track | update_track |
delete_track | add_clip | update_clip | delete_clip | move_clip`) and are part of undo/redo.

## Sources

* **Your files** — Import button or drag files onto the Music tab: MP3, WAV, M4A/AAC, OGG/Opus,
  FLAC (≤ 60 min). License is recorded as “uploaded” (`own`): you are responsible for the rights.
* **Openverse** (<https://api.openverse.org/v1/audio/>) — WordPress' open media search, indexing
  Creative Commons audio from Jamendo, Freesound, ccMixter, Wikimedia… No account or key.
  Anonymous limits are about **20 requests/minute and 200/day**; searches are cached for
  10 minutes. The API is sometimes slow (5–30 s) or briefly returns 5xx — the app reports it and
  the import falls back to the record already returned by the search.
  * “Music” searches `category=music`; “Sound effects” searches the Freesound source (Openverse's
    `sound_effect` category is sparsely tagged).
  * The commercial filter (on by default) asks only for `cc0, pdm, by, by-sa`.
* **Jamendo** (optional) — needs a free `client_id` from <https://devportal.jamendo.com/>,
  entered in the Music tab (stored locally, returned masked). NC/ND tracks are filtered out when
  the commercial filter is on. Note that Jamendo sells separate licenses for commercial use of
  its catalogue; only CC0 / CC BY / CC BY-SA tracks are safe for monetized videos.

“Thêm vào thư viện” downloads the file through the backend (metadata is re-read from the
provider, never trusted from the browser), normalizes it and keeps the license, license URL,
source page and the provider's attribution sentence.

## Licenses and your attribution duty

| License | Monetized video | Credit required |
| --- | --- | --- |
| CC0, Public Domain Mark | yes | no (courtesy credit is nice) |
| CC BY | yes | **yes** |
| CC BY-SA | yes (the *music* stays BY-SA) | **yes** |
| CC BY-NC / NC-SA / NC-ND, BY-ND | **no** (NC = non-commercial; ND = no derivatives — mixing/cutting counts as a change) | yes |

The **Ghi công nhạc** box (Music tab → My library) builds the credit text for every CC BY /
BY-SA asset used in the current project — title, author, license with link and source page —
ready to paste into the video description, and warns if the project uses NC/ND music.
Openverse metadata is provided as-is; check the source page when in doubt.

## Mixing and auto-ducking

Rendering pre-mixes all audible clips into one file (`storage/editor/<job>/music.flac`, filter
graph saved next to it) and adds it to the usual voice + original-audio mix with
`amix normalize=0` followed by `alimiter=limit=0.95` so the sum never clips. Per clip:
`atrim` (in/duration; looping clips read the file with `-stream_loop -1`), clip × track volume,
linear `afade` in/out, `adelay` to the timeline position, then the ducking envelope.

Ducking is **volume automation computed from the known voice intervals**, not a sidechain
compressor, so it is exact and the editor preview plays the same thing:

1. Voice intervals = every voiced, non-muted cue `[audio_start, audio_start + audio_duration]`
   (none when the dub track is switched off).
2. Intervals closer than `attack + release` are merged, so the music doesn't pump between lines.
3. Envelope `d(t)` ramps 0→1 over `[s − attack, s]`, holds during the line and ramps 1→0 over
   `[e, e + release]`.
4. Gain = `1 − (1 − 10^(amount_db/20)) · d(t)` — e.g. −12 dB → 0.25 under the voice.

Music tracks duck by default (−12 dB, 0.3 s / 0.6 s); sound-effect tracks don't. Measured on a
real render: −14 dB setting → −14.0 dB difference inside lines, 0.0 dB between them.

The preview (`screens/editor/music/MusicPlayer.ts`) plays clips through Web Audio from
pre-decoded buffers, picks them at `heardTime(t)` (output-latency compensation, like the dub
player), tolerates small clock jitter before re-syncing, and drives each clip's gain with the
same fade × ducking formula. Mute / solo apply to the preview; solo is never rendered.
