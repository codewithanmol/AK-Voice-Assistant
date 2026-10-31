"""Test neutral phrase with Google+Whisper"""
from AK import voice
import os

ROOT = r'C:\Users\YOUR_USERNAME\path\to\project'
v = {'stt_engine':'google','language':'en-IN','input_device_index':1,
     'use_local_stt':True,'interrupt':True}
L = voice.Listener(v, lambda p: os.path.join(ROOT,p) if p else p)
txt = L.listen("BOLO: kya haal hai")
print('RES', txt)
