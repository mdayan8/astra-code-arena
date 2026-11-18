# Organiser guide

## The flow of a round

1. **Questions**: make sure each question is **approved** and **validated** (green tick in the Question Bank).
2. **Competitions → New competition**: a 5-step wizard (basics, schedule & registration, languages & scoring, instructions, review). It saves as a **Draft**, invisible to students.
3. **Questions tab**: add questions from the bank, set marks, reorder, then **Save question set**.
4. **Preview**, then **Publish**. Publishing pins the exact question versions, so later edits never change this round.
5. Students register and join while registration is open (or you assign them: **Participants → Assign** or **Import CSV**).
6. At start time the round goes live automatically (if *auto-start* is on) or press **Start now**.
7. During the round use **Live monitor** (online count, queue, submission feed, fair-play flags). **Pause**, **Resume** and **Extend time** apply to everyone at once and are announced in the workspace.
8. **End competition** (or it ends by itself when time runs out), then **Finalise results**. Finalising freezes the leaderboard as the official record.
9. **Reports**: download participant list, final rankings, question-wise performance, submission statistics and the fair-play report as CSV.
10. Optionally add the round to the semester (**Semesters → Add**, with its point contribution).

Every action is recorded in the audit log with who did it and when.

## Choosing languages

Each competition picks its own languages from those installed on the judge (C, C++, Python, Java, JavaScript; Go and Rust if added).
Questions can further restrict languages. Students only see the intersection. Python, Java and JavaScript get extra time
automatically (×2.5, ×2, ×2) because they are slower than C/C++.

## Scoring options

| Option | Effect |
|---|---|
| Partial marks | Marks × (weight of hidden + sample tests passed ÷ total weight). Test weights are set per test case. |
| All-or-nothing | Full marks only when every test passes. |
| Negative marks | Deducted per wrong submission on a problem (never below 0 for that problem). Compilation errors and judge errors never count. |
| First-correct bonus | Extra marks for the first participant to fully solve each problem. |
| Tie-break: penalty | Minutes from start to each scoring submission + N minutes per earlier wrong attempt; lower wins. |
| Tie-break: time | Earliest time to reach the final score wins. |

Only the best submission per problem counts, so repeated accepted submissions never add points. Scores are recomputed
from stored submissions, so they are always reproducible; re-judging updates them.

## Questions

- **Create** manually (Question Bank → New question), from a **PDF**, or with the **AI assistant**.
- **Test cases**: mark examples as *sample* (shown to students) and the rest *hidden* (never shown). Type: normal, boundary, edge, stress. Weight affects partial marks.
- **Bulk import**: paste blocks of `### input` / `### output`. Leave outputs empty and let the reference fill them.
- **Reference & validation**: write a reference solution (ideally both C and Python), then **Validate now**. The judge runs every reference on every test in the sandbox, fills empty expected outputs from the first reference and requires all other references to reproduce them exactly. A question can be approved and published only after this passes.
- **Try on all tests** runs any code (e.g. a deliberately wrong solution) to check your tests catch it.
- **Versioning**: once a question is used by a published competition or has submissions, editing it creates a new version automatically. Old rounds keep the version they used.
- Tick **Show in student Practice** to publish a question for practice when it is not part of an upcoming or live round.

## Question import (PDF, Word, text, photos)

Supported files (max 20 MB):
- **Documents:** PDF (scans via OCR), Word `.docx`, OpenDocument `.odt`, PowerPoint `.pptx`, RTF, HTML, Markdown/text and code files.
- **Question sets:** JSON, CSV/TSV and Excel `.xlsx`. Each record becomes a complete draft with samples, hidden tests (with or without outputs),
  reference solutions, question type and starter code. Download the JSON/CSV templates on the import page.
- **Images:** PNG, JPG, WEBP, TIFF, BMP, GIF (OCR).
- **ZIP archives** of any of the above (up to 60 files). Unsupported files inside are skipped.

For free-text documents, download the paper template on the import page to write papers that import perfectly. The system extracts the text (OCR for scanned pages and photos), splits it at headings such as
*Question 1*, *Problem 2:* or *Q3.*, and detects *Input Format*, *Output Format*, *Constraints*, *Sample Input/Output* and
*Explanation*. The document page shows the original PDF, the extracted text with the detected question boundaries,
and warnings (missing sections, tables, maths symbols, poor OCR). Open each draft, fix it, add a reference solution
and hidden test inputs, then **Save to question bank**. Nothing is published automatically.

## Generating test cases without AI

In a question's **Test cases** tab, **Generate with script** opens a small Python generator (templates for arrays, numbers,
strings and grids). The platform runs it in the sandbox once per seed (1…N); each printed result becomes a hidden test input,
duplicates are skipped, and the reference solution computes the outputs, cross-checked by the other references.

## AI assistant

Works only if a model is configured (Deployment §7). **Generate** creates a full question: the model writes the statement,
samples, hidden test inputs and C + Python solutions; the harness runs them in the sandbox and sends failures back to the model
to repair (up to 3 rounds). The question appears in the bank as an unapproved draft with a harness log. Open it, read it, then approve.
**Variation** rewrites an existing question with a new scenario. **AI suggest tests** (Test cases tab) adds inputs whose outputs are
computed by your reference. AI output is never trusted for expected outputs, and a human approves every question.

## Publishing results

Each competition's **Results** setting decides when students see scores and ranks:
- **Live during the contest**: leaderboard visible while the round runs.
- **Automatically when it ends**: visible as soon as the timer ends.
- **Manually: I publish results**: hidden (also in the student's finish summary) until you press **Publish results** on the
  competition overview or Leaderboard tab, after checking submissions and integrity. Publishing posts an announcement, opens the
  leaderboard, adds the round to semester points and shows every participant a "Results are out, you ranked #N" celebration.
  **Hide results** withdraws them again.

## Practice

**Practice** in the sidebar lists every approved question with how many students tried and solved it. **Add** puts it in the
student Practice section, **Remove** takes it out. Questions used by an upcoming or live competition are hidden from practice
automatically until that round is over.

## Question types

In the question editor choose **Standard** (write the whole program), **Fix the bug** (students start from your buggy program) or
**Complete the code** (students fill in a marked gap). Add starter code per language; students see it in the editor, and **Reset**
returns to it. Judging is the same as for any question (hidden tests, reference-validated).

## Database page (Super Admin)

**Database** in the sidebar shows size, health and row counts, and lets you browse every table read-only (passwords and session
secrets are never shown) and export any table as CSV. Maintenance buttons: integrity check, write-log checkpoint, optimise,
compact (not during a live round), clear expired sign-ins and clean up old code snapshots of finished rounds. Backups: back up now,
download, upload a backup from another machine, and **Restore** (type RESTORE; a safety backup of the current data is taken first,
and restore is refused while a round is live).

## AI Studio: Tools Lab

**One box:** type what you want ("after a wrong answer, explain which edge case was missed, no code"), choose Practice and/or a
competition, press **Create & deploy**. The agent designs the tool and it goes live for students immediately.


**AI Studio → Tools Lab** lets organisers build AI tools without writing code:
- **Describe a tool** ("a debugging coach that asks two guiding questions") and the agent designs it: name, icon, button,
  instructions for the model, form fields, what context it may see (problem, the student's code, their last verdict on samples)
  and a per-problem use limit. Or add a ready-made one: **Hint**, **Explain my error**, **Code review**, **Concept explainer**.
- Edit anything, **test** it with sample code, then choose **where it is switched on**: practice and/or specific competitions,
  and set it to **Active**. Students then get an **Assistant** tab in the workspace with those tools.
- By default tools give hints, never full solutions (you can allow solutions per tool, e.g. after a round). Every use is
  logged (**Usage**) and shows in the competition's Integrity report.
- Tools are a safe form specification run by the server; the AI never injects code into student pages.

**Build questions with AI from any document:** on an imported document's page, **Build questions with AI** sends each detected
question (or the whole text if none were detected) through the same self-checking harness: a full statement, C + Python
solutions and test cases, validated in the sandbox. The results appear in the Question Bank as drafts to approve.

## Insights for students
Students get an **Insights** card (dashboard and profile): topic mastery, their level (Easy → Intermediate → Challenging,
adapting as they solve more), a tip based on their most common mistake, and recommended practice problems that target weak topics.
Problems without examples automatically open the **Custom input** tab so students can test with their own input.

## Departments and students

Admins maintain the department list in **System Settings**; students choose from it when registering, and competition eligibility uses it.
Students sign in with USN + date of birth (DDMMYYYY). **Participants → Reset password** sets it back to the date of birth.
CSV import columns: `name,usn,dob,department,year,semester,section,email` (dob as YYYY-MM-DD or DD-MM-YYYY).

## Fair play

Choose a paste policy per competition: **allow**, **log** (default) or **block external pastes**.
The workspace records pastes (with the first 500 characters of the pasted text), copies, tab switches and time away,
window focus loss, keystroke counts, language changes, code resets and a code snapshot every 30 seconds.
The **Integrity** tab ranks participants by a risk score; open anyone to see the full timeline, a **code replay** slider
and every submission. **Check code similarity** compares final submissions per question (comments and whitespace ignored).
These are signals for a human decision, not proof. **Disqualify** removes someone from the leaderboard and blocks further submissions.
Students are told on the competition page and in the workspace that activity is recorded.

## Duplicating

**Duplicate** copies a competition's settings and question set into a new draft with a new code. Participants, submissions and scores are never copied.

## Semesters

Super Admins create a semester, add finished rounds with their point contribution (e.g. Round 1 = 100, Round 3 = 150),
then **Freeze** (snapshot, still editable after unfreezing) or **Finalise** (permanent record). A student's semester points =
Σ (their round score ÷ round maximum × round points). Only rounds you add count.

## Event-day checklist

- [ ] Admin → System Settings: judge workers green, all needed languages green, last backup recent.
- [ ] Each question validated; competition previewed and published; start time and duration right.
- [ ] A volunteer joins from a lab PC **and** from Wi-Fi, runs the sample, submits a wrong and a right answer.
- [ ] Organisers have the private admin route and their passwords.
- [ ] After the round: End → Finalise → download the reports → Admin → System Settings → **Back up database now**.
