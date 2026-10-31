import io, sys, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
for fn in ("main.py",):
    src = open(fn, encoding="utf-8").read().splitlines()
    print("====", fn)
    for i, l in enumerate(src, 1):
        if 1183 <= i <= 1700:
            s = l.strip()
            if re.search(
                r"flip|toss|roll|random|chance|1.?6|six|heads|tails|sikka|pasa|dice|coin",
                s,
                re.I,
            ):
                print(i, s[:92])
