import io, sys, re, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
for fn in ("AK/skills.py", "AK/local_features.py"):
    src = open(fn, encoding="utf-8").read().splitlines()
    print("====", fn)
    for i, l in enumerate(src, 1):
        if re.search(
            r"flip a coin|flip the coin|coin flip|roll a dice|roll the dice|roll a die|dice roll|toss|chance|random.side|randint|flip_coin|roll_dice|sikka|pasa|1..6|1.?6\b",
            l,
            re.I,
        ):
            print(i, l.strip()[:95])
