"""
Lunii MP3Map — logique de parsing et génération.
Format reverse-engineered et vérifié sur 442 fichiers (13 111 enregistrements, 100 % de correspondance).
"""

import struct
import math
from pathlib import Path

# Constantes firmware Lunii
SAMPLES_PER_FRAME = 1152   # samples par trame MPEG Layer III
INTERNAL_RATE     = 88200  # 2 × 44 100 Hz — taux interne du firmware

# Tables MPEG Layer III
MPEG1_BITRATES = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]
SAMPLE_RATES   = [44100, 48000, 32000, 0]


# ---------------------------------------------------------------------------
# Outils MP3
# ---------------------------------------------------------------------------

def detect_id3_size(data: bytes) -> int:
    """Lit la taille du tag ID3v2 en tête de fichier. Retourne 0 si absent."""
    if len(data) < 10 or data[:3] != b'ID3':
        return 0
    # Entier synchsafe sur 4 octets (7 bits utiles par octet)
    size = (
        ((data[6] & 0x7F) << 21) |
        ((data[7] & 0x7F) << 14) |
        ((data[8] & 0x7F) <<  7) |
         (data[9] & 0x7F)
    )
    return 10 + size  # 10 = entête ID3 fixe


def parse_mp3_frames(data: bytes, id3_offset: int) -> list:
    """
    Parse toutes les trames MPEG Layer III à partir de id3_offset.
    Retourne une liste de tuples (byte_offset, frame_index).
    frame_index 0 = trame Xing/Info (header VBR silencieux).
    """
    frames = []
    pos = id3_offset
    fi = 0

    while pos + 4 <= len(data):
        b0, b1, b2 = data[pos], data[pos + 1], data[pos + 2]

        # Mot de synchronisation MPEG : 0xFF + 3 bits à 1
        if b0 == 0xFF and (b1 & 0xE0) == 0xE0:
            mpeg_ver = (b1 >> 3) & 0x03  # 3=MPEG1, 2=MPEG2, 0=MPEG2.5
            layer    = (b1 >> 1) & 0x03  # 1=Layer III uniquement

            if layer != 1:
                pos += 1
                continue

            br_i    = (b2 >> 4) & 0x0F
            sr_i    = (b2 >> 2) & 0x03
            padding = (b2 >> 1) & 0x01

            br = MPEG1_BITRATES[br_i] * 1000
            sr = SAMPLE_RATES[sr_i]

            if br == 0 or sr == 0:
                pos += 1
                continue

            if mpeg_ver == 3:   # MPEG1
                frame_size = (144 * br // sr) + padding
            else:               # MPEG2 / MPEG2.5
                frame_size = (72 * br // sr) + padding

            if frame_size < 21:
                pos += 1
                continue

            frames.append((pos, fi))
            fi += 1
            pos += frame_size
        else:
            pos += 1

    return frames


def get_mp3_info(mp3_path: str) -> dict:
    """
    Analyse un fichier MP3 et retourne ses métadonnées :
      id3_offset, frame_count, duration_s, avg_bitrate, file_size, frames
    """
    with open(mp3_path, 'rb') as f:
        data = f.read()

    file_size  = len(data)
    id3_offset = detect_id3_size(data)
    frames     = parse_mp3_frames(data, id3_offset)
    N          = len(frames)

    duration_s  = N * SAMPLES_PER_FRAME / 44100 if N > 0 else 0.0
    audio_bytes = file_size - id3_offset
    avg_bitrate = int(audio_bytes * 8 / duration_s) if duration_s > 0 else 0

    return {
        'file_size':   file_size,
        'id3_offset':  id3_offset,
        'frame_count': N,
        'duration_s':  duration_s,
        'avg_bitrate': avg_bitrate,
        'frames':      frames,
    }


# ---------------------------------------------------------------------------
# Génération mp3map
# ---------------------------------------------------------------------------

def compute_x_factor(frame_count: int) -> int:
    """Heuristique pour le facteur X selon le nombre de trames."""
    if frame_count > 100:
        return 34
    elif frame_count > 50:
        return 24
    else:
        return 22


def generate_mp3map(mp3_path: str, x_factor: int = None) -> tuple:
    """
    Génère le contenu binaire d'un fichier .mp3map depuis un MP3.

    Format binaire :
      Header 12 octets  : [total_units u32 LE][id3_offset u32 LE][0 u32 LE]
      N × 8 octets      : [byte_offset u32 LE][frame_index*2304 u32 LE]

    Retourne (bytes_content, info_dict).
    """
    with open(mp3_path, 'rb') as f:
        data = f.read()

    id3_offset = detect_id3_size(data)
    frames = parse_mp3_frames(data, id3_offset)
    N = len(frames)

    if x_factor is None:
        x_factor = compute_x_factor(N)

    total_units = (2 * N - x_factor) * SAMPLES_PER_FRAME
    num_records = total_units // INTERNAL_RATE

    frame_by_index = {fi: off for (off, fi) in frames}

    records_bytes = []
    records_info  = []
    for k in range(1, num_records + 1):
        target_fi = math.ceil(k * INTERNAL_RATE / (2 * SAMPLES_PER_FRAME))
        if target_fi in frame_by_index:
            byte_off = frame_by_index[target_fi]
            unit_pos = target_fi * 2 * SAMPLES_PER_FRAME
            records_bytes.append(struct.pack('<II', byte_off, unit_pos))
            records_info.append({
                'index':       k,
                'time_s':      unit_pos / INTERNAL_RATE,
                'frame_index': target_fi,
                'byte_offset': byte_off,
                'unit_pos':    unit_pos,
            })

    content = struct.pack('<III', total_units, id3_offset, 0) + b''.join(records_bytes)

    info = {
        'frame_count': N,
        'x_factor':    x_factor,
        'total_units': total_units,
        'id3_offset':  id3_offset,
        'num_records': len(records_info),
        'duration_s':  total_units / INTERNAL_RATE,
        'records':     records_info,
        'file_size':   len(content),
    }

    return content, info


# ---------------------------------------------------------------------------
# Parsing mp3map existant
# ---------------------------------------------------------------------------

def parse_mp3map(path: str) -> dict:
    """Parse un fichier .mp3map et retourne son contenu structuré."""
    with open(path, 'rb') as f:
        data = f.read()

    if len(data) < 12:
        raise ValueError(f"Fichier trop petit ({len(data)} o) — mp3map invalide.")

    total_units, id3_offset, reserved = struct.unpack_from('<III', data, 0)
    duration_s = total_units / INTERNAL_RATE

    records = []
    pos = 12
    idx = 1
    while pos + 8 <= len(data):
        byte_off, unit_pos = struct.unpack_from('<II', data, pos)
        frame_index = unit_pos // (2 * SAMPLES_PER_FRAME)
        records.append({
            'index':       idx,
            'time_s':      unit_pos / INTERNAL_RATE,
            'frame_index': frame_index,
            'byte_offset': byte_off,
            'unit_pos':    unit_pos,
        })
        pos += 8
        idx += 1

    return {
        'total_units': total_units,
        'id3_offset':  id3_offset,
        'reserved':    reserved,
        'duration_s':  duration_s,
        'num_records': len(records),
        'records':     records,
        'file_size':   len(data),
    }
