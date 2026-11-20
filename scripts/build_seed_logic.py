"""Builds app/seed_logic.json: the logical-thinking module (aptitude and reasoning multiple choice, as in the first
section of campus placement tests). Every answer is computed here and asserted, never typed by hand.
    python scripts/build_seed_logic.py"""
import datetime
import hashlib
import itertools
import json
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def mcq(title, question, correct, distractors, *, diff="Easy", tags=()):
    correct, distractors = str(correct).strip(), [str(d).strip() for d in distractors]
    assert len(distractors) == 3 and len({correct, *distractors}) == 4, f"'{title}': distractors must differ from {correct!r}"
    options = sorted([correct, *distractors], key=lambda o: hashlib.md5(f"{title}|{o}".encode()).hexdigest())
    return {"title": title, "statement": question, "input_format": "", "output_format": "", "difficulty": diff, "tags": list(tags), "qtype": "mcq",
            "languages": ["answer"], "references": {"answer": correct}, "starter": {}, "options": options,
            "tests": [{"input": "", "expected": correct, "visibility": "hidden"}]}


def shift(word, k):
    return "".join(chr((ord(ch) - 65 + k) % 26 + 65) for ch in word)


def clock_angle(h, m):
    a = abs(30 * (h % 12) + Fraction(m, 2) - 6 * m)
    return min(a, 360 - a)


# seating: five friends in a row; find who is in the middle
def middle_seat():
    names = "ABCDE"
    fits = [p for p in itertools.permutations(names)
            if p[0] == "C" and abs(p.index("A") - p.index("B")) == 1 and p.index("D") == 4 and p.index("B") < p.index("A") and abs(p.index("E") - p.index("D")) == 1]
    assert len(fits) == 1, fits
    return fits[0][2]


# directions: walk and report the straight-line distance from the start
def walk(steps):
    x = y = 0
    for d, n in steps:
        dx, dy = {"N": (0, 1), "S": (0, -1), "E": (1, 0), "W": (-1, 0)}[d]
        x, y = x + dx * n, y + dy * n
    dist = (x * x + y * y) ** 0.5
    assert dist == int(dist)
    return int(dist)


series = [3, 6, 11, 18, 27]                       # +3, +5, +7, +9 -> +11
assert [b - a for a, b in zip(series, series[1:])] == [3, 5, 7, 9]
together = 1 / (Fraction(1, 12) + Fraction(1, 6))  # A in 12 days, B in 6 days
assert together == 4
speed = Fraction(150 + 250, 20) * Fraction(18, 5)  # 150 m train crosses a 250 m bridge in 20 s -> km/h
assert speed == 72
day = (datetime.date(2026, 1, 26)).strftime("%A")  # Republic Day 2026
after_two = round(2000 * 1.10 * 1.10)              # 10% growth twice
ages = next((a, b) for a in range(1, 80) for b in range(1, 80) if a == 3 * b and a + 10 == 2 * (b + 10))
handshakes = len(list(itertools.combinations(range(8), 2)))
liars = [n for n in range(1, 4) if [n == 1, n != 1, n != 3].count(True) == 1]   # exactly one of three statements is true
assert liars == [3], liars
ratio_boys = 40 * 3 // (3 + 5)
odd = [x for x in (121, 169, 225, 289, 200) if int(x ** 0.5) ** 2 != x]
assert odd == [200]

LOGIC = [
    mcq("Logic: Next in the Series", f"What comes next?\n\n`{', '.join(map(str, series))}, ?`", series[-1] + 11, [36, 37, 40], tags=["series"]),
    mcq("Logic: Letter Code", f"In a certain code, `CODE` is written as `{shift('CODE', 2)}`. How is `LOOP` written in that code?", shift("LOOP", 2),
        [shift("LOOP", 1), shift("LOOP", 3), "NQPR"], tags=["coding-decoding"]),
    mcq("Logic: Odd One Out", "Which number does not belong with the others?\n\n`121, 169, 200, 225, 289`", odd[0], [121, 169, 289], tags=["classification"]),
    mcq("Logic: Work Together", "A can finish a job in 12 days and B can finish it in 6 days. Working together, how many days do they need?", f"{together} days",
        ["9 days", "3 days", "18 days"], tags=["time-and-work"]),
    mcq("Logic: Ratio in a Class", "A class of 40 students has boys and girls in the ratio 3 : 5. How many boys are there?", ratio_boys, [24, 12, 25], tags=["ratio"]),
    mcq("Logic: Growth Twice", "A club has 2000 members. It grows by 10% this year and by 10% again next year. How many members will it have?", after_two,
        [2400, 2200, 2440], tags=["percentage"]),
    mcq("Logic: Handshakes", "8 people meet and each shakes hands once with every other person. How many handshakes take place?", handshakes, [56, 16, 64],
        tags=["counting"]),
    mcq("Logic: Which Day", "India's Republic Day is 26 January. On which day of the week does it fall in 2026?", day,
        [d for d in ("Sunday", "Tuesday", "Saturday", "Monday") if d != day][:3], diff="Intermediate", tags=["calendar"]),
    mcq("Logic: Clock Angle", "What is the angle between the hour hand and the minute hand of a clock at 3:30?", f"{clock_angle(3, 30)} degrees",
        ["90 degrees", "60 degrees", "105 degrees"], diff="Intermediate", tags=["clock"]),
    mcq("Logic: Walking Directions", "Ravi walks 6 m north, then 8 m east, then 12 m south, then 16 m west. How far is he from where he started, in a straight line?",
        f"{walk([('N', 6), ('E', 8), ('S', 12), ('W', 16)])} m", ["14 m", "42 m", "8 m"], diff="Intermediate", tags=["directions"]),
    mcq("Logic: Father and Son", "A father is three times as old as his son. In 10 years he will be twice as old as his son. How old is the father now?",
        f"{ages[0]} years", ["40 years", "36 years", "45 years"], diff="Intermediate", tags=["ages"]),
    mcq("Logic: Train and Bridge", "A train 150 m long crosses a bridge 250 m long in 20 seconds. What is the speed of the train?", f"{speed} km/h",
        ["45 km/h", "27 km/h", "20 km/h"], diff="Intermediate", tags=["speed-distance"]),
    mcq("Logic: Five in a Row", "Five friends A, B, C, D and E sit in a row facing north. C sits at the left end and D at the right end. "
        "A and B sit next to each other, and B is to the left of A. E sits next to D. Who sits in the middle?", middle_seat(), [x for x in "ABE" if x != middle_seat()] + ["C"],
        diff="Intermediate", tags=["seating"]),
    mcq("Logic: One True Statement", "A box holds 1, 2 or 3 coins. Three notes are stuck on it:\n\n- Note 1: \"There is exactly 1 coin.\"\n- Note 2: \"There is more than 1 coin.\"\n"
        "- Note 3: \"There are fewer than 3 coins.\"\n\nExactly one note is true. How many coins are in the box?", liars[0], [1, 2, "It cannot be decided"],
        diff="Challenging", tags=["deduction"]),
]

if __name__ == "__main__":
    assert middle_seat() == "A" and len(LOGIC) == 14 and len({q["title"] for q in LOGIC}) == 14
    spots = [q["options"].index(q["references"]["answer"]) for q in LOGIC]
    assert len(set(spots)) == 4, spots
    out = ROOT / "app" / "seed_logic.json"
    out.write_text(json.dumps({"key": "logic_v1", "modules": [{"category": "Placement: Logical Thinking", "questions": LOGIC}]}, indent=1))
    for q in LOGIC:
        print(f"  {q['title']} -> {q['references']['answer']!r}  options={q['options']}")
    print(f"wrote {out} ({len(LOGIC)} questions); correct positions: " + "".join("ABCD"[s] for s in spots))
