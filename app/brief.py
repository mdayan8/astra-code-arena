"""Description + rules/instructions of a competition, written from its real settings (always correct), refreshed while
the organiser has not edited them. With the AI on, the description is reworded in the background (facts stay the same)."""
import json
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from . import ai, contest, db, judge

TZ = ZoneInfo("Asia/Kolkata")
PLACEHOLDERS = {"", "Created with the competition assistant.", "Instant test round created from the Competitions page.",
                "## Rules\n- Individual participation only.\n- Read from standard input, write to standard output.\n- Do not share code during the contest."}
KIND_NAME = {"coding": "coding", "debug": "fix-the-bug", "mcq": "multiple choice", "output": "predict-the-output", "trace": "code-tracing", "prompt": "prompt"}


def _n(x):
    return int(x) if float(x).is_integer() else round(float(x), 1)


def facts(c, cid):
    comp = contest.get_comp(c, cid)
    qs = db.all_(c, "SELECT qv.qtype, q.difficulty FROM competition_questions cq JOIN question_versions qv ON qv.id=cq.version_id "
                    "JOIN questions q ON q.id=cq.question_id WHERE cq.competition_id=?", cid)
    per = comp["draw"] if comp["draw"] and comp["draw"] < len(qs) else len(qs)
    mix = db.jl(db.setting(c, f"mix:{cid}", "{}"), {})
    if not mix:   # what the list holds, scaled to one paper
        have = {}
        for q in qs:
            k = contest.KIND.get(q["qtype"] or "standard", "coding")
            have[k] = have.get(k, 0) + 1
        mix = dict(zip(have, contest._split(per, list(have.values())))) if have else {}
    mix = {contest.KIND.get(k, k): v for k, v in mix.items() if v}
    levels = sorted({q["difficulty"] for q in qs}, key=["Easy", "Intermediate", "Challenging"].index) if qs else []
    fixed = contest.fixed_total(c, cid)
    return {"comp": comp, "per": per, "pool": len(qs), "mix": mix, "levels": levels, "total": fixed or sum(r["marks"] for r in db.all_(
        c, "SELECT marks FROM competition_questions WHERE competition_id=?", cid)) * (per / len(qs) if qs else 1), "fixed": bool(fixed),
        "sc": contest.scoring_cfg(comp), "ld": contest.lockdown_cfg(comp.get("lockdown") or "{}")}


def write(c, cid):
    f = facts(c, cid)
    comp, sc, ld = f["comp"], f["sc"], f["ld"]
    mix = ", ".join(f"{v} {KIND_NAME.get(k, k)}" for k, v in f["mix"].items())
    lv = " / ".join(f["levels"]).lower() if f["levels"] else ""
    when = datetime.fromtimestamp(comp["start_at"], TZ).strftime("%d %b %Y, %I:%M %p") if comp["start_at"] else "to be announced"
    own = comp["draw"] and comp["draw"] < f["pool"]
    langs = ", ".join(judge.LANGS[l]["name"] for l in db.jl(comp["languages"]) if l in judge.LANGS)
    nq = f" with {f['per']} questions" if f["per"] else ""
    mx = f" ({mix})" if mix else ""
    desc = (f"**{comp['title']}** is a {comp['duration_min']}-minute {lv + ' ' if lv else ''}coding round{nq}{mx}, worth {_n(f['total'])} marks. "
            + ("Every student gets their own set of questions of the same kind and level, so the paper is fair for all. " if own else "")
            + f"Starts {when}.")
    r = ["## Before you start", f"- Starts **{when}**, lasts **{comp['duration_min']} minutes**. The timer runs from the start of the round" +
         (", so joining late leaves you less time." if comp["allow_late_join"] else "; late joining is not allowed."),
         "- Use a laptop or desktop with a charged battery. Keep your participant ID and password ready.",
         "", "## Your paper"]
    if f["per"]:
        r.append(f"- You get **{f['per']} questions**{mx}" + (", picked for you from a larger set; your neighbours have different ones." if own else "."))
    if comp["shuffle"]:
        r.append("- Question order (and multiple-choice options) is mixed for each student.")
    r.append(f"- Total **{_n(f['total'])} marks**." + (" Harder questions and coding / fix-the-bug questions are worth more than an easy multiple choice; each question shows its marks." if f["fixed"] else ""))
    if langs:
        r.append(f"- Languages: {langs}. Read input from standard input and print only what is asked (no \"Enter a number\" prompts).")
    r += ["", "## Scoring"]
    r.append("- Coding questions earn marks for every hidden test case passed; all tests = full marks." if sc["mode"] == "partial"
             else "- Coding questions give marks only when every hidden test case passes.")
    r.append("- Multiple-choice and output answers: **your first submitted answer is final**.")
    if sc["negative_marks"]:
        r.append(f"- Each wrong submission costs {_n(sc['negative_marks'])} mark(s) on that question (never below 0).")
    r.append(f"- Equal scores are ranked by time taken" + (f", with {sc['penalty_minutes']} minutes added for each wrong attempt." if sc["tie_break"] == "penalty" and sc["penalty_minutes"] else "."))
    r.append({"live": "- The leaderboard is live during the round.", "after_end": "- Results appear when the round ends.",
              "hidden": "- Results are published by the organisers after the round."}[comp["leaderboard_visibility"]])
    r += ["", "## Fair play"]
    if ld["fullscreen"] == "required":
        r.append("- The test runs in **full screen**. Leaving full screen or switching tabs/windows is recorded as a warning.")
    elif ld["leave"] == "warn":
        r.append("- Switching tabs or windows is recorded as a warning.")
    if ld["internet"] != "allow":
        r.append("- Keep the internet **off** (only the contest network). Using the internet " + ("is blocked." if ld["internet"] == "block" else "is recorded as a warning."))
    if sc["violation_pct"]:
        r.append(f"- Each warning costs **{_n(sc['violation_pct'])}%** of your total marks.")
    if ld["max_violations"]:
        r.append(f"- At **{ld['max_violations']} warnings** your test is submitted automatically.")
    if ld["no_copy"] or ld["no_paste"] or comp["paste_policy"] == "block_external":
        r.append("- Copying and pasting from outside the test is not allowed.")
    elif comp["paste_policy"] == "log":
        r.append("- Pasting is recorded and reviewed.")
    r += ["- Individual work only: no phones, notes, friends or AI tools. Copied code is checked after the round.",
          "", "## Finishing", "- Your work is saved as you go. Press **Finish test** when you are done; when time runs out it is submitted for you.",
          "- After the round you can review each question: your answer, the correct one, and why."]
    return desc, "\n".join(r)


def refresh(c, cid, force=False):
    """Rewrite the parts the organiser has not changed by hand (or both when forced). Never raises."""
    try:
        comp = contest.get_comp(c, cid)
        if not comp or comp["status"] in ("finalized", "archived", "cancelled"):
            return
        last = db.jl(db.setting(c, f"brief:{cid}", "[]"), []) or ["", ""]
        desc, instr = write(c, cid)
        upd = {}
        if force or comp["description"].strip() in PLACEHOLDERS or comp["description"] == last[0]:
            upd["description"] = desc
        if force or comp["instructions"].strip() in PLACEHOLDERS or comp["instructions"] == last[1]:
            upd["instructions"] = instr
        if upd:
            c.execute(f"UPDATE competitions SET {','.join(k + '=?' for k in upd)} WHERE id=?", (*upd.values(), cid))
            db.set_setting(c, f"brief:{cid}", json.dumps([upd.get("description", last[0]), upd.get("instructions", last[1])]))
            if "description" in upd and ai.enabled():
                threading.Thread(target=_ai_words, args=(cid, desc, instr), daemon=True).start()
    except Exception as e:
        db.log_error(c, "brief", e)


def _ai_words(cid, desc, instr):
    """Background: friendlier wording of the description; only kept if nothing changed meanwhile and no number is lost."""
    c = db.connect()
    try:
        txt = ai.chat_text("You write short, friendly announcements for a college coding contest. Use ONLY the facts given; keep every number exactly. "
                           "2-3 sentences, no headings, no emojis.", f"Facts:\n{desc}\n\nRules (for context):\n{instr[:1500]}", 220).strip()
        import re
        if not txt or len(txt) > 900 or not all(n in txt for n in re.findall(r"\d+", desc)):
            return
        comp = contest.get_comp(c, cid)
        if comp and comp["description"] == desc:
            c.execute("UPDATE competitions SET description=? WHERE id=?", (txt, cid))
            last = db.jl(db.setting(c, f"brief:{cid}", "[]"), []) or ["", ""]
            db.set_setting(c, f"brief:{cid}", json.dumps([txt, last[1]]))
    except Exception as e:
        db.log_error(c, "brief-ai", e)
    finally:
        c.close()
