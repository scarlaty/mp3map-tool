# mp3map-tool

GUI tool to generate and inspect `.mp3map` seek files used by audio storytelling devices.

The `.mp3map` format was fully reverse-engineered and verified against 442 original files (13,111 records — 100% match).

---

## Features

- **Convert** any audio file (MP3, WAV, FLAC, OGG, M4A…) to a compatible MP3 + `.mp3map` pair via FFmpeg
- **Generate** a `.mp3map` from an already-encoded MP3 without re-encoding
- **Inspect** any MP3 / `.mp3map` pair:
  - MP3 metadata (frame count, duration, bitrate, ID3 size)
  - mp3map header (`total_units`, `id3_offset`, record count)
  - Full seek table with timestamp, frame index, byte offset and unit position
  - Built-in audio player with seek slider synchronized to the table
  - Export seek table to CSV

---

## Requirements

| Dependency | Role | Install |
|---|---|---|
| Python 3.10+ | Runtime | [python.org](https://www.python.org) |
| pygame | Audio playback | `pip install pygame` |
| FFmpeg | Audio conversion | [ffmpeg.org](https://ffmpeg.org) + add to PATH |

> FFmpeg is only required for audio conversion. Generating a `.mp3map` from an existing MP3 and inspecting files work without it.

---

## Installation

```bash
git clone https://github.com/scarlaty/mp3map-tool.git
cd mp3map-tool
pip install -r requirements.txt
python main.py
```

---

## mp3map Format

Binary format — fully reverse-engineered.

### File structure

```
Offset   Size   Description
0        4      total_units  (u32 LE) — total duration in firmware internal units
4        4      id3_offset   (u32 LE) — ID3v2 tag size at the start of the .mp3 file
8        4      reserved     (u32 LE) — always 0
12+      N×8    seek records
```

### Seek record (8 bytes)

```
Offset   Size   Description
0        4      byte_offset  (u32 LE) — absolute byte position of the MP3 frame in the file
4        4      unit_pos     (u32 LE) — frame_index × 2 × 1152
```

### Key formulas

The firmware tracks audio position at **88,200 units/second** (2 × 44,100 Hz).

```
# One seek record per second of audio
frame_index  = ceil(k × 88200 / 2304)     # for record k = 1, 2, 3, ...
unit_pos     = frame_index × 2304

# Total duration
total_units  = (2 × N_frames − X) × 1152
num_records  = floor(total_units / 88200)
```

`X` is an empirical correction factor (typically 34 for files > 100 frames, 22 for shorter files).

---

## Project structure

```
mp3map-tool/
├── main.py              # GUI entry point (tkinter)
├── core/
│   ├── mp3map_core.py   # mp3map parsing and generation logic
│   └── converter.py     # FFmpeg wrapper
└── requirements.txt
```

