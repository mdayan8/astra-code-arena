# ASTRA Code Arena: student guide

**Address:** `http://<given by organisers>/` (on the college LAN or approved Wi-Fi)

## Account
- **Register** with your full name (as on your college ID), USN, date of birth, department, year, semester, and any email (college or personal).
- **Sign in** with your **USN** and your **date of birth as DDMMYYYY** (15 Aug 2005 → `15082005`). You can set a different password in your profile.
- **Forgot password?** Click it on the sign-in page and enter your USN, full name and date of birth. Your password goes back to your date of birth.
- Update your current semester, section and email from **Profile → Edit profile**.

## Home (your profile)
After signing in you land on your profile. A live round you joined shows as a green banner with a countdown and **Enter arena**;
after you finish it says **Assessment submitted** until results are published. Click your picture to upload a profile photo
(PNG/JPG/WEBP, under 700 KB). **My problems** lists everything you attempted or solved, with your code.

## Profile, streaks and badges
Your profile shows your semester rank, points and percentile, a streak heatmap of the days you code, problems solved by difficulty,
and 12 badges (First Blood, Problem Solver, Code Machine, Half Century, Arena Debut, Veteran, Podium, Champion, Perfect Round,
Polyglot, On Fire, Speedster). Hover a badge to see how to earn it.

## Joining a round
Open **Competitions**, pick a round and press **Join competition** while registration is open. Some rounds are by invitation only.
Problems become visible only when the round goes **Live**. Press **Enter arena**.

## The coding workspace
- Competitions open in **full screen**. Leaving full screen or switching tabs is recorded; use **Return to full screen** if you leave by accident.
- Left: the problem, input/output format, constraints and examples. Right: the editor. Bottom: results.
- Choose your language at the top of the editor. Your code is saved in the browser automatically, per problem and language.
- **Run** (Ctrl/Cmd + Enter) checks your code on the example cases. Use the **Custom input** tab to try your own input. Runs never affect your score.
- **Submit** (Ctrl/Cmd + Shift + Enter) judges your code on hidden test cases. Only your best submission per problem counts.
- After an **Accepted** submission the arena moves to the next unsolved problem automatically (or press **Go now** / **Skip to**).
- **Finish test** (top bar) is your final submission: your best answer for every problem is kept and you can't submit again. When you have solved everything, the arena offers it to you.
- **Submissions** shows your history; click one to see it or load it back into the editor.
- Change font size and light/dark theme from the editor toolbar. ⛶ enters full-screen focus mode.

## Important
- Read from **standard input** and print to **standard output**. Print only what the output format asks for, with no extra prompts like "Enter n".
- Python: use `input()` or `sys.stdin`. C: `scanf`/`printf`. Java: the class must be called `Main`.
- The timer at the top is the official one. When the organisers pause the round, the timer freezes for everyone.

## Verdicts
| Verdict | Meaning |
|---|---|
| Accepted | All tests passed |
| Wrong Answer | Your output differs on at least one test |
| Compilation Error | Fix the errors shown |
| Runtime Error | Crash, exception or non-zero exit |
| Time Limit Exceeded | Too slow, or an infinite loop |
| Memory Limit Exceeded | Too much memory |
| Internal Judge Error | Our problem, not yours. It never counts against you |

## Fair play
Work alone. The workspace records pastes, copying and switching tabs, and organisers review similar submissions. Some rounds block pasting from outside the editor.

## Leaderboards
Each round has a leaderboard (live or revealed at the end, as the organisers choose). The **semester leaderboard** adds up your points across rounds.
