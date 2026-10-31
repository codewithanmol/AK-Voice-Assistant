"""Test Google+Whisper fallback with local STT enabled"""
from AK import voice
import os

ROOT = r'C:\Users\YOUR_USERNAME\path\to\project'
v = {'stt_engine':'google','language':'en-IN','input_device_index':1,
     'use_local_stt':True,'interrupt':True,
     'whisper_model':'base','whisper_beam_size':5,'whisper_vad':True}
L = voice.Listener(v, lambda p: os.path.join(ROOT,p) if p else p)
print('ready', L._local_stt is not None, L.active)
txt = L.listen("BOLO: play tum hi ho")
print('RES', txt)
