"""Instant 'number variants' of predict-the-output questions, no AI needed.
The numbers in the C program are changed a little, the server runs the new program to get the true output, and the
options are rebuilt around it (the old correct answer becomes a tempting wrong option). Same question, same level,
different answer: neighbours cannot copy, and no new question has to be written."""
import json
import random
import re

from . import db

FENCE = re.compile(r"```c\n(.*?)```", re.S)
NUM = re.compile(r"(?<![\w.\[])(\d+)(?![\w.\]])")


def tweak(code, rng):
    """Change some integer literals (2..999) outside strings, #include lines, array sizes and format text."""
    out, changed = [], 0
    for line in code.split("\n"):
        if line.lstrip().startswith("#"):
            out.append(line)
            continue
        quoted = [m.span() for m in re.finditer(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', line)]

        def rep(m):
            nonlocal changed
            v = int(m.group(1))
            if v < 2 or v > 999 or any(a <= m.start() < b for a, b in quoted) or rng.random() < 0.35:
                return m.group(1)
            new = v
            while new == v or new < 2:
                new = v + rng.choice((-3, -2, -1, 1, 2, 3)) * max(1, v // 10)
            changed += 1
            return str(new)
        out.append(NUM.sub(rep, line))
    return "\n".join(out), changed


def distractors(correct, old_correct, rng):
    """Plausible wrong options: the old answer, numbers off by one, reversed order, one item missing."""
    cands = [old_correct]
    for m in re.finditer(r"-?\d+", correct):   # one number changed at a time
        for d in (1, -1, 2):
            cands.append(correct[:m.start()] + str(int(m.group(0)) + d) + correct[m.end():])
    toks = correct.split()
    if len(toks) > 1:
        cands += [" ".join(reversed(toks)), " ".join(toks[:-1])]
    seen, out = {correct.strip()}, []
    for x in cands:
        x = x.strip()
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    trap, rest = out[:1], out[1:]   # the old answer always stays: it catches students who copied it from a neighbour
    rng.shuffle(rest)
    return trap + rest[:3 - len(trap)]


def make(c, qid, uid, run, rng=None):
    """One proved variant of question qid, or None. `run(code) -> output or None` runs C in the sandbox."""
    from .api_admin import QuestionIn, create_question
    rng = rng or random.Random()
    q = db.one(c, "SELECT q.category, q.difficulty, q.tags, qv.* FROM questions q JOIN question_versions qv ON qv.id=q.latest_version_id WHERE q.id=?", qid)
    m = q and q["qtype"] in ("mcq", "output", "trace") and FENCE.search(q["statement"] or "")
    if not m:
        return None
    old = (db.one(c, "SELECT code FROM reference_solutions WHERE version_id=? AND language='answer'", q["id"]) or {}).get("code", "").strip()
    for _ in range(4):
        code, n = tweak(m.group(1), rng)
        if not n:
            continue
        out = run(code)
        if not out or not out.strip() or out.strip() == old or len(out) > 300:
            continue
        right = out.strip()
        wrong = distractors(right, old, rng)
        if len(wrong) < 3:
            continue
        opts = [right] + wrong
        rng.shuffle(opts)
        st = q["statement"][:m.start()] + f"```c\n{code.rstrip()}\n```" + q["statement"][m.end():]
        with db.tx(c):
            nid = create_question(c, QuestionIn(title=f"{q['title']} (variant)"[:150], category=q["category"], difficulty=q["difficulty"],
                                                tags=(db.jl(q["tags"]) + ["variant"])[:12], statement=st, qtype="mcq", options=opts, answer=right), {"id": uid}, "ai")
            c.execute("UPDATE questions SET status='approved' WHERE id=?", (nid,))
        return nid
    return None


if __name__ == "__main__":
    r = random.Random(3)
    code = '#include <stdio.h>\nint main(void){\n    int a[5] = {1, 2, 3, 4, 5};\n    int s = 0;\n    for (int i = 0; i < 5; i++) s += a[i] * 3;\n    printf("%d\\n", s);\n    return 0;\n}'
    new, n = tweak(code, r)
    assert n and "#include <stdio.h>" in new and "a[5]" in new and '"%d\\n"' in new, new
    d = distractors("45", "48", r)
    assert "48" in d and "45" not in d and len(d) == 3, d
    d2 = distractors("1 2 3", "1 2 4", r)
    assert len(d2) == 3 and "1 2 3" not in d2, d2
    print("variants self-check ok")
