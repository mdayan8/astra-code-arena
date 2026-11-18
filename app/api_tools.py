"""AI Tools Lab (admin builds tools by describing them; switches them on per competition / practice),
AI document conversion, and adaptive learning insights for students."""
import json
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import ai, db
from .api_admin import admin, comp_or_404, drafts_for
from .api_student import arena_access, conn, practice_question, student, student_submission
from .security import audit, client_ip, need, rate_limit

router = APIRouter()
A = "/api/admin"

PRESETS = {
    "hint": {"name": "Hint", "button_label": "Get a hint", "icon": "bulb", "description": "A small nudge in the right direction. Never the answer.",
             "system_prompt": "You are a patient programming tutor for first-year engineering students. Give ONE short hint (2-4 sentences) that helps the student take the next step on this problem. Point at the idea or the edge case they are missing. Do not give code or the full algorithm.",
             "inputs": [{"key": "stuck", "label": "Where are you stuck? (optional)", "type": "text", "options": [], "placeholder": "e.g. my loop gives the wrong answer for n = 1"}],
             "include": {"problem": True, "code": True, "last_result": True}, "allow_full_solutions": False, "max_uses_per_problem": 3},
    "explain": {"name": "Explain my error", "button_label": "Explain my error", "icon": "alert", "description": "Explains your last compile error, crash or wrong answer in plain words.",
                "system_prompt": "You explain programming errors to beginners. Using the student's code and their last judge result, explain in simple words what the error means and where in their code it most likely comes from. Suggest what to check. Do not rewrite their whole program.",
                "inputs": [], "include": {"problem": True, "code": True, "last_result": True}, "allow_full_solutions": False, "max_uses_per_problem": 5},
    "review": {"name": "Code review", "button_label": "Review my code", "icon": "code", "description": "Feedback on readability, efficiency and edge cases after you solve it.",
               "system_prompt": "You are a friendly senior engineer reviewing a beginner's solution. Give 3-5 concise bullet points: correctness risks / edge cases, time complexity, and readability. Praise one thing they did well. Do not provide a rewritten full solution.",
               "inputs": [], "include": {"problem": True, "code": True, "last_result": True}, "allow_full_solutions": False, "max_uses_per_problem": 2},
    "concept": {"name": "Concept explainer", "button_label": "Explain a concept", "icon": "book", "description": "Ask about any programming concept with a tiny example.",
                "system_prompt": "You teach programming concepts to beginners with a short explanation and a tiny generic example (not specific to the current problem's solution).",
                "inputs": [{"key": "topic", "label": "Concept", "type": "text", "options": [], "placeholder": "e.g. two pointers, recursion, hashing"}],
                "include": {"problem": False, "code": False, "last_result": False}, "allow_full_solutions": False, "max_uses_per_problem": 5},
}


def tool_row(r, with_prompt=True):
    spec = db.jl(r["spec"], {}) or {}
    if not with_prompt:
        spec = {k: v for k, v in spec.items() if k not in ("system_prompt",)}
    return {"id": r["id"], "name": r["name"], "status": r["status"], "spec": spec, "scope": db.jl(r["scope"], {}) or {"competitions": [], "practice": False},
            "created_at": r["created_at"], "updated_at": r["updated_at"]}


# ---------------- admin: tools lab ----------------

@router.get(f"{A}/ai/tools")
def list_tools(u=Depends(admin), c=Depends(conn)):
    need(u, "ai")
    rows = [tool_row(r) for r in db.all_(c, "SELECT * FROM ai_tools ORDER BY id DESC")]
    for t in rows:
        t["uses"] = db.one(c, "SELECT COUNT(*) AS n FROM ai_tool_uses WHERE tool_id=?", t["id"])["n"]
    return {"tools": rows, "presets": {k: {"name": v["name"], "description": v["description"], "icon": v["icon"]} for k, v in PRESETS.items()},
            "ai": ai.status() if ai.enabled() else {"enabled": False}}


class DesignIn(BaseModel):
    request: str = Field(min_length=5, max_length=1500)


@router.post(f"{A}/ai/tools/design")
def design(body: DesignIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "ai")
    if not ai.enabled():
        raise HTTPException(503, "AI is not configured on this server.")
    try:
        spec = ai.design_tool(body.request)
    except Exception as e:
        raise HTTPException(502, f"The AI could not design the tool: {e}")
    audit(c, u, "ai_tool.design", detail={"request": body.request[:200]}, ip=client_ip(req))
    return spec


class QuickIn(BaseModel):
    request: str = Field(min_length=5, max_length=1500)
    scope: dict = {}


@router.post(f"{A}/ai/tools/quick")
def quick_create(body: QuickIn, req: Request, u=Depends(admin), c=Depends(conn)):
    """One box: describe a tool -> the agent designs it -> it is created and switched on where you chose, in one step."""
    need(u, "ai")
    if not ai.enabled():
        raise HTTPException(503, "AI is not configured on this server.")
    try:
        spec = ai.design_tool(body.request)
    except Exception as e:
        raise HTTPException(502, f"The AI could not design the tool: {e}")
    scope = clean_scope(c, u, body.scope)
    t = db.now()
    tid = c.execute("INSERT INTO ai_tools(name,spec,status,scope,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (spec["name"], json.dumps(spec), "active" if (scope["practice"] or scope["competitions"]) else "draft", json.dumps(scope), u["id"], t, t)).lastrowid
    audit(c, u, "ai_tool.quick_create", "ai_tool", tid, detail={"request": body.request[:200], "scope": scope}, ip=client_ip(req))
    return tool_row(db.one(c, "SELECT * FROM ai_tools WHERE id=?", tid))


class ToolIn(BaseModel):
    spec: dict
    status: str = Field(default="draft", pattern="^(draft|active|off)$")
    scope: dict = {}


def clean_scope(c, u, scope):
    comps = [int(x) for x in (scope.get("competitions") or []) if str(x).isdigit()]
    for cid in comps:
        comp_or_404(c, cid, u)  # organisers can only switch tools on in competitions they manage
    return {"competitions": sorted(set(comps)), "practice": bool(scope.get("practice"))}


@router.post(f"{A}/ai/tools")
def create_tool(body: ToolIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "ai")
    spec = ai.normalize_tool(body.spec)
    t = db.now()
    tid = c.execute("INSERT INTO ai_tools(name,spec,status,scope,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (spec["name"], json.dumps(spec), body.status, json.dumps(clean_scope(c, u, body.scope)), u["id"], t, t)).lastrowid
    audit(c, u, "ai_tool.create", "ai_tool", tid, detail={"name": spec["name"]}, ip=client_ip(req))
    return tool_row(db.one(c, "SELECT * FROM ai_tools WHERE id=?", tid))


@router.post(f"{A}/ai/tools/presets/{{key}}")
def install_preset(key: str, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "ai")
    if key not in PRESETS:
        raise HTTPException(404, "Unknown preset.")
    return create_tool(ToolIn(spec=PRESETS[key]), req, u, c)


@router.put(f"{A}/ai/tools/{{tid}}")
def update_tool(tid: int, body: ToolIn, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "ai")
    if not db.one(c, "SELECT 1 AS x FROM ai_tools WHERE id=?", tid):
        raise HTTPException(404, "Tool not found.")
    spec = ai.normalize_tool(body.spec)
    scope = clean_scope(c, u, body.scope)
    c.execute("UPDATE ai_tools SET name=?, spec=?, status=?, scope=?, updated_at=? WHERE id=?", (spec["name"], json.dumps(spec), body.status, json.dumps(scope), db.now(), tid))
    audit(c, u, "ai_tool.update", "ai_tool", tid, detail={"status": body.status, "scope": scope}, ip=client_ip(req))
    return tool_row(db.one(c, "SELECT * FROM ai_tools WHERE id=?", tid))


@router.delete(f"{A}/ai/tools/{{tid}}")
def delete_tool(tid: int, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "ai")
    c.execute("DELETE FROM ai_tools WHERE id=?", (tid,))
    audit(c, u, "ai_tool.delete", "ai_tool", tid, ip=client_ip(req))
    return {"ok": True}


@router.get(f"{A}/ai/tools/{{tid}}/uses")
def tool_uses(tid: int, u=Depends(admin), c=Depends(conn)):
    need(u, "ai")
    return db.all_(c, "SELECT tu.*, us.name, s.participant_id, co.code AS competition FROM ai_tool_uses tu JOIN users us ON us.id=tu.user_id "
                      "LEFT JOIN students s ON s.user_id=us.id LEFT JOIN competitions co ON co.id=tu.competition_id WHERE tu.tool_id=? ORDER BY tu.id DESC LIMIT 200", tid)


def build_prompt(c, spec, question_id, code, language, inputs, last):
    parts = []
    inc = spec.get("include", {})
    if inc.get("problem") and question_id:
        v = db.one(c, "SELECT qv.* FROM questions q JOIN question_versions qv ON qv.id=q.latest_version_id WHERE q.id=?", question_id)
        if v:
            parts.append(f"## Problem: {v['title']}\n{v['statement']}\nInput format: {v['input_format']}\nOutput format: {v['output_format']}\nConstraints: {v['constraints']}")
    if inc.get("code") and code:
        parts.append(f"## Student's current code ({language})\n```\n{code[:8000]}\n```")
    if inc.get("last_result") and last:
        lr = f"## Student's last judge result\nVerdict: {last.get('verdict_name')}"
        if last.get("compile_output"):
            lr += f"\nCompiler/runtime message:\n{last['compile_output'][:1500]}"
        if last.get("failed_sample"):
            fs = last["failed_sample"]
            lr += f"\nFailed sample input:\n{fs['input'][:500]}\nExpected:\n{fs['expected'][:300]}\nTheir output:\n{(fs['output'] or '')[:300]}"
        parts.append(lr)
    for x in spec.get("inputs", []):
        val = str((inputs or {}).get(x["key"], ""))[:2000]
        if val.strip():
            parts.append(f"## {x['label']}\n{val}")
    guard = ("" if spec.get("allow_full_solutions") else
             "\nRules: never write a complete solution or a full program; at most a 3-line illustrative snippet. Guide, do not solve.")
    system = spec.get("system_prompt", "") + guard + "\nAnswer in clear markdown, under 220 words. Be encouraging."
    return system, "\n\n".join(parts) or "The student asked for help."


class TestIn(BaseModel):
    question_id: int | None = None
    code: str = Field(default="", max_length=65536)
    language: str = Field(default="c", max_length=20)
    inputs: dict = {}


@router.post(f"{A}/ai/tools/{{tid}}/test")
def test_tool(tid: int, body: TestIn, u=Depends(admin), c=Depends(conn)):
    need(u, "ai")
    r = db.one(c, "SELECT * FROM ai_tools WHERE id=?", tid)
    if not r:
        raise HTTPException(404, "Tool not found.")
    if not ai.enabled():
        raise HTTPException(503, "AI is not configured on this server.")
    system, user = build_prompt(c, db.jl(r["spec"], {}), body.question_id, body.code, body.language, body.inputs, None)
    t0 = time.time()
    try:
        out = ai.chat_text(system, user)
    except ai.AIBusy as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(502, f"AI request failed: {e}")
    return {"output": out, "ms": int((time.time() - t0) * 1000)}


# ---------------- admin: AI conversion of imported documents ----------------

def _convert_worker(draft_ids, user_id):
    for gid in draft_ids:
        c = db.connect()
        g = db.one(c, "SELECT * FROM generated_question_drafts WHERE id=?", gid)
        c.close()
        if g:
            ai.run_draft(gid, "convert", db.jl(g["payload"], {}), user_id)  # sequential: one model call at a time


@router.post(f"{A}/pdf/{{did}}/ai")
def convert_document(did: int, req: Request, u=Depends(admin), c=Depends(conn)):
    """AI reads every detected question of a document (or the whole text if none were detected) and builds complete,
    sandbox-validated questions with reference solutions and test cases."""
    need(u, "ai")
    if not ai.enabled():
        raise HTTPException(503, "AI is not configured on this server.")
    d = db.one(c, "SELECT * FROM uploaded_documents WHERE id=?", did)
    if not d or d["status"] != "done":
        raise HTTPException(409, "The document is not processed yet.")
    gids = [g["id"] for g in drafts_for(c, "document_id=? AND g.status IN ('pending','failed') AND g.question_id IS NULL", did)]
    if not gids:
        text = (d["extracted_text"] or "").strip()
        if not text:
            raise HTTPException(400, "No text was extracted from this document.")
        gids = [c.execute("INSERT INTO generated_question_drafts(source,document_id,status,payload,created_by,created_at) VALUES('pdf',?,'pending',?,?,?)",
                          (did, json.dumps({"title": d["filename"], "raw_text": text[:8000], "samples": []}), u["id"], db.now())).lastrowid]
    c.execute(f"UPDATE generated_question_drafts SET status='generating' WHERE id IN ({','.join('?' * len(gids))})", gids)
    audit(c, u, "document.ai_convert", "document", did, detail={"drafts": gids}, ip=client_ip(req))
    threading.Thread(target=_convert_worker, args=(gids, u["id"]), daemon=True).start()
    return {"converting": gids}


@router.post(f"{A}/drafts/{{gid}}/ai")
def convert_draft(gid: int, req: Request, u=Depends(admin), c=Depends(conn)):
    need(u, "ai")
    if not ai.enabled():
        raise HTTPException(503, "AI is not configured on this server.")
    g = db.one(c, "SELECT * FROM generated_question_drafts WHERE id=? AND status IN ('pending','failed') AND question_id IS NULL", gid)
    if not g:
        raise HTTPException(409, "This draft cannot be converted.")
    c.execute("UPDATE generated_question_drafts SET status='generating' WHERE id=?", (gid,))
    audit(c, u, "draft.ai_convert", "draft", gid, ip=client_ip(req))
    threading.Thread(target=_convert_worker, args=([gid], u["id"]), daemon=True).start()
    return {"converting": [gid]}


# ---------------- student: tools ----------------

def tools_for(c, competition_id, practice):
    out = []
    for r in db.all_(c, "SELECT * FROM ai_tools WHERE status='active' ORDER BY id"):
        sc = db.jl(r["scope"], {}) or {}
        if (competition_id and competition_id in sc.get("competitions", [])) or (practice and sc.get("practice")):
            out.append(r)
    return out


@router.get("/api/student/tools")
def student_tools(competition_id: int | None = None, question_id: int | None = None, u=Depends(student), c=Depends(conn)):
    if not ai.enabled():
        return []
    if competition_id:
        arena_access(c, competition_id, u)
    rows = tools_for(c, competition_id, competition_id is None)
    out = []
    for r in rows:
        t = tool_row(r, with_prompt=False)
        used = db.one(c, "SELECT COUNT(*) AS n FROM ai_tool_uses WHERE tool_id=? AND user_id=? AND question_id IS ?", r["id"], u["id"], question_id)["n"]
        t["uses_left"] = max(0, t["spec"].get("max_uses_per_problem", 3) - used)
        out.append(t)
    return out


class RunToolIn(BaseModel):
    competition_id: int | None = None
    question_id: int
    code: str = Field(default="", max_length=65536)
    language: str = Field(default="c", max_length=20)
    inputs: dict = {}


@router.post("/api/student/tools/{tid}/run")
def run_tool(tid: int, body: RunToolIn, req: Request, u=Depends(student), c=Depends(conn)):
    rate_limit(f"tool:{u['id']}", 6, 60)
    if not ai.enabled():
        raise HTTPException(503, "The AI assistant is not available right now.")
    r = db.one(c, "SELECT * FROM ai_tools WHERE id=? AND status='active'", tid)
    if not r:
        raise HTTPException(404, "This tool is not available.")
    if body.competition_id:
        comp, p = arena_access(c, body.competition_id, u)
        if comp["status"] not in ("live", "paused") or p["status"] == "disqualified":
            raise HTTPException(409, "Tools are available only while the competition is running.")
        if not db.one(c, "SELECT 1 AS x FROM competition_questions WHERE competition_id=? AND question_id=?", body.competition_id, body.question_id):
            raise HTTPException(404, "Problem not found in this competition.")
    else:
        practice_question(c, body.question_id)
    if r not in tools_for(c, body.competition_id, body.competition_id is None):
        raise HTTPException(403, "This tool is switched off here.")
    spec = db.jl(r["spec"], {})
    used = db.one(c, "SELECT COUNT(*) AS n FROM ai_tool_uses WHERE tool_id=? AND user_id=? AND question_id=?", tid, u["id"], body.question_id)["n"]
    if used >= spec.get("max_uses_per_problem", 3):
        raise HTTPException(429, "You have used this tool the maximum number of times for this problem.")
    last = db.one(c, "SELECT * FROM submissions WHERE user_id=? AND question_id=? AND kind IN ('submit','practice') AND status='done' "
                     "AND competition_id IS ? ORDER BY id DESC LIMIT 1", u["id"], body.question_id, body.competition_id)
    system, user = build_prompt(c, spec, body.question_id, body.code, body.language, body.inputs, student_submission(c, last) if last else None)
    t0 = time.time()
    try:
        out = ai.chat_text(system, user)
    except ai.AIBusy as e:
        raise HTTPException(503, str(e))
    except Exception:
        raise HTTPException(502, "The AI assistant could not answer right now. Try again.")
    ms = int((time.time() - t0) * 1000)
    c.execute("INSERT INTO ai_tool_uses(tool_id,user_id,competition_id,question_id,input,output,ms,created_at) VALUES(?,?,?,?,?,?,?,?)",
              (tid, u["id"], body.competition_id, body.question_id, json.dumps(body.inputs)[:4000], out[:8000], ms, db.now()))
    if body.competition_id:
        c.execute("INSERT INTO activity_events(competition_id,user_id,question_id,type,detail,ip,created_at) VALUES(?,?,?,'ai_tool',?,?,?)",
                  (body.competition_id, u["id"], body.question_id, json.dumps({"tool": r["name"]}), client_ip(req), db.now()))
    return {"output": out, "uses_left": spec.get("max_uses_per_problem", 3) - used - 1}


# ---------------- student: adaptive insights ----------------

TIPS = {"WA": "Wrong answers usually hide in edge cases: n = 1, all equal values, negatives, the largest input. Test them with Custom input before submitting.",
        "TLE": "Time limit: count your loops. Nested loops over n = 10^5 are too slow; look for sorting, prefix sums or a hash map.",
        "RE": "Runtime errors: check array bounds, division by zero and reading input in exactly the stated format.",
        "CE": "Compilation errors: read the first error line the compiler shows; it points to the exact line.",
        "MLE": "Memory limit: avoid storing everything at once; process input as you read it."}


@router.get("/api/student/insights")
def insights(u=Depends(student), c=Depends(conn)):
    subs = db.all_(c, "SELECT s.question_id, s.verdict, q.category, q.difficulty FROM submissions s JOIN questions q ON q.id=s.question_id "
                      "WHERE s.user_id=? AND s.kind IN ('submit','practice') AND s.status='done'", u["id"])
    cats = {}
    for s in subs:
        k = cats.setdefault(s["category"], {"category": s["category"], "attempts": 0, "accepted": 0, "tried": set(), "solved": set()})
        k["attempts"] += 1
        k["tried"].add(s["question_id"])
        if s["verdict"] == "AC":
            k["accepted"] += 1
            k["solved"].add(s["question_id"])
    topics = sorted(({"category": k["category"], "attempts": k["attempts"], "tried": len(k["tried"]), "solved": len(k["solved"]),
                      "mastery": round(len(k["solved"]) / max(1, len(k["tried"])) * 100)} for k in cats.values()), key=lambda x: -x["mastery"])
    verdicts = {}
    for s in subs:
        if s["verdict"] not in ("AC", "IE", None):
            verdicts[s["verdict"]] = verdicts.get(s["verdict"], 0) + 1
    solved = {s["question_id"] for s in subs if s["verdict"] == "AC"}
    easy_done = sum(1 for s in subs if s["verdict"] == "AC" and s["difficulty"] == "Easy")
    level = "Easy" if len(solved) < 5 else ("Intermediate" if len(solved) < 15 or easy_done < 5 else "Challenging")
    weak = [t["category"] for t in sorted(topics, key=lambda x: x["mastery"]) if t["mastery"] < 70][:2]
    pool = db.all_(c, "SELECT q.id, q.category, q.difficulty, qv.title FROM questions q JOIN question_versions qv ON qv.id=q.latest_version_id "
                      "WHERE q.practice=1 AND q.status='approved' AND NOT EXISTS (SELECT 1 FROM competition_questions cq JOIN competitions co ON co.id=cq.competition_id "
                      "WHERE cq.question_id=q.id AND co.status IN ('draft','published','live','paused'))")
    pool = [q for q in pool if q["id"] not in solved]
    order = {"Easy": 0, "Intermediate": 1, "Challenging": 2}
    pool.sort(key=lambda q: (0 if q["category"] in weak else 1, abs(order[q["difficulty"]] - order[level]), q["title"]))
    recs = [{**q, "reason": f"Strengthens {q['category']}" if q["category"] in weak else f"Matches your level ({level})"} for q in pool[:4]]
    top = max(verdicts, key=verdicts.get) if verdicts else None
    return {"level": level, "topics": topics, "verdicts": verdicts, "weak": weak, "strong": [t["category"] for t in topics if t["mastery"] >= 80][:2],
            "recommendations": recs, "tip": TIPS.get(top) if top else "Start with an Easy practice problem: read the sample, run it, then submit.",
            "solved": len(solved)}
