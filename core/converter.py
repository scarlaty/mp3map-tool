"""
Wrapper FFmpeg pour la conversion audio vers le format MP3 compatible Lunii.
"""

import subprocess
import shutil


def check_ffmpeg() -> tuple:
    """
    Vérifie la disponibilité de ffmpeg.
    Retourne (disponible: bool, version: str).
    """
    if not shutil.which('ffmpeg'):
        return False, ""
    try:
        result = subprocess.run(
            ['ffmpeg', '-version'],
            capture_output=True, text=True, timeout=10
        )
        first_line = result.stdout.split('\n')[0] if result.stdout else ""
        return True, first_line
    except Exception:
        return False, ""


def convert_to_lunii_mp3(
    input_path:  str,
    output_path: str,
    quality:     int  = 4,
    sample_rate: int  = 44100,
    channels:    int  = 2,
    progress_cb       = None,
) -> tuple:
    """
    Convertit n'importe quel fichier audio en MP3 compatible Lunii via FFmpeg.

    Paramètres
    ----------
    quality     : qualité VBR libmp3lame 0-9 (0=meilleur ~245 kbps, 4=~165 kbps, 9=~65 kbps)
    sample_rate : fréquence cible en Hz (44100 recommandé pour Lunii)
    channels    : 1=mono, 2=stéréo
    progress_cb : callable(line: str) appelé pour chaque ligne stderr FFmpeg contenant 'time='

    Retourne
    --------
    (success: bool, message: str)
    """
    cmd = [
        'ffmpeg', '-y',
        '-i', input_path,
        '-codec:a', 'libmp3lame',
        '-q:a', str(quality),
        '-ar', str(sample_rate),
        '-ac', str(channels),
        output_path,
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
        )

        stderr_lines = []
        for line in proc.stderr:
            line = line.rstrip()
            stderr_lines.append(line)
            if progress_cb and ('time=' in line or 'Error' in line):
                progress_cb(line)

        proc.wait(timeout=300)

        if proc.returncode == 0:
            return True, '\n'.join(stderr_lines)
        else:
            return False, '\n'.join(stderr_lines)

    except subprocess.TimeoutExpired:
        proc.kill()
        return False, "Timeout : FFmpeg a mis trop de temps."
    except FileNotFoundError:
        return False, "FFmpeg introuvable. Installez ffmpeg et ajoutez-le au PATH."
    except Exception as e:
        return False, f"Erreur inattendue : {e}"
