import io, sys, re, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
src = open("main.py", encoding="utf-8").read().splitlines()
# find generic skill dispatch / "skills." callers and helper defs list
for i, l in enumerate(src, 1):
    s = l.strip()
    if re.search(r"def _handle_skill|def _try_local|skills\.[a-z_]+\(|canonical|_canon|_skill_map|_SKILLS\b|method='|methods=|getattr\(.*skills", s, re.I) and i < 2220:
        print(i, s[:100])
