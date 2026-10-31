import io, sys, re, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
for fn in ("main.py", "main_build.py"):
    src = open(fn, encoding="utf-8").read().splitlines()
    print("====", fn)
    for i, l in enumerate(src, 1):
        if re.search(
            r"skills\.flip_coin|skills\.roll_dice|_viz_custom\(|_viz_state\(\s*[\"']custom|random_tool|RandomTools|random\.choice|randint\(\s*1,\s*6|flip_coin|roll_dice",
            l,
        ):
            z = l.strip()
            print(i, z[:98])
