"""
Lunii MP3Map Tool
=================
Interface graphique pour :
  • Convertir n'importe quel fichier audio en MP3 + mp3map compatibles Lunii
  • Inspecter un couple MP3 / mp3map existant avec lecture audio intégrée

Dépendances : pygame (pip install pygame)
              ffmpeg (dans le PATH système)
"""

import os
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

# Ajout du répertoire courant dans sys.path pour les imports relatifs
sys.path.insert(0, os.path.dirname(__file__))

from core.mp3map_core import (
    generate_mp3map, parse_mp3map, get_mp3_info, compute_x_factor
)
from core.converter import check_ffmpeg, convert_to_lunii_mp3

# ---------------------------------------------------------------------------
# Tentative d'import pygame
# ---------------------------------------------------------------------------
try:
    import pygame
    pygame.mixer.pre_init(44100, -16, 2, 2048)
    pygame.mixer.init()
    PYGAME_OK = True
except Exception:
    PYGAME_OK = False


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def fmt_time(seconds: float) -> str:
    """Formate un nombre de secondes en MM:SS.mmm"""
    if seconds < 0:
        seconds = 0.0
    m = int(seconds) // 60
    s = seconds - m * 60
    return f"{m:02d}:{s:06.3f}"


def fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} o"
    elif n < 1024 * 1024:
        return f"{n / 1024:.1f} Ko"
    else:
        return f"{n / (1024 * 1024):.2f} Mo"


VBR_BITRATES = {
    0: "~245 kbps", 1: "~225 kbps", 2: "~190 kbps", 3: "~175 kbps",
    4: "~165 kbps", 5: "~130 kbps", 6: "~115 kbps", 7: "~100 kbps",
    8: "~85 kbps",  9: "~65 kbps",
}

# Palette Catppuccin Mocha
C = {
    'bg':       '#1e1e2e',
    'surface':  '#313244',
    'surface2': '#45475a',
    'overlay':  '#6c7086',
    'fg':       '#cdd6f4',
    'subtext':  '#a6adc8',
    'accent':   '#89b4fa',   # blue
    'green':    '#a6e3a1',
    'yellow':   '#f9e2af',
    'red':      '#f38ba8',
    'mauve':    '#cba6f7',
}


# ---------------------------------------------------------------------------
# Lecteur audio (wrapper pygame)
# ---------------------------------------------------------------------------

class AudioPlayer:
    """Wrapper pygame.mixer.music avec suivi de position."""

    def __init__(self):
        self.loaded_path = None
        self.duration    = 0.0
        self._offset     = 0.0   # position absolue lors du dernier play()
        self._paused_at  = None  # position absolue mémorisée lors d'une pause
        self._playing    = False
        self._paused     = False

    def load(self, path: str, duration: float = 0.0) -> bool:
        if not PYGAME_OK:
            return False
        try:
            pygame.mixer.music.stop()
            pygame.mixer.music.load(path)
            self.loaded_path = path
            self.duration    = duration
            self._offset     = 0.0
            self._paused_at  = None
            self._playing    = False
            self._paused     = False
            return True
        except Exception:
            return False

    def play(self, start: float = 0.0):
        if not PYGAME_OK or not self.loaded_path:
            return
        self._offset    = start
        self._paused_at = None
        self._paused    = False
        self._playing   = True
        pygame.mixer.music.play(start=start)

    def pause(self):
        if not PYGAME_OK:
            return
        self._paused_at = self.get_pos()
        self._paused    = True
        self._playing   = False
        pygame.mixer.music.pause()

    def unpause(self):
        """Reprend depuis la position mémorisée (re-play plus fiable que unpause)."""
        if not PYGAME_OK:
            return
        pos = self._paused_at if self._paused_at is not None else 0.0
        self._paused  = False
        self._playing = True
        self.play(pos)

    def stop(self):
        if not PYGAME_OK:
            return
        pygame.mixer.music.stop()
        self._playing   = False
        self._paused    = False
        self._offset    = 0.0
        self._paused_at = None

    def seek(self, seconds: float):
        """Saute à la position donnée (en secondes). Relance la lecture."""
        self.play(seconds)

    def is_playing(self) -> bool:
        return PYGAME_OK and pygame.mixer.music.get_busy()

    def is_paused(self) -> bool:
        return self._paused

    def get_pos(self) -> float:
        """Position de lecture courante en secondes."""
        if not PYGAME_OK:
            return 0.0
        if self._paused and self._paused_at is not None:
            return self._paused_at
        ms = pygame.mixer.music.get_pos()
        if ms < 0:
            return self._offset
        return self._offset + ms / 1000.0


# ---------------------------------------------------------------------------
# Application principale
# ---------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Lunii MP3Map Tool")
        self.geometry("950x720")
        self.minsize(800, 600)
        self.configure(bg=C['bg'])

        self._apply_style()

        self.ffmpeg_ok, self.ffmpeg_ver = check_ffmpeg()

        # Notebook (onglets)
        nb = ttk.Notebook(self)
        nb.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 4))

        self.tab_convert = ConverterTab(nb, self)
        self.tab_reader  = ReaderTab(nb, self)
        nb.add(self.tab_convert, text="  ⚡ Convertir  ")
        nb.add(self.tab_reader,  text="  🔍 Inspecter  ")

        # Barre de statut
        self.status_var = tk.StringVar()
        ttk.Label(self, textvariable=self.status_var,
                  style='Status.TLabel', anchor=tk.W
                  ).pack(fill=tk.X, padx=10, pady=(0, 6))

        if self.ffmpeg_ok:
            self.set_status(f"✓ {self.ffmpeg_ver}", C['green'])
        else:
            self.set_status("⚠  FFmpeg introuvable — conversion désactivée. "
                            "Téléchargez ffmpeg et ajoutez-le au PATH.", C['red'])

    def set_status(self, msg: str, color: str = None):
        self.status_var.set(msg)
        if color:
            # On ne peut pas changer la fg d'un Label ttk facilement — on recrée le style
            s = ttk.Style()
            s.configure('Status.TLabel', foreground=color)

    def _apply_style(self):
        s = ttk.Style(self)
        s.theme_use('clam')

        bg, fg = C['bg'], C['fg']
        surf, surf2 = C['surface'], C['surface2']
        acc = C['accent']

        s.configure('.',
                     background=bg, foreground=fg,
                     font=('Segoe UI', 9))

        s.configure('TFrame',      background=bg)
        s.configure('TLabel',      background=bg, foreground=fg)
        s.configure('TLabelframe', background=bg)
        s.configure('TLabelframe.Label',
                     background=bg, foreground=acc,
                     font=('Segoe UI', 9, 'bold'))

        s.configure('TNotebook',     background=bg, borderwidth=0)
        s.configure('TNotebook.Tab', background=surf, foreground=fg,
                     padding=[14, 6])
        s.map('TNotebook.Tab',
              background=[('selected', surf2)],
              foreground=[('selected', acc)])

        s.configure('TButton', background=surf2, foreground=fg, padding=[8, 4])
        s.map('TButton',
              background=[('active', acc), ('pressed', C['mauve'])],
              foreground=[('active', bg), ('pressed', bg)])

        s.configure('Accent.TButton',
                     background=acc, foreground=bg,
                     font=('Segoe UI', 10, 'bold'), padding=[14, 6])
        s.map('Accent.TButton',
              background=[('active', C['green']), ('pressed', C['mauve'])],
              foreground=[('active', bg), ('pressed', bg)])

        s.configure('TEntry',
                     fieldbackground=surf, foreground=fg,
                     insertcolor=fg, borderwidth=1)

        s.configure('TCombobox',
                     fieldbackground=surf, foreground=fg,
                     selectbackground=surf2, selectforeground=fg)
        s.map('TCombobox', fieldbackground=[('readonly', surf)])

        s.configure('TSpinbox', fieldbackground=surf, foreground=fg)

        s.configure('Treeview',
                     background=surf, foreground=fg,
                     fieldbackground=surf, rowheight=22)
        s.configure('Treeview.Heading',
                     background=surf2, foreground=acc,
                     font=('Segoe UI', 9, 'bold'))
        s.map('Treeview',
              background=[('selected', acc)],
              foreground=[('selected', bg)])

        s.configure('TProgressbar', background=acc, troughcolor=surf)
        s.configure('TScrollbar',   background=surf2, troughcolor=surf,
                     arrowcolor=fg)
        s.configure('TScale',       background=bg, troughcolor=surf2)

        s.configure('Status.TLabel',
                     background=surf, foreground=C['subtext'],
                     padding=[6, 3], font=('Segoe UI', 8))


# ---------------------------------------------------------------------------
# Onglet Convertir
# ---------------------------------------------------------------------------

class ConverterTab(ttk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent)
        self.app        = app
        self.converting = False

        # Variables
        self.var_input      = tk.StringVar()
        self.var_out_dir    = tk.StringVar()
        self.var_out_name   = tk.StringVar()
        self.var_quality    = tk.IntVar(value=4)
        self.var_x_mode     = tk.StringVar(value="auto")
        self.var_x_custom   = tk.IntVar(value=34)
        self.var_samplerate = tk.StringVar(value="44100")
        self.var_channels   = tk.StringVar(value="Stéréo (2)")

        self._build()

    def _build(self):
        pad = dict(padx=10, pady=4)

        # --- Source ---
        frm = ttk.LabelFrame(self, text="Fichier source", padding=8)
        frm.pack(fill=tk.X, **pad)
        ttk.Entry(frm, textvariable=self.var_input,
                  state='readonly').pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        ttk.Button(frm, text="Parcourir…",
                   command=self._browse_input).pack(side=tk.RIGHT)

        # --- Sortie ---
        frm2 = ttk.LabelFrame(self, text="Sortie", padding=8)
        frm2.pack(fill=tk.X, **pad)

        r1 = ttk.Frame(frm2); r1.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(r1, text="Dossier :", width=12, anchor=tk.W).pack(side=tk.LEFT)
        ttk.Entry(r1, textvariable=self.var_out_dir,
                  state='readonly').pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        ttk.Button(r1, text="Parcourir…",
                   command=self._browse_outdir).pack(side=tk.RIGHT)

        r2 = ttk.Frame(frm2); r2.pack(fill=tk.X)
        ttk.Label(r2, text="Nom de base :", width=12, anchor=tk.W).pack(side=tk.LEFT)
        ttk.Entry(r2, textvariable=self.var_out_name).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(r2, text=".mp3 / .mp3map",
                  foreground=C['overlay']).pack(side=tk.LEFT, padx=(6, 0))

        # --- Options ---
        frm3 = ttk.LabelFrame(self, text="Options d'encodage", padding=8)
        frm3.pack(fill=tk.X, **pad)

        # Qualité VBR
        rq = ttk.Frame(frm3); rq.pack(fill=tk.X, pady=2)
        ttk.Label(rq, text="Qualité VBR :", width=14, anchor=tk.W).pack(side=tk.LEFT)
        self._lbl_quality = ttk.Label(rq, text="4 — ~165 kbps",
                                       foreground=C['accent'], width=20)
        self._lbl_quality.pack(side=tk.RIGHT)
        ttk.Scale(rq, from_=0, to=9, variable=self.var_quality,
                  orient=tk.HORIZONTAL,
                  command=self._update_quality_lbl).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Facteur X
        rx = ttk.Frame(frm3); rx.pack(fill=tk.X, pady=2)
        ttk.Label(rx, text="Facteur X :", width=14, anchor=tk.W).pack(side=tk.LEFT)
        cb = ttk.Combobox(rx, textvariable=self.var_x_mode, width=14, state='readonly',
                          values=["auto", "34", "24", "22", "26", "20", "16", "personnalisé"])
        cb.pack(side=tk.LEFT, padx=(0, 8))
        cb.bind('<<ComboboxSelected>>', self._on_x_mode)
        self._spin_x = ttk.Spinbox(rx, from_=2, to=100, increment=2,
                                    textvariable=self.var_x_custom,
                                    width=6, state='disabled')
        self._spin_x.pack(side=tk.LEFT)
        ttk.Label(rx, text="(correction durée firmware)",
                  foreground=C['overlay']).pack(side=tk.LEFT, padx=(10, 0))

        # Fréquence / Canaux
        re = ttk.Frame(frm3); re.pack(fill=tk.X, pady=2)
        ttk.Label(re, text="Fréquence :", width=14, anchor=tk.W).pack(side=tk.LEFT)
        ttk.Combobox(re, textvariable=self.var_samplerate, width=7, state='readonly',
                     values=["44100", "48000", "32000"]).pack(side=tk.LEFT)
        ttk.Label(re, text="Hz", foreground=C['overlay']).pack(side=tk.LEFT, padx=(4, 20))
        ttk.Label(re, text="Canaux :", anchor=tk.W).pack(side=tk.LEFT)
        ttk.Combobox(re, textvariable=self.var_channels, width=12, state='readonly',
                     values=["Stéréo (2)", "Mono (1)"]).pack(side=tk.LEFT, padx=(4, 0))

        # --- Boutons ---
        bfrm = ttk.Frame(self); bfrm.pack(pady=6)
        self._btn_convert = ttk.Button(
            bfrm, text="⚡  Convertir & générer mp3map",
            style='Accent.TButton', command=self._start_full_conversion)
        self._btn_convert.pack(side=tk.LEFT, padx=4)

        self._btn_maponly = ttk.Button(
            bfrm, text="🗺  Générer mp3map uniquement",
            command=self._start_maponly)
        self._btn_maponly.pack(side=tk.LEFT, padx=4)

        if not self.app.ffmpeg_ok:
            self._btn_convert.config(state='disabled')
            ttk.Label(bfrm, text="(FFmpeg requis pour la conversion)",
                      foreground=C['red']).pack(side=tk.LEFT, padx=8)

        # Progress bar
        self._progress = ttk.Progressbar(self, mode='indeterminate')
        self._progress.pack(fill=tk.X, padx=10, pady=(0, 4))

        # Journal
        jfrm = ttk.LabelFrame(self, text="Journal", padding=4)
        jfrm.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))

        self._log = tk.Text(
            jfrm, height=10, wrap=tk.WORD,
            bg=C['surface'], fg=C['fg'],
            insertbackground=C['fg'],
            font=('Consolas', 9), state='disabled',
            relief='flat', borderwidth=0,
            selectbackground=C['surface2'])
        scr = ttk.Scrollbar(jfrm, command=self._log.yview)
        self._log.config(yscrollcommand=scr.set)
        scr.pack(side=tk.RIGHT, fill=tk.Y)
        self._log.pack(fill=tk.BOTH, expand=True)

        self._log.tag_config('ok',   foreground=C['green'])
        self._log.tag_config('err',  foreground=C['red'])
        self._log.tag_config('info', foreground=C['accent'])
        self._log.tag_config('dim',  foreground=C['overlay'])
        self._log.tag_config('head', foreground=C['mauve'], font=('Consolas', 9, 'bold'))

    # ---- helpers ----

    def _update_quality_lbl(self, val=None):
        q = self.var_quality.get()
        self._lbl_quality.config(text=f"{q} — {VBR_BITRATES.get(q, '?')}")

    def _on_x_mode(self, _=None):
        state = 'normal' if self.var_x_mode.get() == "personnalisé" else 'disabled'
        self._spin_x.config(state=state)

    def _browse_input(self):
        p = filedialog.askopenfilename(
            title="Sélectionner un fichier audio",
            filetypes=[
                ("Fichiers audio", "*.mp3 *.wav *.ogg *.flac *.m4a *.aac *.wma *.opus"),
                ("Tous les fichiers", "*.*"),
            ])
        if p:
            self.var_input.set(p)
            pp = Path(p)
            if not self.var_out_dir.get():
                self.var_out_dir.set(str(pp.parent))
            if not self.var_out_name.get():
                self.var_out_name.set(pp.stem)

    def _browse_outdir(self):
        p = filedialog.askdirectory(title="Dossier de sortie")
        if p:
            self.var_out_dir.set(p)

    def _get_x(self):
        m = self.var_x_mode.get()
        if m == "auto":
            return None
        elif m == "personnalisé":
            return self.var_x_custom.get()
        return int(m)

    def log(self, msg: str, tag: str = ''):
        self._log.config(state='normal')
        self._log.insert(tk.END, msg + '\n', tag)
        self._log.see(tk.END)
        self._log.config(state='disabled')

    def log_clear(self):
        self._log.config(state='normal')
        self._log.delete('1.0', tk.END)
        self._log.config(state='disabled')

    def _set_busy(self, busy: bool):
        self.converting = busy
        state = 'disabled' if busy else 'normal'
        self._btn_convert.config(state=state)
        self._btn_maponly.config(state=state)
        if busy:
            self._progress.start(12)
        else:
            self._progress.stop()

    # ---- conversion complète ----

    def _start_full_conversion(self):
        if self.converting:
            return
        inp  = self.var_input.get()
        odir = self.var_out_dir.get()
        name = self.var_out_name.get().strip()
        if not inp or not odir or not name:
            messagebox.showwarning("Champs manquants",
                                   "Veuillez remplir tous les champs (source, dossier, nom).")
            return

        self.log_clear()
        self._set_busy(True)
        threading.Thread(
            target=self._run_full_conversion,
            args=(inp, odir, name),
            daemon=True,
        ).start()

    def _run_full_conversion(self, inp, odir, name):
        out_mp3    = os.path.join(odir, name + '.mp3')
        out_mp3map = os.path.join(odir, name + '.mp3map')
        q  = self.var_quality.get()
        sr = int(self.var_samplerate.get())
        ch = 1 if "1" in self.var_channels.get() else 2
        xf = self._get_x()

        def ui(fn): self.after(0, fn)

        ui(lambda: self.log("══ Étape 1/2 — Conversion FFmpeg ══", 'head'))
        ui(lambda: self.log(f"  Entrée  : {inp}", 'dim'))
        ui(lambda: self.log(f"  Sortie  : {out_mp3}", 'dim'))
        ui(lambda: self.log(f"  Qualité : VBR q={q}, {sr} Hz, {ch} canal(aux)\n", 'dim'))

        def on_ffmpeg_line(line):
            if 'time=' in line:
                ui(lambda l=line: self.log(f"  {l}", 'dim'))

        ok, msg = convert_to_lunii_mp3(inp, out_mp3, q, sr, ch, on_ffmpeg_line)

        if not ok:
            ui(lambda: self.log("✗ Erreur FFmpeg :", 'err'))
            ui(lambda: self.log(msg, 'err'))
            ui(lambda: self._set_busy(False))
            return

        ui(lambda: self.log("✓ MP3 créé.\n", 'ok'))
        self._generate_map(out_mp3, out_mp3map, xf)

    # ---- génération mp3map seule ----

    def _start_maponly(self):
        if self.converting:
            return
        inp  = self.var_input.get()
        odir = self.var_out_dir.get()
        name = self.var_out_name.get().strip()

        if not inp:
            messagebox.showwarning("Champ manquant", "Veuillez sélectionner un fichier source.")
            return
        if not inp.lower().endswith('.mp3'):
            if not messagebox.askyesno("Format inattendu",
                                       "Le fichier source n'est pas un .mp3.\n"
                                       "La génération du mp3map sera incorrecte "
                                       "si ce n'est pas un MP3 valide.\nContinuer ?"):
                return
        if not odir or not name:
            messagebox.showwarning("Champs manquants",
                                   "Veuillez remplir le dossier et le nom de sortie.")
            return

        out_mp3map = os.path.join(odir, name + '.mp3map')
        self.log_clear()
        self._set_busy(True)
        threading.Thread(
            target=lambda: self._generate_map(inp, out_mp3map, self._get_x()),
            daemon=True,
        ).start()

    def _generate_map(self, mp3_path, mp3map_path, xf):
        def ui(fn): self.after(0, fn)

        ui(lambda: self.log("══ Étape 2/2 — Génération mp3map ══", 'head'))
        ui(lambda: self.log(f"  Source  : {mp3_path}", 'dim'))
        ui(lambda: self.log(f"  Sortie  : {mp3map_path}\n", 'dim'))

        try:
            content, info = generate_mp3map(mp3_path, xf)
            with open(mp3map_path, 'wb') as f:
                f.write(content)

            N   = info['frame_count']
            x   = info['x_factor']
            tu  = info['total_units']
            nr  = info['num_records']
            dur = info['duration_s']
            sz  = info['file_size']

            def show_summary():
                self.log("✓ mp3map généré.\n", 'ok')
                self.log("── Résumé ─────────────────────────────", 'info')
                self.log(f"  Trames MP3      : {N:,}", 'ok')
                self.log(f"  Facteur X       : {x}", 'ok')
                self.log(f"  total_units     : {tu:,}", 'ok')
                self.log(f"  Durée calculée  : {fmt_time(dur)}", 'ok')
                self.log(f"  Enregistrements : {nr}", 'ok')
                self.log(f"  Taille mp3map   : {fmt_size(sz)}", 'ok')
                self.log(f"\n  Fichiers prêts pour Lunii :", 'info')
                self.log(f"  • {mp3_path}", 'dim')
                self.log(f"  • {mp3map_path}", 'dim')
            ui(show_summary)

        except Exception as e:
            ui(lambda: self.log(f"✗ Erreur génération mp3map : {e}", 'err'))

        ui(lambda: self._set_busy(False))


# ---------------------------------------------------------------------------
# Onglet Inspecter
# ---------------------------------------------------------------------------

class ReaderTab(ttk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent)
        self.app       = app
        self.mp3_data  = None   # dict get_mp3_info
        self.map_data  = None   # dict parse_mp3map
        self.audio     = AudioPlayer()
        self._paused   = False
        self._seek_job = None

        self.var_mp3    = tk.StringVar()
        self.var_mp3map = tk.StringVar()
        self.var_seek   = tk.DoubleVar()
        self._user_seeking = False

        self._build()

    def _build(self):
        pad = dict(padx=10, pady=4)

        # --- Fichiers ---
        ff = ttk.LabelFrame(self, text="Fichiers", padding=8)
        ff.pack(fill=tk.X, **pad)

        for label, var, cmd in [
            ("MP3      :", self.var_mp3,    self._browse_mp3),
            ("MP3MAP :", self.var_mp3map,  self._browse_mp3map),
        ]:
            row = ttk.Frame(ff); row.pack(fill=tk.X, pady=2)
            ttk.Label(row, text=label, width=9, anchor=tk.W).pack(side=tk.LEFT)
            ttk.Entry(row, textvariable=var,
                      state='readonly').pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
            ttk.Button(row, text="Parcourir…", command=cmd).pack(side=tk.RIGHT)

        ttk.Button(ff, text="⚙  Analyser", style='Accent.TButton',
                   command=self._analyze).pack(pady=(6, 0))

        # --- Infos côte à côte ---
        info_row = ttk.Frame(self); info_row.pack(fill=tk.X, **pad)

        self._mp3_labels = self._build_info_panel(
            info_row, "Informations MP3",
            [('file_size',   'Taille fichier'),
             ('frame_count', 'Trames MP3'),
             ('duration_s',  'Durée estimée'),
             ('avg_bitrate', 'Bitrate moyen'),
             ('id3_offset',  'Tag ID3 (octets)')],
            side=tk.LEFT, padx=(0, 5))

        self._map_labels = self._build_info_panel(
            info_row, "Informations MP3MAP",
            [('total_units', 'total_units'),
             ('id3_offset',  'id3_offset'),
             ('duration_s',  'Durée calculée'),
             ('num_records', 'Enregistrements'),
             ('file_size',   'Taille fichier')],
            side=tk.LEFT, padx=(5, 0))

        # --- Table de seek ---
        tf = ttk.LabelFrame(self, text="Table de seek", padding=4)
        tf.pack(fill=tk.BOTH, expand=True, **pad)

        cols = ('#', 'time', 'frame', 'byte_hex', 'byte_dec', 'unit_pos')
        self._tree = ttk.Treeview(tf, columns=cols, show='headings', height=9)

        heads = {'#': ('#', 45, tk.CENTER),
                 'time':     ('Temps',        95,  tk.CENTER),
                 'frame':    ('Trame',         65, tk.CENTER),
                 'byte_hex': ('Offset (hex)', 115, tk.CENTER),
                 'byte_dec': ('Offset (déc)',  95, tk.CENTER),
                 'unit_pos': ('Unit pos',      95, tk.CENTER)}
        for col, (heading, w, anchor) in heads.items():
            self._tree.heading(col, text=heading)
            self._tree.column(col, width=w, anchor=anchor, stretch=False)

        # Alternance de couleurs de lignes
        self._tree.tag_configure('odd',  background=C['surface'])
        self._tree.tag_configure('even', background='#2a2a3e')
        self._tree.tag_configure('cur',  background=C['accent'], foreground=C['bg'])

        vs = ttk.Scrollbar(tf, orient=tk.VERTICAL, command=self._tree.yview)
        hs = ttk.Scrollbar(tf, orient=tk.HORIZONTAL, command=self._tree.xview)
        self._tree.config(yscrollcommand=vs.set, xscrollcommand=hs.set)
        vs.pack(side=tk.RIGHT, fill=tk.Y)
        hs.pack(side=tk.BOTTOM, fill=tk.X)
        self._tree.pack(fill=tk.BOTH, expand=True)

        self._tree.bind('<<TreeviewSelect>>', self._on_row_select)

        # Bouton export CSV
        ttk.Button(tf, text="📋 Exporter CSV…",
                   command=self._export_csv).pack(side=tk.RIGHT, pady=(4, 0))

        # --- Lecteur audio ---
        pf = ttk.LabelFrame(self, text="Lecteur audio", padding=8)
        pf.pack(fill=tk.X, padx=10, pady=(0, 8))

        if not PYGAME_OK:
            ttk.Label(pf,
                      text="pygame non installé — lecture désactivée.  "
                           "Installez-le avec :  pip install pygame",
                      foreground=C['red']).pack()
        else:
            ctrl = ttk.Frame(pf); ctrl.pack(fill=tk.X, pady=(0, 6))

            self._btn_play = ttk.Button(ctrl, text="▶  Lecture",
                                         command=self._toggle_play, width=12)
            self._btn_play.pack(side=tk.LEFT, padx=(0, 4))
            ttk.Button(ctrl, text="■  Stop",
                       command=self._stop, width=8).pack(side=tk.LEFT)

            self._lbl_time = ttk.Label(ctrl, text="00:00.000 / 00:00.000",
                                        foreground=C['accent'],
                                        font=('Consolas', 9))
            self._lbl_time.pack(side=tk.RIGHT, padx=8)

            self._seek_slider = ttk.Scale(pf, from_=0, to=100,
                                           variable=self.var_seek,
                                           orient=tk.HORIZONTAL)
            self._seek_slider.pack(fill=tk.X)
            self._seek_slider.bind('<ButtonPress-1>',   self._slider_press)
            self._seek_slider.bind('<ButtonRelease-1>', self._slider_release)

    def _build_info_panel(self, parent, title, fields, side, padx):
        frame = ttk.LabelFrame(parent, text=title, padding=8)
        frame.pack(side=side, fill=tk.BOTH, expand=True, padx=padx)
        labels = {}
        for key, label_text in fields:
            row = ttk.Frame(frame); row.pack(fill=tk.X, pady=1)
            ttk.Label(row, text=f"{label_text} :",
                      width=18, anchor=tk.W).pack(side=tk.LEFT)
            lbl = ttk.Label(row, text="—", foreground=C['accent'])
            lbl.pack(side=tk.LEFT)
            labels[key] = lbl
        return labels

    # ---- Parcourir ----

    def _browse_mp3(self):
        p = filedialog.askopenfilename(
            title="Sélectionner un fichier MP3",
            filetypes=[("MP3", "*.mp3"), ("Tous", "*.*")])
        if p:
            self.var_mp3.set(p)
            candidate = p + 'map'
            if os.path.exists(candidate):
                self.var_mp3map.set(candidate)

    def _browse_mp3map(self):
        p = filedialog.askopenfilename(
            title="Sélectionner un fichier mp3map",
            filetypes=[("MP3MAP", "*.mp3map"), ("Tous", "*.*")])
        if p:
            self.var_mp3map.set(p)
            candidate = p[:-3]  # retire 'map'
            if os.path.exists(candidate):
                self.var_mp3.set(candidate)

    # ---- Analyse ----

    def _reset_info(self):
        for lbl in self._mp3_labels.values():
            lbl.config(text="—")
        for lbl in self._map_labels.values():
            lbl.config(text="—")
        for item in self._tree.get_children():
            self._tree.delete(item)
        self.mp3_data = None
        self.map_data = None

    def _analyze(self):
        self._stop()
        self._reset_info()
        errors = []

        mp3_path = self.var_mp3.get()
        if mp3_path and os.path.exists(mp3_path):
            try:
                info = get_mp3_info(mp3_path)
                self.mp3_data = info
                self._mp3_labels['file_size'].config(  text=fmt_size(info['file_size']))
                self._mp3_labels['frame_count'].config( text=f"{info['frame_count']:,}")
                self._mp3_labels['duration_s'].config(  text=fmt_time(info['duration_s']))
                self._mp3_labels['avg_bitrate'].config(
                    text=f"{info['avg_bitrate'] // 1000} kbps")
                self._mp3_labels['id3_offset'].config(  text=str(info['id3_offset']))

                if PYGAME_OK:
                    self._stop()
                    loaded = self.audio.load(mp3_path, info['duration_s'])
                    if loaded and hasattr(self, '_seek_slider'):
                        dur = max(info['duration_s'], 1.0)
                        self._seek_slider.config(to=dur)
                        self._lbl_time.config(
                            text=f"00:00.000 / {fmt_time(info['duration_s'])}")
                        self._btn_play.config(state='normal')

            except Exception as e:
                errors.append(f"MP3 : {e}")
        elif mp3_path:
            errors.append(f"MP3 introuvable : {mp3_path}")

        map_path = self.var_mp3map.get()
        if map_path and os.path.exists(map_path):
            try:
                data = parse_mp3map(map_path)
                self.map_data = data
                self._map_labels['total_units'].config(text=f"{data['total_units']:,}")
                self._map_labels['id3_offset'].config( text=str(data['id3_offset']))
                self._map_labels['duration_s'].config( text=fmt_time(data['duration_s']))
                self._map_labels['num_records'].config(text=str(data['num_records']))
                self._map_labels['file_size'].config(  text=fmt_size(data['file_size']))

                for i, rec in enumerate(data['records']):
                    tag = 'even' if i % 2 == 0 else 'odd'
                    self._tree.insert('', tk.END, tags=(tag,), values=(
                        rec['index'],
                        fmt_time(rec['time_s']),
                        rec['frame_index'],
                        f"0x{rec['byte_offset']:08X}",
                        f"{rec['byte_offset']:,}",
                        f"{rec['unit_pos']:,}",
                    ))

            except Exception as e:
                errors.append(f"MP3MAP : {e}")
        elif map_path:
            errors.append(f"MP3MAP introuvable : {map_path}")

        if errors:
            messagebox.showerror("Erreur(s)", "\n".join(errors))

    # ---- Export CSV ----

    def _export_csv(self):
        if not self.map_data or not self.map_data['records']:
            messagebox.showinfo("Rien à exporter", "Analysez d'abord un fichier mp3map.")
            return
        p = filedialog.asksaveasfilename(
            title="Exporter la table de seek",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("Tous", "*.*")])
        if not p:
            return
        try:
            with open(p, 'w', encoding='utf-8') as f:
                f.write("index,time_s,frame_index,byte_offset,unit_pos\n")
                for rec in self.map_data['records']:
                    f.write(f"{rec['index']},{rec['time_s']:.6f},"
                            f"{rec['frame_index']},{rec['byte_offset']},{rec['unit_pos']}\n")
            messagebox.showinfo("Export réussi", f"Table exportée dans :\n{p}")
        except Exception as e:
            messagebox.showerror("Erreur", str(e))

    # ---- Lecture audio ----

    def _toggle_play(self):
        if not PYGAME_OK:
            return
        if self.audio.is_playing():
            self.audio.pause()
            self._paused = True
            self._btn_play.config(text="▶  Lecture")
            self._cancel_update()
        elif self._paused:
            self.audio.unpause()
            self._paused = False
            self._btn_play.config(text="⏸  Pause")
            self._schedule_update()
        else:
            if not self.audio.loaded_path:
                return
            self.audio.play(0.0)
            self._paused = False
            self._btn_play.config(text="⏸  Pause")
            self._schedule_update()

    def _stop(self):
        if not PYGAME_OK:
            return
        self._cancel_update()
        self.audio.stop()
        self._paused = False
        if hasattr(self, '_btn_play'):
            self._btn_play.config(text="▶  Lecture")
        if hasattr(self, 'var_seek'):
            self.var_seek.set(0)
        if hasattr(self, '_lbl_time') and self.mp3_data:
            self._lbl_time.config(
                text=f"00:00.000 / {fmt_time(self.mp3_data['duration_s'])}")

    def _schedule_update(self):
        self._seek_job = self.after(200, self._update_player)

    def _cancel_update(self):
        if self._seek_job:
            self.after_cancel(self._seek_job)
            self._seek_job = None

    def _update_player(self):
        if not self.audio.is_playing() and not self._paused:
            self._btn_play.config(text="▶  Lecture")
            self.var_seek.set(0)
            self._seek_job = None
            return

        if not self._user_seeking:
            pos = self.audio.get_pos()
            dur = self.audio.duration
            self.var_seek.set(pos)
            self._lbl_time.config(text=f"{fmt_time(pos)} / {fmt_time(dur)}")
            self._highlight_row(pos)

        self._schedule_update()

    def _highlight_row(self, pos_s: float):
        """Met en surbrillance la ligne de la table correspondant à la position actuelle."""
        children = self._tree.get_children()
        best = None
        for child in children:
            vals = self._tree.item(child)['values']
            try:
                t_str = str(vals[1])
                parts = t_str.split(':')
                t = int(parts[0]) * 60 + float(parts[1])
                if t <= pos_s:
                    best = child
                else:
                    break
            except Exception:
                pass

        if best:
            # Rétablir les tags originaux pour toutes les lignes
            for i, child in enumerate(children):
                tag = 'even' if i % 2 == 0 else 'odd'
                self._tree.item(child, tags=(tag,))
            # Marquer la ligne courante
            self._tree.item(best, tags=('cur',))
            self._tree.see(best)

    def _on_row_select(self, _=None):
        """Seek audio vers la position de la ligne cliquée."""
        sel = self._tree.selection()
        if not sel or not PYGAME_OK or not self.audio.loaded_path:
            return
        vals = self._tree.item(sel[0])['values']
        try:
            t_str = str(vals[1])
            parts = t_str.split(':')
            t = int(parts[0]) * 60 + float(parts[1])
            if self.audio.is_playing() or self._paused:
                self.audio.seek(t)
                self._paused = False
                self._btn_play.config(text="⏸  Pause")
                self._cancel_update()
                self._schedule_update()
        except Exception:
            pass

    def _slider_press(self, _=None):
        self._user_seeking = True

    def _slider_release(self, _=None):
        self._user_seeking = False
        if not PYGAME_OK or not self.audio.loaded_path:
            return
        pos = self.var_seek.get()
        was_active = self.audio.is_playing() or self._paused
        self.audio.seek(pos)
        self._paused = False
        if was_active:
            self._btn_play.config(text="⏸  Pause")
            self._cancel_update()
            self._schedule_update()


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------

def main():
    app = App()
    app.mainloop()


if __name__ == '__main__':
    main()
