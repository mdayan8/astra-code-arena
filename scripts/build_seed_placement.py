"""Builds app/seed_placement.json: three placement-preparation modules (TCS NQT / Infosys / Wipro / Accenture style).
Same rule as build_seed.py: every expected output comes from really compiling and running C (gcc), and every
computable concept answer is computed here in Python, never typed by hand.
    python scripts/build_seed_placement.py"""
import hashlib
import json
import sqlite3
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_seed import ROOT, code as _code, run_c  # noqa: E402  (importing also rebuilds the C seed lists in memory, a few seconds)


def mcq(title, question, correct, distractors, *, diff="Easy", tags=()):
    """Multiple choice: 4 options in a fixed order decided by a hash of the title, so the answer is not always first."""
    correct = str(correct).strip()
    distractors = [str(d).strip() for d in distractors]
    assert len(distractors) == 3 and len({correct, *distractors}) == 4, f"'{title}': need 3 distinct distractors that differ from {correct!r}"
    options = sorted([correct, *distractors], key=lambda o: hashlib.md5(f"{title}|{o}".encode()).hexdigest())
    return {"title": title, "statement": question, "input_format": "", "output_format": "", "difficulty": diff, "tags": list(tags), "qtype": "mcq",
            "languages": ["answer"], "references": {"answer": correct}, "starter": {}, "options": options,
            "tests": [{"input": "", "expected": correct, "visibility": "hidden"}]}


def mcq_output(title, question, snippet, distractors, *, pseudo="", diff="Easy", tags=()):
    """Predict-the-output MCQ: the correct option is whatever the C snippet really prints.
    With pseudo=..., the statement shows the pseudo-code and the C snippet is only used to compute the answer."""
    ans = run_c(snippet).strip()
    assert ans, title
    shown = f"```\n{pseudo.strip()}\n```" if pseudo else f"```c\n{snippet.strip()}\n```"
    return mcq(title, f"{question}\n\n{shown}", ans, distractors, diff=diff, tags=tags)


def code(*a, **k):
    return {**_code(*a, **k), "options": []}


# ---------------------------------------------------------------- 1. Predict the Output
OUTPUT = [
    mcq_output("Output MCQ: Operator Precedence", "What does this program print?", r"""
#include <stdio.h>
int main(void) {
    int x = 2 + 3 * 4 - 8 / 2;
    printf("%d\n", x);
    return 0;
}
""", ["16", "6", "3"], tags=["operators", "precedence"]),
    mcq_output("Output MCQ: Division and Remainder", "What does this program print?", r"""
#include <stdio.h>
int main(void) {
    int a = 7, b = 2;
    printf("%d %d %d\n", a / b, a % b, -a / b);
    return 0;
}
""", ["3.5 1 -3.5", "3 1 -4", "4 1 -3"], tags=["operators", "division", "modulo"]),
    mcq_output("Output MCQ: Pre and Post Increment", "What does this program print?", r"""
#include <stdio.h>
int main(void) {
    int a = 5;
    int b = a++ + 2;
    int c = ++a * 2;
    printf("%d %d %d\n", a, b, c);
    return 0;
}
""", ["7 8 14", "6 7 12", "7 7 12"], tags=["operators", "increment"]),
    mcq_output("Output MCQ: Loop with Continue", "What does this program print?", r"""
#include <stdio.h>
int main(void) {
    int sum = 0;
    for (int i = 1; i <= 10; i++) {
        if (i % 3 == 0) continue;
        sum += i;
    }
    printf("%d\n", sum);
    return 0;
}
""", ["55", "18", "34"], tags=["loops", "continue"]),
    mcq_output("Pseudo-code MCQ: Counting Nested Loops", "What is printed by this pseudo-code?", r"""
#include <stdio.h>
int main(void) {
    int count = 0;
    for (int i = 1; i <= 4; i++)
        for (int j = i; j <= 4; j++) count = count + 1;
    printf("%d\n", count);
    return 0;
}
""", ["16", "6", "12"], pseudo="""
Integer i, j, count
Set count = 0
For (each i from 1 to 4)
    For (each j from i to 4)
        count = count + 1
    End For
End For
Print count
""", tags=["pseudo-code", "nested-loops"]),
    mcq_output("Pseudo-code MCQ: Loop with Break", "What is printed by this pseudo-code?", r"""
#include <stdio.h>
int main(void) {
    int n, sum = 0;
    for (n = 1; n <= 10; n++) {
        sum = sum + n;
        if (sum > 12) break;
    }
    printf("%d %d\n", n, sum);
    return 0;
}
""", ["4 10", "5 12", "10 55"], pseudo="""
Integer n, sum
Set sum = 0
For (each n from 1 to 10)
    sum = sum + n
    If (sum > 12) then
        Break out of the loop
    End If
End For
Print n and sum separated by a space
""", tags=["pseudo-code", "loops", "break"]),
    mcq_output("Output MCQ: Switch Fall-through", "What does this program print?", r"""
#include <stdio.h>
int main(void) {
    int x = 2;
    switch (x) {
        case 1: printf("A");
        case 2: printf("B");
        case 3: printf("C");
                break;
        default: printf("D");
    }
    printf("\n");
    return 0;
}
""", ["B", "BCD", "ABC"], diff="Intermediate", tags=["switch", "fall-through"]),
    mcq_output("Output MCQ: Pointer Arithmetic", "What does this program print?", r"""
#include <stdio.h>
int main(void) {
    int a[] = {10, 20, 30, 40, 50};
    int *p = a + 1;
    p += 2;
    printf("%d %d %d\n", *p, *(p - 1), (int)(p - a));
    return 0;
}
""", ["30 20 2", "40 30 12", "40 39 3"], diff="Intermediate", tags=["arrays", "pointers"]),
    mcq_output("Output MCQ: strlen and sizeof", "What does this program print?", r"""
#include <stdio.h>
#include <string.h>
int main(void) {
    char s[] = "hello";
    printf("%d %d\n", (int)strlen(s), (int)sizeof(s));
    return 0;
}
""", ["5 5", "6 6", "5 8"], diff="Intermediate", tags=["strings", "sizeof"]),
    mcq_output("Output MCQ: Static Variable", "What does this program print?", r"""
#include <stdio.h>
int next(void) {
    static int c = 0;
    c += 2;
    return c;
}
int main(void) {
    int sum = 0;
    for (int i = 0; i < 3; i++) sum += next();
    printf("%d\n", sum);
    return 0;
}
""", ["6", "2", "8"], diff="Intermediate", tags=["functions", "static"]),
    mcq_output("Output MCQ: Bitwise Operators", "What does this program print?", r"""
#include <stdio.h>
int main(void) {
    int a = 12, b = 10;
    printf("%d %d %d %d\n", a & b, a | b, a ^ b, a << 2);
    return 0;
}
""", ["1 1 0 48", "8 14 6 24", "8 14 2 48"], diff="Intermediate", tags=["bitwise"]),
    mcq_output("Pseudo-code MCQ: Digit Loop", "What is printed by this pseudo-code? (`/` is integer division, `MOD` gives the remainder.)", r"""
#include <stdio.h>
int main(void) {
    int n = 4729, s = 0, d;
    while (n > 0) {
        d = n % 10;
        if (d % 2 == 0) s = s + d;
        else s = s - 1;
        n = n / 10;
    }
    printf("%d\n", s);
    return 0;
}
""", ["22", "6", "2"], pseudo="""
Integer n, s, d
Set n = 4729, s = 0
While (n > 0)
    d = n MOD 10
    If (d MOD 2 EQUALS 0) then
        s = s + d
    Else
        s = s - 1
    End If
    n = n / 10
End While
Print s
""", diff="Intermediate", tags=["pseudo-code", "digits", "while"]),
    mcq_output("Output MCQ: Short-circuit Evaluation", "What does this program print?", r"""
#include <stdio.h>
int main(void) {
    int a = 0, b = 5, c = 0;
    if (a++ && ++b) c = 1;
    if (a || b++) c += 2;
    printf("%d %d %d\n", a, b, c);
    return 0;
}
""", ["1 6 2", "1 7 3", "1 6 3"], diff="Challenging", tags=["logical-operators", "short-circuit"]),
    mcq_output("Pseudo-code MCQ: Double Recursion", "What is printed by this pseudo-code?", r"""
#include <stdio.h>
int fun(int n) {
    if (n <= 1) return n;
    return fun(n - 1) + fun(n - 2) + 1;
}
int main(void) {
    printf("%d\n", fun(5));
    return 0;
}
""", ["5", "8", "11"], pseudo="""
Function fun(Integer n)
    If (n <= 1) then
        Return n
    End If
    Return fun(n - 1) + fun(n - 2) + 1
End Function

Print fun(5)
""", diff="Challenging", tags=["pseudo-code", "recursion"]),
]


# ---------------------------------------------------------------- 2. CS Fundamentals (computable answers are computed)
def _loop_counts():
    n, i, halving = 1024, 1, 0
    while i < n:
        i *= 2
        halving += 1
    triangle = sum(1 for i in range(n) for _ in range(i, n))
    nlogn = 0
    for _ in range(n):
        j = 1
        while j <= n:
            j *= 2
            nlogn += 1
    return n, halving, triangle, nlogn


_n, _halving, _triangle, _nlogn = _loop_counts()
_lg = _n.bit_length() - 1
assert _halving == _lg and _triangle == _n * (_n + 1) // 2 and _nlogn == _n * (_lg + 1)

_ops = [("push", 4), ("push", 7), ("pop", None), ("push", 1), ("push", 9), ("pop", None), ("pop", None), ("pop", None)]
_stack, _queue, _lifo, _fifo = [], deque(), [], []
for _op, _v in _ops:
    if _op == "push":
        _stack.append(_v)
        _queue.append(_v)
    else:
        _lifo.append(_stack.pop())
        _fifo.append(_queue.popleft())
_lifo, _fifo = " ".join(map(str, _lifo)), " ".join(map(str, _fifo))

_div3, _div5 = sum(1 for k in range(1, 101) if k % 3 == 0), sum(1 for k in range(1, 101) if k % 5 == 0)
_div_either = sum(1 for k in range(1, 101) if k % 3 == 0 or k % 5 == 0)
_div_one_only = sum(1 for k in range(1, 101) if (k % 3 == 0) != (k % 5 == 0))

_depts = ["CSE", "ECE", "CSE", "ME", "CSE"]
_db = sqlite3.connect(":memory:")
_db.execute("CREATE TABLE Students (id INTEGER PRIMARY KEY, dept TEXT)")
_db.executemany("INSERT INTO Students (dept) VALUES (?)", [(d,) for d in _depts])
_groups = len(_db.execute("SELECT dept, COUNT(*) FROM Students GROUP BY dept").fetchall())

CONCEPTS = [
    mcq("CS MCQ: Loop That Doubles", "What is the time complexity of this loop?\n\n```c\nfor (int i = 1; i < n; i = i * 2)\n    count++;\n```",
        "O(log n)", ["O(n)", "O(n log n)", "O(n^2)"], tags=["time-complexity", "loops"]),
    mcq("CS MCQ: Matching Brackets", "You must check whether the brackets in an expression like `{[()]}` are properly matched. Which data structure fits this task best?",
        "Stack", ["Queue", "Binary search tree", "Hash table"], tags=["data-structures", "stack"]),
    mcq("CS MCQ: Binary Search Requirement", "What must be true about an array before binary search can be used on it?",
        "It must be sorted", ["It must contain only positive numbers", "Its size must be a power of 2", "It must not contain duplicate values"],
        tags=["searching", "binary-search"]),
    mcq("CS MCQ: Hiding the Data", "In OOP, keeping an object's data private and allowing access only through its methods is called:",
        "Encapsulation", ["Inheritance", "Polymorphism", "Recursion"], tags=["oop"]),
    mcq("CS MCQ: Identifying a Row", "In a database table, which key uniquely identifies every row and can never be NULL?",
        "Primary key", ["Foreign key", "Unique key", "Sort key"], tags=["dbms", "keys"]),
    mcq("CS MCQ: Waiting Forever", "Process A holds resource R1 and waits for R2. Process B holds R2 and waits for R1. Neither can ever continue. What is this situation called?",
        "Deadlock", ["Starvation", "Thrashing", "Context switching"], tags=["os", "deadlock"]),
    mcq("CS MCQ: Binary to Decimal", "What is the decimal value of the binary number `101101`?",
        int("101101", 2), [int("101011", 2), int("110101", 2), int("101101", 2) * 2], tags=["number-system", "binary"]),
    mcq("CS MCQ: Dependent Nested Loop", "What is the time complexity of this code?\n\n```c\nfor (int i = 0; i < n; i++)\n    for (int j = i; j < n; j++)\n        count++;\n```",
        "O(n^2)", ["O(n)", "O(n log n)", "O(2^n)"], diff="Intermediate", tags=["time-complexity", "nested-loops"]),
    mcq("CS MCQ: Stack Pop Order", "These operations run on an empty stack, in this order:\n\n`push 4, push 7, pop, push 1, push 9, pop, pop, pop`\n\nIn what order do the values come out?",
        _lifo, [_fifo, "9 1 7 4", "7 1 9 4"], diff="Intermediate", tags=["data-structures", "stack"]),
    mcq("CS MCQ: Reliable Sorting", "Which sorting algorithm always takes O(n log n) time, even in the worst case, and is also stable (equal elements keep their original order)?",
        "Merge sort", ["Quick sort", "Heap sort", "Selection sort"], diff="Intermediate", tags=["sorting"]),
    mcq("CS MCQ: Same Call, Different Behaviour", "A base-class reference calls `draw()`, and the version written in the object's actual class (`Circle` or `Square`) runs. This is decided while the program is running. What is it called?",
        "Run-time polymorphism", ["Method overloading", "Encapsulation", "Data abstraction"], diff="Intermediate", tags=["oop", "polymorphism"]),
    mcq("CS MCQ: GROUP BY Row Count", f"The table `Students` has {len(_depts)} rows. Their `dept` values are: {', '.join(_depts)}.\n\nHow many rows does this query return?\n\n```sql\nSELECT dept, COUNT(*) FROM Students GROUP BY dept;\n```",
        _groups, [len(_depts), 1, _groups + 1], diff="Intermediate", tags=["sql", "group-by"]),
    mcq("CS MCQ: Divisible by 3 or 5", "How many whole numbers from 1 to 100 are divisible by 3 or by 5 (or by both)?",
        _div_either, [_div3 + _div5, _div_one_only, _div3], diff="Intermediate", tags=["aptitude", "counting"]),
    mcq("CS MCQ: Loop Inside a Loop", "What is the time complexity of this code?\n\n```c\nfor (int i = 1; i <= n; i++)\n    for (int j = 1; j <= n; j = j * 2)\n        count++;\n```",
        "O(n log n)", ["O(n^2)", "O(n)", "O(log n)"], diff="Challenging", tags=["time-complexity", "nested-loops"]),
]


# ---------------------------------------------------------------- 3. Coding Patterns
PATTERNS = [
    code("Count Vowels and Consonants", "Read one line of text. Count how many letters are vowels (a, e, i, o, u in either case) and how many are consonants. Digits, spaces and symbols are not counted.", r"""#include <stdio.h>
#include <ctype.h>
#include <string.h>

int main(void) {
    int ch, vowels = 0, consonants = 0;
    while ((ch = getchar()) != EOF && ch != '\n') {
        if (!isalpha(ch)) continue;
        if (strchr("aeiou", tolower(ch))) vowels++;
        else consonants++;
    }
    printf("%d %d\n", vowels, consonants);
    return 0;
}
""", ["Hello World\n", "Programming in C\n", "AEIOU\n", "xyz\n", "12345 !!\n", "The Quick Brown Fox 2026\n", "a\n"],
         tags=["strings", "counting"], fmt_in="One line of text (at most 1000 characters).", fmt_out="Two integers separated by a space: vowels then consonants."),
    code("Digital Root", "Read a non-negative integer n. Add its digits. If the result has more than one digit, add its digits again, and repeat until a single digit is left. Print that digit.\n\nExample: 9875 → 9+8+7+5 = 29 → 2+9 = 11 → 1+1 = 2.", r"""#include <stdio.h>

int main(void) {
    long long n;
    if (scanf("%lld", &n) != 1 || n < 0) return 0;
    while (n >= 10) {
        long long s = 0;
        while (n > 0) {
            s += n % 10;
            n /= 10;
        }
        n = s;
    }
    printf("%lld\n", n);
    return 0;
}
""", ["9875\n", "5\n", "0\n", "99\n", "10\n", "38\n", "999999999999999999\n", "1000000000000000000\n"],
         tags=["loops", "digits"], fmt_in="One integer n (0 ≤ n ≤ 10^18).", fmt_out="One digit."),
    code("Number Triangle", "Read n and print a triangle of numbers with n rows. Row i contains the numbers 1 to i, separated by single spaces.", r"""#include <stdio.h>

int main(void) {
    int n;
    if (scanf("%d", &n) != 1) return 0;
    for (int i = 1; i <= n; i++) {
        for (int j = 1; j <= i; j++) {
            if (j > 1) putchar(' ');
            printf("%d", j);
        }
        putchar('\n');
    }
    return 0;
}
""", ["4\n", "1\n", "2\n", "5\n", "9\n", "12\n"],
         tags=["nested-loops", "patterns"], fmt_in="One integer n (1 ≤ n ≤ 20).", fmt_out="n lines. Line i has the numbers 1 to i separated by spaces, with no space at the end."),
    code("Second Largest Element", "Read n, then n integers. Print the second largest distinct value in the array. If every element is the same (or there is only one element), print `-1`.", r"""#include <stdio.h>

int main(void) {
    int n;
    if (scanf("%d", &n) != 1) return 0;
    long long first = 0, second = 0, x;
    int have_first = 0, have_second = 0;
    for (int i = 0; i < n; i++) {
        if (scanf("%lld", &x) != 1) break;
        if (!have_first || x > first) {
            if (have_first) {
                second = first;
                have_second = 1;
            }
            first = x;
            have_first = 1;
        } else if (x < first && (!have_second || x > second)) {
            second = x;
            have_second = 1;
        }
    }
    if (have_second) printf("%lld\n", second);
    else puts("-1");
    return 0;
}
""", ["5\n12 45 7 45 30\n", "4\n8 8 8 8\n", "1\n9\n", "2\n-3 -9\n", "6\n1 2 3 4 5 6\n", "6\n6 5 4 3 2 1\n", "5\n-1000000000 1000000000 0 1000000000 -1000000000\n"],
         tags=["arrays", "logic"], fmt_in="First line: n (1 ≤ n ≤ 1000). Second line: n integers, each between -10^9 and 10^9.", fmt_out="The second largest distinct value, or -1."),
    code("Reverse the Words", "Read one line containing words separated by spaces. Print the words in reverse order, separated by single spaces. The letters inside each word stay as they are.", r"""#include <stdio.h>
#include <string.h>

int main(void) {
    char line[1105];
    char *words[600];
    int n = 0;
    if (!fgets(line, sizeof line, stdin)) line[0] = '\0';
    for (char *t = strtok(line, " \t\r\n"); t != NULL; t = strtok(NULL, " \t\r\n")) words[n++] = t;
    for (int i = n - 1; i >= 0; i--) {
        fputs(words[i], stdout);
        if (i > 0) putchar(' ');
    }
    putchar('\n');
    return 0;
}
""", ["hello world\n", "I love coding in C\n", "single\n", "  leading and   trailing spaces  \n", "a b c d e f g\n", "Placement season is here!\n", "TCS Infosys Wipro\n"],
         diff="Intermediate", tags=["strings", "words"], fmt_in="One line of at most 1000 characters with at least one word. Words may be separated by more than one space.",
         fmt_out="The words in reverse order, separated by single spaces."),
    code("Anagram Check", "Read two words. Print `Yes` if one can be formed by rearranging all the letters of the other, otherwise print `No`.", r"""#include <stdio.h>

int main(void) {
    char a[1005], b[1005];
    int count[256] = {0};
    if (scanf("%1000s %1000s", a, b) != 2) return 0;
    for (int i = 0; a[i] != '\0'; i++) count[(unsigned char)a[i]]++;
    for (int i = 0; b[i] != '\0'; i++) count[(unsigned char)b[i]]--;
    for (int i = 0; i < 256; i++) {
        if (count[i] != 0) {
            puts("No");
            return 0;
        }
    }
    puts("Yes");
    return 0;
}
""", ["listen\nsilent\n", "hello\nworld\n", "a\na\n", "ab\na\n", "aabb\nabab\n", "aab\nabb\n", "triangle\nintegral\n", "abc\nabd\n"],
         diff="Intermediate", tags=["strings", "counting"], fmt_in="Two lines, one word on each. Each word has 1 to 1000 lowercase letters.", fmt_out="`Yes` or `No`."),
    code("Remove Duplicates from Sorted Array", "Read n, then n integers that are already sorted in non-decreasing order. Remove the repeated values so that each value appears once. Print how many values are left, then the values.", r"""#include <stdio.h>

int main(void) {
    static int a[1000];
    int n, m = 0, k = 0;
    if (scanf("%d", &n) != 1) return 0;
    if (n > 1000) n = 1000;
    while (m < n && scanf("%d", &a[m]) == 1) m++;
    for (int i = 0; i < m; i++) {
        if (k == 0 || a[i] != a[k - 1]) a[k++] = a[i];
    }
    printf("%d\n", k);
    for (int i = 0; i < k; i++) {
        if (i > 0) putchar(' ');
        printf("%d", a[i]);
    }
    putchar('\n');
    return 0;
}
""", ["7\n1 1 2 2 2 3 4\n", "5\n5 5 5 5 5\n", "1\n7\n", "6\n1 2 3 4 5 6\n", "8\n-3 -3 -1 0 0 0 2 2\n", "4\n10 10 20 20\n", "2\n-1000000000 1000000000\n"],
         diff="Intermediate", tags=["arrays", "two-pointers"], fmt_in="First line: n (1 ≤ n ≤ 1000). Second line: n sorted integers, each between -10^9 and 10^9.",
         fmt_out="First line: the number of distinct values. Second line: the distinct values in order, separated by spaces."),
    code("Find the Missing Number", "The numbers 1 to n were written down, and then exactly one of them was erased. Read n and the remaining n - 1 numbers (in any order) and print the missing one.", r"""#include <stdio.h>

int main(void) {
    long long n, x;
    if (scanf("%lld", &n) != 1) return 0;
    long long missing = n * (n + 1) / 2;
    for (long long i = 0; i < n - 1; i++) {
        if (scanf("%lld", &x) != 1) break;
        missing -= x;
    }
    printf("%lld\n", missing);
    return 0;
}
""", ["5\n1 2 4 5\n", "3\n3 1\n", "1\n\n", "2\n1\n", "2\n2\n", "10\n10 9 8 7 6 5 4 3 2\n", "6\n1 2 3 4 5\n",
      "1000\n" + " ".join(str(v) for v in range(1000, 0, -1) if v != 500) + "\n"],
         diff="Intermediate", tags=["arrays", "math"], fmt_in="First line: n (1 ≤ n ≤ 100000). Second line: n - 1 distinct integers from 1 to n (empty when n = 1).",
         fmt_out="The missing number."),
    code("Longest Run of a Character", "Read a word. Find the longest block of the same character repeated side by side. Print that character and the length of the block. If two blocks have the same length, print the one that comes first.", r"""#include <stdio.h>

int main(void) {
    char s[1005];
    if (scanf("%1000s", s) != 1) return 0;
    char best = s[0];
    int best_len = 1, run = 1;
    for (int i = 1; s[i] != '\0'; i++) {
        if (s[i] == s[i - 1]) run++;
        else run = 1;
        if (run > best_len) {
            best_len = run;
            best = s[i];
        }
    }
    printf("%c %d\n", best, best_len);
    return 0;
}
""", ["aabbbcc\n", "abcd\n", "z\n", "aaaa\n", "aabbaa\n", "abbbbbbcccccc\n", "xxyyyzzzz\n", "aabbbccc\n"],
         diff="Intermediate", tags=["strings", "counting"], fmt_in="One word of 1 to 1000 lowercase letters.", fmt_out="The character, a space, then the length of its longest run."),
    code("Balanced Parentheses", "Read a string made only of the brackets `(` `)` `[` `]` `{` `}`. Print `Balanced` if every opening bracket is closed by the same type of bracket in the correct order, otherwise print `Not Balanced`.", r"""#include <stdio.h>

int main(void) {
    char s[1005], stack[1005];
    int top = 0, ok = 1;
    if (scanf("%1000s", s) != 1) return 0;
    for (int i = 0; s[i] != '\0' && ok; i++) {
        char c = s[i];
        if (c == '(' || c == '[' || c == '{') {
            stack[top++] = c;
        } else if (c == ')' || c == ']' || c == '}') {
            char open = c == ')' ? '(' : c == ']' ? '[' : '{';
            if (top == 0 || stack[top - 1] != open) ok = 0;
            else top--;
        }
    }
    puts(ok && top == 0 ? "Balanced" : "Not Balanced");
    return 0;
}
""", ["{[()]}\n", "([)]\n", "()\n", "(\n", ")(\n", "((()))[]{}\n", "{[}\n", "())\n"],
         diff="Challenging", tags=["strings", "stack"], fmt_in="One string of 1 to 1000 bracket characters, with no spaces.", fmt_out="`Balanced` or `Not Balanced`."),
]


def check(path):
    """Fails loudly if the written file is not what the platform expects."""
    data = json.loads(path.read_text())
    qs = [q for m in data["modules"] for q in m["questions"]]
    assert data["key"] == "placement_v1" and [len(m["questions"]) for m in data["modules"]] == [14, 14, 10] and len(qs) == 38
    titles = [q["title"] for q in qs] + [q["title"] for m in json.loads((ROOT / "app" / "seed_c.json").read_text())["modules"] for q in m["questions"]]
    dupes = {t for t in titles if titles.count(t) > 1}
    assert not dupes, f"duplicate titles: {dupes}"
    fields = {"title", "statement", "input_format", "output_format", "difficulty", "tags", "qtype", "languages", "references", "starter", "tests", "options"}
    for q in qs:
        assert set(q) == fields, q["title"]
        if q["qtype"] == "mcq":
            ans = q["references"]["answer"]
            assert len(q["options"]) == 4 and len(set(q["options"])) == 4 and q["options"].count(ans) == 1, q["title"]
            assert q["languages"] == ["answer"] and q["tests"] == [{"input": "", "expected": ans, "visibility": "hidden"}], q["title"]
        else:
            assert q["options"] == [] and q["languages"] == ["c"] and len(q["tests"]) >= 6, q["title"]
            assert all(t["expected"].strip() for t in q["tests"]), q["title"]
            assert [t["visibility"] for t in q["tests"][:3]] == ["sample", "sample", "hidden"], q["title"]
    mix = [[sum(q["difficulty"] == d for q in m["questions"]) for d in ("Easy", "Intermediate", "Challenging")] for m in data["modules"]]
    assert mix == [[6, 6, 2], [7, 6, 1], [4, 5, 1]], mix
    assert sum("pseudo-code" in q["tags"] for q in data["modules"][0]["questions"]) == 4
    spots = [q["options"].index(q["references"]["answer"]) for q in qs if q["qtype"] == "mcq"]
    assert len(set(spots)) == 4, f"correct option is not spread over all four positions: {spots}"
    return spots


if __name__ == "__main__":
    modules = [{"category": "Placement: Predict the Output", "questions": OUTPUT},
               {"category": "Placement: CS Fundamentals", "questions": CONCEPTS},
               {"category": "Placement: Coding Patterns", "questions": PATTERNS}]
    out = ROOT / "app" / "seed_placement.json"
    out.write_text(json.dumps({"key": "placement_v1", "modules": modules}, indent=1))
    spots = check(out)
    print(f"wrote {out}: " + ", ".join(f"{m['category']} ({len(m['questions'])})" for m in modules))
    print("  correct option positions: " + "".join("ABCD"[s] for s in spots))
    for m in modules:
        for q in m["questions"]:
            if q["qtype"] == "mcq":
                print(f"  {q['title']} -> {q['references']['answer']!r}  options={q['options']}")
            else:
                print(f"  {q['title']} -> " + " | ".join(repr(t["expected"]) for t in q["tests"])[:150])
