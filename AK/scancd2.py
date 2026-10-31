import io, sys, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
for fn in ("main.py", "main_build.py"):
    src = open(fn, encoding="utf-8").read().splitlines()
    print("====", fn)
    for i, l in enumerate(src, 1):
        if re.search(r"random|coin|dice|heads|tails|flip|toss|roll a", l, re.I) and 1183 <= i <= 1450:
            print(i, l.strip()[:90])
