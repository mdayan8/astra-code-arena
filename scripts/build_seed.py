"""Builds app/seed_c.json: the two C practice modules that ship with the platform.
Every expected output is produced by compiling and running the reference C code here (gcc), never typed by hand.
    python scripts/build_seed.py"""
import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
H = "#include <stdio.h>\n"


def run_c(code, stdin=""):
    with tempfile.TemporaryDirectory() as d:
        src, exe = Path(d) / "m.c", Path(d) / "m"
        src.write_text(code)
        subprocess.run(["gcc", "-O2", "-std=c11", "-w", "-o", str(exe), str(src), "-lm"], check=True)
        return subprocess.run([str(exe)], input=stdin, capture_output=True, text=True, timeout=5, check=True).stdout


def code(title, statement, ref, inputs, *, diff="Easy", tags=(), fmt_in="", fmt_out="", qtype="standard", starter=""):
    """A programming question: inputs[:2] become sample tests, the rest hidden."""
    if starter and qtype == "debug":
        assert any(run_c(starter, i) != run_c(ref, i) for i in inputs), f"starter of '{title}' is not buggy"
    tests = [{"input": i, "expected": run_c(ref, i), "visibility": "sample" if n < 2 else "hidden"} for n, i in enumerate(inputs)]
    return {"title": title, "statement": statement, "input_format": fmt_in, "output_format": fmt_out, "difficulty": diff, "tags": list(tags),
            "qtype": qtype, "languages": ["c"], "references": {"c": ref}, "starter": {"c": starter} if starter else {}, "tests": tests}


def written(title, question, snippet, *, qtype="output", diff="Easy", tags=()):
    """Predict-the-output / trace question: the answer is whatever the snippet really prints."""
    ans = run_c(snippet)
    assert ans.strip(), title
    st = f"{question}\n\n```c\n{snippet.strip()}\n```\n\nType your answer exactly as the program prints it."
    return {"title": title, "statement": st, "input_format": "", "output_format": "", "difficulty": diff, "tags": list(tags), "qtype": qtype,
            "languages": ["answer"], "references": {"answer": ans}, "starter": {}, "tests": [{"input": "", "expected": ans, "visibility": "hidden"}]}


BASICS = [
    code("Sum of Two Numbers", "Read two integers and print their sum.",
         H + "int main(void){long a,b;if(scanf(\"%ld %ld\",&a,&b)!=2)return 0;printf(\"%ld\\n\",a+b);return 0;}\n",
         ["3 4\n", "10 -3\n", "0 0\n", "-7 -8\n", "100000 250000\n", "999999 1\n"],
         tags=["input-output", "arithmetic"], fmt_in="Two integers a and b on one line.", fmt_out="One integer: a + b."),
    code("Even or Odd", "Read an integer and print `Even` if it is even, otherwise print `Odd`.",
         H + "int main(void){long n;if(scanf(\"%ld\",&n)!=1)return 0;puts(n%2==0?\"Even\":\"Odd\");return 0;}\n",
         ["8\n", "13\n", "0\n", "-5\n", "-12\n", "1000001\n"],
         tags=["if-else", "modulo"], fmt_in="One integer n.", fmt_out="`Even` or `Odd`."),
    code("Largest of Three", "Read three integers and print the largest one.",
         H + "int main(void){long a,b,c;if(scanf(\"%ld %ld %ld\",&a,&b,&c)!=3)return 0;long m=a;if(b>m)m=b;if(c>m)m=c;printf(\"%ld\\n\",m);return 0;}\n",
         ["4 9 2\n", "7 7 3\n", "-1 -5 -3\n", "10 2 10\n", "0 0 0\n", "5 6 7\n"],
         tags=["if-else", "comparison"], fmt_in="Three integers on one line.", fmt_out="The largest of the three."),
    written("Predict the Output: Integer Division", "What does this program print?",
            H + "int main(void){\n    int a = 17, b = 5;\n    printf(\"%d %d\\n\", a / b, a % b);\n    return 0;\n}\n", tags=["operators", "division"]),
    written("Predict the Output: Increment Operators", "What does this program print?",
            H + "int main(void){\n    int x = 5;\n    int y = x++;\n    int z = ++x;\n    printf(\"%d %d %d\\n\", x, y, z);\n    return 0;\n}\n", tags=["operators", "increment"]),
    code("Complete the Code: Factorial", "The program must read `n` (0 to 12) and print `n!`. The loop body is missing. Complete it.",
         H + "int main(void){int n;if(scanf(\"%d\",&n)!=1)return 0;long f=1;for(int i=2;i<=n;i++)f*=i;printf(\"%ld\\n\",f);return 0;}\n",
         ["5\n", "0\n", "1\n", "10\n", "12\n", "7\n"], qtype="complete", tags=["loops", "factorial"], fmt_in="One integer n (0 ≤ n ≤ 12).", fmt_out="n!",
         starter=H + "\nint main(void) {\n    int n;\n    if (scanf(\"%d\", &n) != 1) return 0;\n    long f = 1;\n    for (int i = 2; i <= n; i++) {\n        /* TODO: multiply f by i */\n    }\n    printf(\"%ld\\n\", f);\n    return 0;\n}\n"),
    code("Fix the Bug: Swap Two Numbers", "This program should read two integers and print them in swapped order, but it prints the wrong result. Find and fix the bug.",
         H + "int main(void){int a,b;if(scanf(\"%d %d\",&a,&b)!=2)return 0;int t=a;a=b;b=t;printf(\"%d %d\\n\",a,b);return 0;}\n",
         ["3 9\n", "5 5\n", "-2 7\n", "0 1\n", "100 -100\n"], qtype="debug", tags=["variables", "swap"], fmt_in="Two integers a and b.", fmt_out="b then a, separated by a space.",
         starter=H + "\nint main(void) {\n    int a, b;\n    if (scanf(\"%d %d\", &a, &b) != 2) return 0;\n    a = b;\n    b = a;\n    printf(\"%d %d\\n\", a, b);\n    return 0;\n}\n"),
    written("Trace the Code: Running Total", "Follow the loop step by step. What is printed at the end?",
            H + "int main(void){\n    int total = 0;\n    for (int i = 1; i <= 6; i++) {\n        if (i % 2 == 0) total += i;\n        else total -= 1;\n    }\n    printf(\"%d\\n\", total);\n    return 0;\n}\n",
            qtype="trace", tags=["loops", "tracing"]),
]

LOGIC = [
    code("Sum of Digits", "Read a non-negative integer and print the sum of its digits.",
         H + "int main(void){long n;if(scanf(\"%ld\",&n)!=1)return 0;int s=0;if(n<0)n=-n;while(n>0){s+=n%10;n/=10;}printf(\"%d\\n\",s);return 0;}\n",
         ["1234\n", "9\n", "0\n", "99999\n", "100000\n", "987654321\n"], tags=["loops", "digits"], fmt_in="One integer n (0 ≤ n ≤ 10^9).", fmt_out="Sum of the digits of n."),
    code("Reverse a Number", "Read a non-negative integer and print its digits in reverse order, without leading zeros.",
         H + "int main(void){long n;if(scanf(\"%ld\",&n)!=1)return 0;long r=0;while(n>0){r=r*10+n%10;n/=10;}printf(\"%ld\\n\",r);return 0;}\n",
         ["1234\n", "1200\n", "7\n", "0\n", "1000001\n", "98765\n"], tags=["loops", "digits"], fmt_in="One integer n (0 ≤ n ≤ 10^9).", fmt_out="n with its digits reversed."),
    code("Palindrome Number", "Read a non-negative integer. Print `Yes` if it reads the same forwards and backwards, otherwise `No`.",
         H + "int main(void){long n;if(scanf(\"%ld\",&n)!=1)return 0;long o=n,r=0;while(n>0){r=r*10+n%10;n/=10;}puts(r==o?\"Yes\":\"No\");return 0;}\n",
         ["121\n", "123\n", "7\n", "1221\n", "10\n", "0\n", "123454321\n"], tags=["loops", "digits"], fmt_in="One integer n.", fmt_out="`Yes` or `No`."),
    code("Prime Check", "Read an integer n. Print `Prime` if n is a prime number, otherwise `Not Prime`.",
         H + "int main(void){long n;if(scanf(\"%ld\",&n)!=1)return 0;int p=n>1;for(long i=2;i*i<=n;i++)if(n%i==0){p=0;break;}puts(p?\"Prime\":\"Not Prime\");return 0;}\n",
         ["7\n", "12\n", "1\n", "2\n", "97\n", "91\n", "999983\n", "0\n"], tags=["loops", "math"], fmt_in="One integer n (0 ≤ n ≤ 10^6).", fmt_out="`Prime` or `Not Prime`."),
    code("Star Triangle", "Read n and print a right-angled triangle of `*` with n rows: row i has i stars, with no spaces.",
         H + "int main(void){int n;if(scanf(\"%d\",&n)!=1)return 0;for(int i=1;i<=n;i++){for(int j=0;j<i;j++)putchar('*');putchar('\\n');}return 0;}\n",
         ["3\n", "1\n", "5\n", "2\n", "8\n"], tags=["nested-loops", "patterns"], fmt_in="One integer n (1 ≤ n ≤ 30).", fmt_out="n lines of stars."),
    code("Complete the Code: Fibonacci", "The program must read n (1 to 40) and print the n-th Fibonacci number, where F(1) = 1 and F(2) = 1. Complete the loop.",
         H + "int main(void){int n;if(scanf(\"%d\",&n)!=1)return 0;long a=1,b=1;for(int i=3;i<=n;i++){long c=a+b;a=b;b=c;}printf(\"%ld\\n\",b);return 0;}\n",
         ["5\n", "1\n", "2\n", "10\n", "40\n", "20\n"], qtype="complete", tags=["loops", "sequences"], fmt_in="One integer n (1 ≤ n ≤ 40).", fmt_out="F(n).",
         starter=H + "\nint main(void) {\n    int n;\n    if (scanf(\"%d\", &n) != 1) return 0;\n    long a = 1, b = 1;\n    for (int i = 3; i <= n; i++) {\n        /* TODO: move a and b one step forward */\n    }\n    printf(\"%ld\\n\", b);\n    return 0;\n}\n"),
    code("Fix the Bug: Count Multiples", "This program should read n and print how many numbers from 1 to n (inclusive) are divisible by 3. It gives a wrong count for some inputs. Fix it.",
         H + "int main(void){int n;if(scanf(\"%d\",&n)!=1)return 0;int c=0;for(int i=1;i<=n;i++)if(i%3==0)c++;printf(\"%d\\n\",c);return 0;}\n",
         ["9\n", "10\n", "3\n", "2\n", "30\n", "1\n"], qtype="debug", tags=["loops", "off-by-one"], fmt_in="One integer n (1 ≤ n ≤ 10^5).", fmt_out="The count.",
         starter=H + "\nint main(void) {\n    int n;\n    if (scanf(\"%d\", &n) != 1) return 0;\n    int count = 0;\n    for (int i = 1; i < n; i++) {\n        if (i % 3 == 0) count++;\n    }\n    printf(\"%d\\n\", count);\n    return 0;\n}\n"),
    written("Predict the Output: Nested Loops", "What does this program print?",
            H + "int main(void){\n    for (int i = 1; i <= 3; i++) {\n        for (int j = 1; j <= i; j++) printf(\"%d\", i * j);\n        printf(\"\\n\");\n    }\n    return 0;\n}\n", tags=["nested-loops"]),
    written("Trace the Code: Loop with Break", "Follow the loop carefully. What does it print?",
            H + "int main(void){\n    int n = 27, steps = 0;\n    while (n != 1) {\n        if (n % 2 == 0) n /= 2;\n        else n = n * 3 + 1;\n        steps++;\n        if (steps == 5) break;\n    }\n    printf(\"%d %d\\n\", n, steps);\n    return 0;\n}\n",
            qtype="trace", diff="Intermediate", tags=["while", "tracing"]),
    code("Second Largest", "Read n, then n integers. Print the second largest distinct value. If there is no such value, print `-1`.",
         H + "#include <limits.h>\nint main(void){int n;if(scanf(\"%d\",&n)!=1)return 0;long a=LONG_MIN,b=LONG_MIN,x;for(int i=0;i<n;i++){scanf(\"%ld\",&x);if(x>a){b=a;a=x;}else if(x<a&&x>b)b=x;}"
             "if(b==LONG_MIN)puts(\"-1\");else printf(\"%ld\\n\",b);return 0;}\n",
         ["5\n3 9 4 9 1\n", "3\n7 7 7\n", "2\n1 2\n", "1\n5\n", "6\n-1 -2 -3 -4 -5 -6\n", "4\n10 10 9 9\n", "5\n1 2 3 4 5\n"],
         diff="Intermediate", tags=["arrays", "logic"], fmt_in="First line: n (1 ≤ n ≤ 1000). Second line: n integers.", fmt_out="The second largest distinct value, or -1."),
]

if __name__ == "__main__":
    modules = [{"category": "C Basics", "questions": BASICS}, {"category": "C Logic Building", "questions": LOGIC}]
    out = ROOT / "app" / "seed_c.json"
    out.write_text(json.dumps({"key": "c_practice_v1", "modules": modules}, indent=1))
    print(f"wrote {out}: " + ", ".join(f"{m['category']} ({len(m['questions'])})" for m in modules))
    for m in modules:
        for q in m["questions"]:
            if q["languages"] == ["answer"]:
                print(f"  {q['title']} -> {q['tests'][0]['expected']!r}")
