import io, sys, re, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
for fn in ("AK/visualizer_py.py", "AK/skills.py", "AK/local_features.py"):
    src = open(fn, encoding="utf-8").read().splitlines()
    print("=====", fn)
    for i, l in enumerate(src, 1):
        if re.search(r'coin|dice|sikka|pasa|paisa|heads|tails|\bflip\b|\broll\b|random_tool|RandomTools', l, re.I):
            print(i, l.strip()[:92])
