"""Plain-language feedback on a failed submission, worked out by rules (no AI needed, same answer every time).
Looks at the expected vs printed output of a visible example, the verdict, the compiler message and the code itself."""
import re


def _nums(s):
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", s)]


def output_diff(expected, got):
    """Why the printed output differs from the expected output, most specific reason first."""
    e, g = (expected or "").strip(), (got or "").strip()
    if not g:
        return "Your program printed nothing. Check that it prints the answer, and that it reads the input it expects (it may be waiting for input that is not there)."
    if e == g:
        return "The output only differs in spaces or blank lines. Print exactly the format asked: no extra spaces at line ends, no extra blank lines."
    if e.lower() == g.lower():
        return "Only upper/lower case differs. Match the exact spelling, for example `Yes` vs `YES`."
    if " ".join(e.split()) == " ".join(g.split()):
        return "The values are right but the layout is not: check where you print spaces and new lines (one value per line vs all on one line)."
    if e in g and len(g) > len(e):
        extra = g.replace(e, "").strip()[:60]
        return f"Your output contains the right answer plus extra text ({extra!r}). Remove prompts like \"Enter a number\" and any debug prints."
    en, gn = _nums(e), _nums(g)
    if en and gn and len(en) == len(gn):
        if sorted(en) == sorted(gn):
            return "You printed the right values in the wrong order."
        diff = [b - a for a, b in zip(en, gn)]
        if all(abs(d) == 1 for d in diff if d) and any(diff):
            return "Some numbers are off by exactly one. Check loop bounds (< vs <=) and whether counting starts at 0 or 1."
        if any(a != b and a == int(a) and abs(b - a) < 1 for a, b in zip(en, gn)):
            return "A whole number answer came out with decimals (or the other way round). Check integer vs float division and the print format."
        if any(a > 2**31 - 1 or a < -2**31 for a in en) and any(b != a for a, b in zip(en, gn)):
            return "The answer is larger than an int can hold. Use long long (C/C++) or long (Java)."
        if any(a and b == -a for a, b in zip(en, gn)):
            return "A sign is wrong (positive vs negative). Check subtraction order and how negative input is handled."
    if en and gn and len(gn) < len(en):
        return f"You printed fewer values ({len(gn)}) than expected ({len(en)}). A loop may stop early, or some cases are not printed."
    if en and gn and len(gn) > len(en):
        return f"You printed more values ({len(gn)}) than expected ({len(en)}). A loop may run too long, or there are extra prints."
    return "The output is different from the expected output. Work through the example by hand and compare line by line."


def code_hints(code, lang, verdict):
    """Common mistakes visible in the code itself."""
    code = code or ""
    h = []
    if re.search(r"(?i)printf\s*\(\s*\"[^\"]*(enter|input|give)\b|input\s*\(\s*['\"][^'\"]*(enter|input)", code):
        h.append("Your program prints an input prompt (like \"Enter a number\"). The judge compares everything printed, so remove it.")
    if lang in ("c", "cpp") and verdict in ("RE", "WA") and re.search(r"\[\s*\w+\s*\]", code) and re.search(r"<=\s*n\b", code):
        h.append("A loop goes up to `<= n` while indexing an array: if the array has n items, the last index is n-1.")
    if lang in ("c", "cpp") and re.search(r"scanf\s*\(\s*\"[^\"]*%d[^\"]*\"\s*,\s*[a-zA-Z_]\w*\s*[,)]", code):
        h.append("scanf needs the address of the variable: write `scanf(\"%d\", &x)`, not `scanf(\"%d\", x)`.")
    if verdict == "TLE" and len(re.findall(r"\bfor\b|\bwhile\b", code)) >= 2:
        h.append("There are nested loops. For large inputs that can be too slow; look for a way to do it in one pass (sorting, a running sum, or a hash map).")
    if verdict == "RE" and re.search(r"/\s*[a-zA-Z_]\w*", code) and not re.search(r"!=\s*0|== *0", code):
        h.append("There is a division by a variable with no check for zero.")
    if verdict == "RE" and lang == "python" and re.search(r"input\(\)\s*\)?\s*$", code, re.M) and "split" not in code:
        h.append("If several values are on one line, read them with `input().split()`; `int(input())` fails on a line like `3 4`.")
    if lang in ("c", "cpp") and verdict == "WA" and re.search(r"\bint\b[^;]*\b(sum|total|prod|fact|result)\b", code) and not re.search(r"long\s+long", code):
        h.append("Totals and products can get large: consider `long long` instead of `int`.")
    return h[:3]


def explain(verdict, expected=None, got=None, code="", lang="", compile_output=""):
    """-> list of short, specific reasons (best first)."""
    out = []
    if verdict == "CE" and compile_output:
        first = next((l for l in compile_output.splitlines() if "error" in l.lower()), compile_output.splitlines()[0] if compile_output else "")
        m = re.search(r":(\d+):\d*:?\s*error:\s*(.*)", first)
        out.append(f"Compiler error on line {m.group(1)}: {m.group(2)}" if m else f"Compiler says: {first[:200]}")
        if re.search(r"expected ';'", compile_output):
            out.append("A semicolon is missing at the end of a statement, usually on the line just above the one reported.")
        if re.search(r"undeclared|not declared|was not declared", compile_output):
            out.append("A variable is used before it is declared, or its name is misspelled.")
    if expected is not None and got is not None and verdict in ("WA", "PE"):
        out.append(output_diff(expected, got))
    out += code_hints(code, lang, verdict)
    return out


LOOP_VARS = set("ijknmxyabcst") | {"_"}


def quality(code, lang):
    """Rule-based code quality, 0-100, with the reasons. Same rules for everyone; it never changes marks, only breaks exact ties."""
    code = (code or "").replace("\r\n", "\n")
    lines = [l for l in code.split("\n")]
    body = [l for l in lines if l.strip()]
    score, notes = 100, []

    def cut(n, why):
        nonlocal score
        score -= n
        notes.append(why)
    if not body:
        return {"score": 0, "notes": ["No code."]}
    if len(code) > 150 and len(body) <= 3:
        cut(25, "Everything is squeezed onto a few lines: write one statement per line.")
    long_lines = sum(1 for l in body if len(l) > 120)
    if long_lines:
        cut(min(15, 5 * long_lines), f"{long_lines} very long line(s) (over 120 characters): break them up.")
    if any(l.startswith("\t") for l in body) and any(l.startswith("  ") for l in body):
        cut(10, "Mixed tabs and spaces for indentation: use one style.")
    depth = max(((len(l) - len(l.lstrip(" \t"))) // 4 for l in body), default=0)
    if lang in ("c", "cpp", "java", "javascript", "go", "rust"):
        braces, deep = 0, 0
        for ch in re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', "", code):
            braces += (ch == "{") - (ch == "}")
            deep = max(deep, braces)
        depth = max(depth, deep - 1)
    if depth > 4:
        cut(10, f"Code is nested {depth} levels deep: return early or move parts into a function.")
    names = set(re.findall(r"\b(?:int|long|float|double|char|bool|auto|var|let|const|String)\s+\*?\s*([A-Za-z_]\w*)", code))
    if lang == "python":
        names |= set(re.findall(r"^\s*([A-Za-z_]\w*)\s*=(?!=)", code, re.M))
    vague = sorted(n for n in names if len(n) == 1 and n not in LOOP_VARS)
    if len(vague) >= 3:
        cut(8, f"Several one-letter names ({', '.join(vague[:4])}): use names that say what they hold.")
    if re.search(r"\bgoto\b", code):
        cut(10, "Uses goto: use loops and if/else instead.")
    if lang in ("c", "cpp"):
        top = re.sub(r"\{[^{}]*\}", "", re.sub(r"\{[^{}]*\}", "", re.sub(r"\{[^{}]*\}", "", code)))
        if re.search(r"^\s*(?:int|long|float|double|char)\s+\w+(?:\s*\[[^\]]*\])?\s*(?:=[^;]*)?;", top, re.M):
            cut(5, "Global variables: keep variables inside the function that uses them.")
        if re.search(r"\bgets\s*\(", code):
            cut(10, "gets() is unsafe: use fgets() or scanf with a width.")
    funcs = re.split(r"\n(?=[A-Za-z_][\w\s\*]*\([^;]*\)\s*\{)|\n(?=def )", code)
    if max((f.count("\n") for f in funcs), default=0) > 60:
        cut(10, "A function is over 60 lines long: split it into smaller functions.")
    if len(body) > 25 and not re.search(r"//|/\*|#(?!include|define)", code):
        cut(5, "No comments in a longer program: a short comment on the tricky part helps readers.")
    dead = sum(1 for l in body if re.match(r"\s*(?://|#)\s*\w+.*[;:]\s*$", l))
    if dead >= 3:
        cut(5, f"{dead} lines of commented-out code: delete code you no longer use.")
    return {"score": max(0, min(100, score)), "notes": notes or ["Clean and readable."]}


if __name__ == "__main__":
    good = "#include <stdio.h>\nint main(void) {\n    int count;\n    scanf(\"%d\", &count);\n    printf(\"%d\\n\", count * 2);\n    return 0;\n}\n"
    bad = "#include <stdio.h>\nint g;int main(){int p,q,r,w;scanf(\"%d %d\",&p,&q);r=p+q;w=r*2;if(p>0){if(q>0){if(r>0){if(w>0){if(p>q){printf(\"%d\",w);}}}}}goto e;e:return 0;}"
    qg, qb = quality(good, "c"), quality(bad, "c")
    assert qg["score"] == 100 and qb["score"] < 60, (qg, qb)
    assert any("goto" in n for n in qb["notes"]) and any("one statement per line" in n for n in qb["notes"]), qb
    assert quality("n = int(input())\nprint(n * 2)\n", "python")["score"] == 100
    assert "nothing" in output_diff("5\n", "")
    assert "spaces" in output_diff("1 2\n", "1 2 \n\n") or "layout" in output_diff("1 2\n", "1  2")
    assert "case" in output_diff("Yes", "YES")
    assert "extra text" in output_diff("7", "Enter two numbers: 7")
    assert "order" in output_diff("1 2 3", "3 2 1")
    assert "off by exactly one" in output_diff("10", "11")
    assert "fewer" in output_diff("1 2 3", "1 2")
    assert "layout" in output_diff("1\n2", "1 2")
    assert any("&x" in h for h in code_hints('int x; scanf("%d", x);', "c", "RE"))
    assert any("prompt" in h for h in code_hints('printf("Enter n: ");', "c", "WA"))
    assert explain("CE", compile_output="main.c:4:5: error: expected ';' before 'return'")[0].startswith("Compiler error on line 4")
    print("feedback self-check ok")
