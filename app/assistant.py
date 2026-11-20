"""Competition assistant: turns a plain description into a competition plan and picks matching questions from the bank.
Rule-based on purpose: it works offline, without the AI model, and always behaves the same way.
    "60 minute C round for 1st years tomorrow at 10am, 3 easy coding, 2 predict the output, 1 debug on loops, full screen, no internet"
Each new message only changes what it mentions, so "make it 90 minutes" or "add 2 tracing questions" refine the plan."""
import datetime
import json
import random
import re
from zoneinfo import ZoneInfo

from . import config, db

WORDNUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
ORD = {"1st": 1, "first": 1, "2nd": 2, "second": 2, "3rd": 3, "third": 3, "4th": 4, "fourth": 4, "final": 4}
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
TYPES = [("debug", r"debug|fix(?:ing)? (?:the )?bug|bug ?fix|find the bug|\bbugs?\b"), ("complete", r"complet|fill in|fill the|missing code"),
         ("trace", r"trac|dry.?run"), ("output", r"output|predict"), ("prompt", r"prompt"),
         ("standard", r"coding|code writing|standard|programming|write (?:a |the )?(?:program|code)|logic")]
TYPE_NAME = {"standard": "coding", "debug": "fix-the-bug", "complete": "complete-the-code", "output": "predict-the-output", "trace": "code-tracing", "prompt": "prompt", None: ""}
DIFFS = [("Easy", r"easy|simple|beginner|basic"), ("Intermediate", r"medium|intermediate|moderate"), ("Challenging", r"hard|challenging|difficult|tough|advanced")]
LANGS = [("cpp", r"c\+\+|\bcpp\b"), ("python", r"\bpython\b"), ("javascript", r"javascript|\bjs\b|node"), ("java", r"\bjava\b(?!script)"), ("c", r"(?<![\w+])c(?![\w+#])")]
STOP = {"the", "and", "with", "for", "question", "questions", "problem", "problems", "each", "type", "types", "topic", "topics", "round", "on", "about", "of", "from", "in"}


def _num(s):
    return int(s) if s.isdigit() else WORDNUM.get(s)


def _when(t, now):
    """-> 'YYYY-MM-DDTHH:MM' or None. Understands today/tomorrow/weekdays/dates, '10am', '14:30', 'in 2 hours'."""
    m = re.search(r"\bin (\d+|an?|one|two|three) (hours?|hrs?|minutes?|mins?)\b", t)
    if m:
        d = datetime.timedelta(**{"hours" if m.group(2)[0] == "h" else "minutes": _num(m.group(1))})
        return (now + d).strftime("%Y-%m-%dT%H:%M")
    date = None
    if "day after tomorrow" in t:
        date = now.date() + datetime.timedelta(days=2)
    elif re.search(r"\btomorrow\b|\btmrw\b", t):
        date = now.date() + datetime.timedelta(days=1)
    elif re.search(r"\btoday\b|\btonight\b", t):
        date = now.date()
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t)
    if m:
        date = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?(?: of)? (" + "|".join(MONTHS) + r")[a-z]*(?:,? (\d{4}))?", t) or \
        re.search(r"\b(" + "|".join(MONTHS) + r")[a-z]* (\d{1,2})(?:st|nd|rd|th)?(?:,? (\d{4}))?", t)
    if m:
        a, b, y = m.groups()
        day, mon = (int(a), MONTHS.index(b) + 1) if a.isdigit() else (int(b), MONTHS.index(a) + 1)
        date = datetime.date(int(y) if y else now.year, mon, day)
        if not y and date < now.date():
            date = date.replace(year=now.year + 1)
    m = re.search(r"\b(?:on |next |this )?(" + "|".join(DAYS) + r")\b", t)
    if m and not date:
        ahead = (DAYS.index(m.group(1)) - now.weekday()) % 7
        date = now.date() + datetime.timedelta(days=ahead or 7)
    clock = None
    m = re.search(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)", t) or re.search(r"\b(?:at|@|from|starts?(?: at)?) (\d{1,2})[:.](\d{2})()", t) or \
        re.search(r"\b(\d{1,2}):(\d{2})()\b", t)
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "")[:1]
        if ap == "p" and h < 12:
            h += 12
        if ap == "a" and h == 12:
            h = 0
        if h < 24 and mi < 60:
            clock = (h, mi)
    if not date and not clock:
        return None
    date = date or now.date()
    h, mi = clock or (10, 0)
    dt = datetime.datetime(date.year, date.month, date.day, h, mi, tzinfo=now.tzinfo)
    if dt <= now and clock and not re.search(r"today|tonight|\d{4}-|" + "|".join(MONTHS), t):
        dt += datetime.timedelta(days=1)
    return dt.strftime("%Y-%m-%dT%H:%M")


def _questions(t):
    """Find specs like '3 easy coding', 'two predict the output on loops', '1 hard debug question'."""
    specs = []
    for clause in re.split(r"[,;\n]| and | plus |\. ", t):
        m = re.search(r"\b(\d{1,2}|" + "|".join(WORDNUM) + r")\b(?!\s*(?:st|nd|rd|th|min|hour|hr|am|pm|:|\.\d|year|sem|mark|warning|%))", clause)
        if not m:
            continue
        n = _num(m.group(1))
        rest = clause[m.end():]
        typ = next((k for k, rx in TYPES if re.search(rx, rest)), None)
        diff = next((k for k, rx in DIFFS if re.search(rx, rest)), None)
        if not n or n > 30 or not (typ or diff or re.search(r"question|problem|challenge|\bqs?\b", rest)):
            continue
        tm = re.search(r"\b(?:on|about|from|covering) ([a-z0-9 +\-]{2,40})", rest)
        topic = " ".join(w for w in (tm.group(1).split() if tm else []) if w not in STOP)[:40]
        specs.append({"count": n, "type": typ, "difficulty": diff, "topic": topic})
    return specs


def parse(text, plan=None, departments=(), now=None):
    """Merge what this message says into the plan (a plain dict the admin page sends back each turn)."""
    now = now or datetime.datetime.now(ZoneInfo(config.APP_TIMEZONE))
    p = json.loads(json.dumps(plan or {}))
    raw = text.strip()
    t = " " + re.sub(r"\s+", " ", raw.lower()) + " "
    m = re.search(r"(?:called|named|titled|title(?: is|:)?|name(?: is|:)?) [\"“']?([^\"”'\n,.;]{3,80})", raw, re.I) or re.search(r"[\"“]([^\"”]{3,80})[\"”]", raw)
    if m:
        p["title"] = m.group(1).strip()
    start = _when(t, now)
    if start:
        p["start_at"] = start
    dur_text = re.sub(r"\bin (\d+|an?|one|two|three) (hours?|hrs?|minutes?|mins?)\b", " ", t)
    m = re.search(r"(\d+(?:\.\d+)?)\s*-?\s*(hours?|hrs?|minutes?|mins?)\b", dur_text) or re.search(r"\b(an?|one|two|three)\s+(hours?|hrs?)\b", dur_text)
    if m:
        v = float(m.group(1)) if m.group(1)[0].isdigit() else _num(m.group(1))
        p["duration_min"] = max(5, min(24 * 60, int(round(v * 60 if m.group(2)[0] == "h" else v))))
    if re.search(r"all years|every year|everyone|all students|any year", t):
        p["eligible_years"] = []
    else:
        m = re.search(r"((?:(?:" + "|".join(ORD) + r")[\s,/&]*(?:and |& |or )?)+)(?:years?|yrs?)\b", t)
        if m:
            p["eligible_years"] = sorted({ORD[w] for w in re.findall("|".join(ORD), m.group(1))})
    deps = [d for d in departments if re.search(r"(?<![\w])" + re.escape(d.lower()) + r"(?![\w])", t)]
    if deps:
        p["eligible_departments"] = deps
    elif re.search(r"all (?:departments|branches)", t):
        p["eligible_departments"] = []
    langs = [k for k, rx in LANGS if re.search(rx, t)]
    if langs:
        p["languages"] = sorted(set(langs), key=["c", "cpp", "python", "java", "javascript"].index)
    qs = _questions(t)
    if qs:
        p["questions"] = (p.get("questions", []) + qs) if re.search(r"\badd\b|\balso\b|\bmore\b|\bplus\b", t) and p.get("questions") else qs
    m = re.search(r"(\d+) marks? (?:each|per)", t)
    if m:
        p["marks"] = max(1, min(1000, int(m.group(1))))
    ld = p.setdefault("lockdown", {})
    if re.search(r"no internet|block(?:ed)? internet|without internet|internet (?:off|blocked|not allowed)|offline only", t):
        ld["internet"] = "block"
    elif re.search(r"internet (?:allowed|on|ok)|allow internet|with internet|open internet|online round", t):
        ld["internet"] = "allow"
    if re.search(r"no full ?screen|without full ?screen|full ?screen (?:off|optional|not required)", t):
        ld["fullscreen"] = "off"
    elif re.search(r"full ?screen", t):
        ld["fullscreen"] = "required"
    if re.search(r"no (?:copy|paste)|block (?:copy|paste|all pasting)|(?:copy|paste)[ -]?(?:paste )?(?:off|blocked|not allowed|disabled)", t):
        ld["no_paste"] = True
        ld["no_copy"] = bool(re.search(r"copy", t))
    elif re.search(r"allow (?:copy|paste)|(?:copy|paste)[ -]?(?:paste )?allowed", t):
        ld["no_paste"] = ld["no_copy"] = False
    m = re.search(r"(?:auto[- ]?submit|submit automatically|end the test)[^.,;]{0,25}?(\d+) (?:warnings?|violations?|tab switch)|(\d+) (?:warnings?|violations?|tab switches) ", t)
    if m:
        ld["max_violations"] = max(0, min(50, int(m.group(1) or m.group(2))))
    if re.search(r"tab switch(?:ing)? allowed|allow (?:tab )?switch", t):
        ld["leave"] = "allow"
    if re.search(r"hide (?:the )?(?:leaderboard|results)|(?:leaderboard|results) hidden|publish (?:results )?(?:manually|later)|manual results", t):
        p["leaderboard_visibility"] = "hidden"
    elif re.search(r"live leaderboard|show (?:the )?leaderboard", t):
        p["leaderboard_visibility"] = "live"
    elif re.search(r"(?:leaderboard|results) (?:at|after) (?:the )?end", t):
        p["leaderboard_visibility"] = "after_end"
    if re.search(r"all[- ]or[- ]nothing|no partial", t):
        p["scoring_mode"] = "all_or_nothing"
    elif re.search(r"partial", t):
        p["scoring_mode"] = "partial"
    return p


def pick(c, plan):
    """Choose approved, validated questions from the bank for each spec. -> (chosen rows, shortage messages)"""
    langs = plan.get("languages") or ["c", "python"]
    pool = db.all_(c, "SELECT q.id, q.category, q.difficulty, q.tags, qv.title, qv.qtype, qv.languages FROM questions q JOIN question_versions qv ON qv.id=q.latest_version_id "
                      "WHERE q.status='approved' AND qv.validated_at IS NOT NULL AND NOT EXISTS (SELECT 1 FROM competition_questions cq JOIN competitions co ON co.id=cq.competition_id "
                      "WHERE cq.question_id=q.id AND co.status IN ('published','live','paused'))")
    random.shuffle(pool)
    chosen, short = [], []
    for s in plan.get("questions") or []:
        words = [w for w in s.get("topic", "").split() if len(w) > 1]

        def ok(q, use_topic=True):
            ql = db.jl(q["languages"])
            lang_ok = any(l in ("answer", "prompt") for l in ql) or any(l in langs for l in ql)
            hay = f"{q['title']} {q['category']} {q['tags']}".lower()
            return (lang_ok and q["id"] not in {x["id"] for x in chosen} and (not s.get("type") or (q["qtype"] or "standard") == s["type"])
                    and (not s.get("difficulty") or q["difficulty"] == s["difficulty"]) and (not use_topic or not words or any(w.rstrip("s") in hay for w in words)))
        got = [q for q in pool if ok(q)][:s["count"]]
        if len(got) < s["count"] and words:  # not enough on that topic: fill with the same type and level
            got += [q for q in pool if ok(q, False) and q not in got][:s["count"] - len(got)]
            if got:
                short.append(f"Fewer than {s['count']} questions on \"{s['topic']}\": filled the rest with other {TYPE_NAME[s.get('type')] or ''} questions.".replace("  ", " "))
        chosen += got
        if len(got) < s["count"]:
            what = " ".join(x for x in [(s.get("difficulty") or "").lower(), TYPE_NAME[s.get("type")]] if x) or "matching"
            short.append(f"The question bank has only {len(got)} of the {s['count']} {what} questions you asked for (approved, validated, in {', '.join(langs)}). "
                         "Add more in Question Bank or AI Studio, or change the request.")
    return chosen, short


def describe(plan, chosen, short, now=None):
    """Human-readable lines for the chat reply."""
    now = now or datetime.datetime.now(ZoneInfo(config.APP_TIMEZONE))
    yrs = plan.get("eligible_years") or []
    who = ("Years " + ", ".join(map(str, yrs))) if yrs else "All years"
    if plan.get("eligible_departments"):
        who += " · " + ", ".join(plan["eligible_departments"])
    start = "No start time yet (set it before publishing)"
    if plan.get("start_at"):
        start = datetime.datetime.fromisoformat(plan["start_at"]).strftime("%a %d %b %Y, %I:%M %p")
    ld = plan.get("lockdown") or {}
    rules = [{"block": "internet blocked", "allow": "internet allowed"}.get(ld.get("internet"), "internet reported"),
             "full screen optional" if ld.get("fullscreen") == "off" else "full screen required",
             f"auto-submit at {ld['max_violations']} warnings" if ld.get("max_violations") else "", "no copy" if ld.get("no_copy") else "", "no paste" if ld.get("no_paste") else ""]
    lines = [["Title", plan.get("title") or default_title(plan, now)], ["Duration", f"{plan.get('duration_min', 90)} minutes"], ["Starts", start], ["Who", who],
             ["Languages", ", ".join(plan.get("languages") or ["c", "python"])], ["Rules", " · ".join(r for r in rules if r)],
             ["Results", {"hidden": "hidden until you publish", "after_end": "shown at the end"}.get(plan.get("leaderboard_visibility"), "live leaderboard")]]
    todo = []
    if not plan.get("questions"):
        todo.append("Tell me the questions, for example: \"3 easy coding, 2 predict the output, 1 debug\".")
    if not plan.get("start_at"):
        todo.append("No start time yet. Say for example \"tomorrow at 10am\", or set it later in the competition.")
    return {"lines": lines, "questions": [{"id": q["id"], "title": q["title"], "difficulty": q["difficulty"], "qtype": q["qtype"] or "standard", "category": q["category"]} for q in chosen],
            "problems": short, "todo": todo, "ready": bool(chosen)}


def default_title(plan, now):
    yrs = plan.get("eligible_years") or []
    who = {1: "First Year", 2: "Second Year", 3: "Third Year", 4: "Final Year"}.get(yrs[0], "") if len(yrs) == 1 else ""
    lang = {"c": "C", "cpp": "C++", "python": "Python", "java": "Java", "javascript": "JavaScript"}.get((plan.get("languages") or [""])[0], "") if len(plan.get("languages") or []) == 1 else "Coding"
    day = datetime.datetime.fromisoformat(plan["start_at"]) if plan.get("start_at") else now
    return " ".join(x for x in [who, lang, "Round", day.strftime("%d %b")] if x)


if __name__ == "__main__":  # self-check
    tz = ZoneInfo(config.APP_TIMEZONE)
    now = datetime.datetime(2026, 11, 20, 15, 0, tzinfo=tz)  # a Friday
    p = parse('Create a 60 minute C round for 1st years tomorrow at 10am called "Freshers Cup", 3 easy coding, 2 predict the output, 1 debug on loops, full screen, no internet, '
              'auto submit after 3 warnings, hide the leaderboard', {}, ["CSE", "ISE"], now)
    assert p["title"] == "Freshers Cup" and p["duration_min"] == 60 and p["eligible_years"] == [1] and p["languages"] == ["c"], p
    assert p["start_at"] == "2026-11-21T10:00", p["start_at"]
    assert p["questions"] == [{"count": 3, "type": "standard", "difficulty": "Easy", "topic": ""}, {"count": 2, "type": "output", "difficulty": None, "topic": ""},
                              {"count": 1, "type": "debug", "difficulty": None, "topic": "loops"}], p["questions"]
    assert p["lockdown"] == {"internet": "block", "fullscreen": "required", "max_violations": 3} and p["leaderboard_visibility"] == "hidden", p
    p2 = parse("make it 1.5 hours and add 2 code tracing questions, for CSE and ISE 2nd and 3rd year, python and c++ too", p, ["CSE", "ISE"], now)
    assert p2["duration_min"] == 90 and len(p2["questions"]) == 4 and p2["questions"][-1]["type"] == "trace" and p2["title"] == "Freshers Cup", p2
    assert p2["eligible_years"] == [2, 3] and p2["eligible_departments"] == ["CSE", "ISE"] and p2["languages"] == ["cpp", "python"], p2
    assert parse("on 25 nov at 2:30 pm", {}, [], now)["start_at"] == "2026-11-25T14:30"
    assert parse("next monday 9am, two hours", {}, [], now) == {"start_at": "2026-11-23T09:00", "duration_min": 120, "lockdown": {}}
    assert parse("starts in 2 hours, 45 min", {}, [], now)["start_at"] == "2026-11-20T17:00" and parse("starts in 2 hours, 45 min", {}, [], now)["duration_min"] == 45
    assert parse("at 9am", {}, [], now)["start_at"] == "2026-11-21T09:00"      # 9am already passed today -> tomorrow
    assert parse("5 hard questions on arrays", {}, [], now)["questions"] == [{"count": 5, "type": None, "difficulty": "Challenging", "topic": "arrays"}]
    assert "questions" not in parse("for 2nd year students, 90 minutes", {}, [], now)
    print("assistant self-check ok")
