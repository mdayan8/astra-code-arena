# ASTRA Code Arena on Ubuntu: the complete guide

This guide is also inside the app: open **ASTRA Code Arena** from the app menu and click **Guide & help**.

## 1. What you get

- **One background service.** It runs the student website and the judge, restarts them within 5 seconds if anything crashes, and starts by itself after every reboot, even before anyone logs in.
- **A control panel** at http://127.0.0.1:8765 that works only on the server PC. It has Run/Stop, links to share, machine check, address and port, opening hours, backups, Diagnose & fix, and logs.
- **The organiser site (admin)** on a secret link that you can open from any PC on the LAN. You run competitions, questions, students and results there.
- **No internet needed after installation.** Everything runs on the college LAN.

## 2. Machine you need

| Item | Minimum | Recommended (300-400 students) | Notes |
|---|---|---|---|
| CPU | 4 cores | 6 cores (e.g. Intel i5-12400) | One judge worker per real core; each worker serves about 100 students |
| RAM | 4 GB (no AI) | 8 GB (with AI) | Ubuntu desktop + web ~2 GB, AI model ~1.5 GB, ~400 MB per judge worker |
| Free disk | 20 GB | 40 GB or more | The installer refuses to install under 20 GB |
| OS | Ubuntu 22.04 | Ubuntu 22.04 or 24.04 | Desktop or Server |
| Network | Wired LAN | Gigabit Ethernet + fixed IP | See section 6 |

**Where the disk space goes**

| What | Size |
|---|---|
| Compilers, Java, Node.js, Python packages, app | about 1 GB |
| AI model (only with --with-ai) | about 2 GB |
| Database after one 400-student round | 50-200 MB (code, results, code replay, activity) |
| Database after a full semester | about 0.5 GB |
| 30 verified backups of that database | about 15 GB (the biggest item) |
| Logs and judge scratch files | under 100 MB (capped) |

**Example:** an i5-12400 with 8 GB RAM gets 6 judge workers, about 600 students at once with the AI on. A 4-core 4 GB PC gets 3 workers (about 300 students) without AI.

The control panel's **This machine** card measures the PC and says how many students it can handle at once.

## 3. Install (once, about 10 minutes)

1. Get the code onto the PC: download the ZIP from GitHub (Code → Download ZIP) and extract it, or `git clone` the repository.
2. Open the folder and start the setup app: **right-click `start.sh` → Run as a Program** (or in a terminal: `./start.sh`).
3. The setup window shows this PC's cores, RAM, free disk and how many students it can handle. Tick the AI model if you want Prompt challenges, then press **Install ASTRA Code Arena**.
4. Enter your Ubuntu password when asked. A progress bar shows every step: checking the computer, compilers, Node.js, copying the app, Python packages, (AI model), background service, keep awake, done.
5. At the end it shows the **first admin password** and the student link, plus an **Open control panel** button. Write the password down: you must change it at first sign-in.

Run `start.sh` again any time. If everything is installed and current it opens the control panel. If the folder has a newer version it offers **Update** (all data is kept). If something broke it offers **Repair / reinstall**.

Without a desktop (Ubuntu Server), run `sudo ./ubuntu/install.sh` (add `--with-ai` for the AI model) instead.

The installer:
- installs C, C++, Java, JavaScript and Python;
- copies the app to `/opt/astra-arena` and keeps data in `/var/lib/astra-arena`;
- creates the `astra-arena` service and the app-menu icon;
- opens port 8000 in the firewall;
- stops the PC from sleeping (a sleeping PC disconnects every student).

**To update later:** `git pull` (or download the new ZIP) and run `start.sh` again, then press **Update**. Your data, students and results are kept.

## 4. Navigating the control panel

Open **ASTRA Code Arena** from the app menu (purple hexagon icon). It opens in its own window when Chrome or Chromium is installed, otherwise in a browser tab. **Sign in with the Super Admin account**: the same username and password as the organiser site (the first password is shown at the end of the installation). Five wrong passwords lock sign-in for a minute. Forgot it? Run `astra-arena reset-password` in a terminal on that PC.

| Card | What it does |
|---|---|
| Top bar | **Running** (green), **Starting / recovering** (amber) or **Stopped** (grey) |
| Run / Stop / Restart | Start or stop the platform. If you press Stop, it stays stopped after a reboot until you press Run again |
| Links to share | **Students** link: share with everyone. **Organisers (private)** link: give only to staff. First admin password until you click "I saved it" |
| This machine | Cores, RAM, free disk, live CPU and memory use, and how many students it comfortably serves |
| Network | Address (All networks is recommended), Port (default 8000), Judge workers (empty = auto) |
| Opening hours | Days and times students can use the site. Outside them students see a "closed, opens at..." page. It never closes during a live round, and admins always have access |
| Keep it running | Start at boot, an extra backup folder (e.g. a USB drive), Back up now, Stop and close |
| Updates | Current version, Check now / Update now, automatic updates on or off, access token for a private repository |
| Move to another computer | Create a move package, import one, or remove this computer as the host (section 8) |
| Diagnose & fix | Runs 14 health checks. Each problem shows the fix, often as a one-click button |
| Logs | The last lines from the web server and the judge |

From a terminal you can do the same with `astra-arena open | status | start | stop | restart | logs | backup | doctor`.

## 5. Running competitions (organiser site)

Open the **Organisers** link from the control panel and sign in. The menu:

| Menu | Use it to |
|---|---|
| Dashboard | Live status, alerts (judge down, low disk, high CPU/RAM, backup problems), active rounds |
| Competitions | Create rounds, set eligibility, publish, start/pause/extend/end, publish results, copy the student link |
| Question Bank | Write questions, reference solutions, starter code; validate and approve |
| Practice | Choose which questions students can practise any time |
| Question Import | Import from PDF, Word, PowerPoint, images, JSON, CSV, Excel or ZIP |
| AI Studio | Describe a question in one box: AI writes, tests and validates it (needs the AI model) |
| Test Cases | Sample and hidden tests per question; generate expected outputs from the reference |
| Participants | Students, import from CSV, reset passwords, photos |
| Submissions | Every submission, re-judge |
| Leaderboards / Semesters / Reports | Results by round, by semester, CSV exports |
| Admin Users / Audit Logs | Staff accounts with roles; every admin action is recorded |
| Database | Browse tables, export CSV, back up, restore, upload a backup |
| System Settings | Departments, opening hours, registration rules, submission cooldown |

### Different challenges for different years

1. **Competitions → New**. Under **Eligibility**, tick the years (1st, 2nd, 3rd, 4th) and departments. Only those students can see and register for the round.
2. Under **Languages**, tick what is allowed. For a prompt round, tick **Prompt (AI challenge)**.
3. Add questions, set the start time and **Publish**.
4. Open the competition and use **Student link → Copy**. Share it on WhatsApp or the classroom board. Eligible students who open it land on that round.

Create one competition per year group (for example "1st year: Basics", "3rd year: DSA"). They can all run at the same time.

### Challenge types (Question Bank → Question type)

| Type | Students... | Good for |
|---|---|---|
| Standard | write a full program from scratch | DSA, problem solving |
| Fix the bug | get code with a mistake and must fix it | Debugging rounds, code reviews, "codebase" challenges |
| Complete the code | fill in the missing part of given code | 1st year, timed warm-ups |
| Prompt challenge | write an AI prompt (not code); the AI's reply to each hidden input must match exactly | Prompt engineering, new-age AI rounds |

For **Fix the bug** and **Complete the code**, paste the starter code per language in the question editor. For a **Prompt challenge**, the reference solution is a prompt that you know works; validate it like any other question.

### Exam lockdown (per competition)

Competitions → edit → **Exam lockdown**. Each round can have different rules:

| Setting | Choices |
|---|---|
| Internet | **Detect and report** (default) · **Block**: the test is hidden while the PC can reach the internet · **Allowed** for online, research or prompt rounds |
| Full screen | **Required**: the test is hidden outside full screen, and leaving full screen is a warning · Not required |
| Leaving the test (other tab or app) | **Hide the test and give a warning** · Allowed, only recorded |
| Auto-submit after warnings | 0 = never; e.g. 3 = the test is submitted automatically at the 3rd warning |
| Copy / paste | Paste policy (allow, record, block outside pastes), plus **Block copying** and **Block all pasting** |

Students see these rules on the round page before they start. Warnings are counted on the server. If a student is auto-submitted by mistake, open **Participants → their activity → Reopen test**: their screen opens again and the warning count starts from zero.

A web page cannot turn off a PC's internet. **Block** hides the test whenever the PC can reach the internet, so it only works when the lab network has no internet during the round (ask IT for a firewall rule for the exam). Run a round on **Detect** first: the Fair-play tab shows how many PCs could reach the internet.

### Results

Under **Results visibility**, choose live leaderboard, show at the end, or hidden until you publish. After the round, check the submissions, then **Publish results**. Students then see celebrations, badges and their rank.

## 6. Networking

**Students and the server must be on the same campus network** (LAN or Wi-Fi that can reach the server).

1. **The fixed address is automatic.** At install, at every start and every 5 minutes, the app:
   - finds this PC's network;
   - picks a free address near the top of it (e.g. `10.202.65.251`), checking that no other device answers to it;
   - adds it to the network card as an extra address (the normal address from the network keeps working);
   - checks that the network really passes traffic from it.

   Then the student link always uses this address, so it stays the same after reboots or a new address from the network. If the PC moves to a different network, it picks a new address there by itself. You see it under **Links → Address**; it can be switched off under **Network**.
   - **If the campus network blocks self-chosen addresses** (some managed switches do), the app notices, undoes it and uses the normal address. Diagnose & fix then says so. Ask IT to reserve the PC's address (a DHCP reservation for its MAC address).
2. **Check the address.** The control panel shows it under Links. From any student PC, open `http://<server-ip>:8000/`.
3. **Firewalls:**
   - On the server, the installer already opened port 8000. After changing the port, press **Open port in firewall**.
   - On the campus firewall (e.g. Sophos), allow LAN clients to reach `<server-ip>:8000`. No internet rule is needed.
4. **Lab PCs on Wi-Fi with "client isolation"** cannot reach other devices. Ask IT to allow the server's IP, or use wired lab PCs.
5. **Student code never gets network access:** each program runs as its own user in an empty network sandbox, with CPU, memory, time and output limits.

## 7. Data safety: what happens when things go wrong

| Event | What the platform does |
|---|---|
| Power cut or crash mid-round | Every saved answer is already on disk (the database writes durably). After power returns the service starts by itself and students continue. Submissions that were being judged are judged again |
| Database damaged | At start-up it is detected, set aside as `astra.db.damaged-<time>`, and the newest good backup is restored automatically. The dashboard shows an alert |
| Backups | Every hour during a live round, every 6 hours otherwise, 30 kept, each one verified. Optional second copy in the extra backup folder |
| Network drops | Students' code stays in their browser; they reconnect and continue |
| Disk filling up | Alert under 2 GB free; logs rotate at 5 MB |

**Manual restore:** Organiser site → **Database** → choose a backup → **Restore** (type RESTORE). A safety backup is taken first.

## 8. Automatic updates

When a new version is pushed to GitHub, every installed PC picks it up by itself:

1. It checks GitHub every 30 minutes (and when you press **Check now**).
2. It **never updates during a round, or in the hour before one starts**.
3. It takes a verified backup of the database, downloads the new version, and installs it. Packages are reinstalled only if the list changed, and the database is upgraded automatically: students, questions and results are kept.
4. It restarts (a few seconds) and checks that the new version is healthy. **If it does not start, the previous version is restored automatically** and that version is not tried again until a newer one is pushed.

The Updates card shows the current version, the last check, and the result of the last update. Switch automatic updates off there to update only by hand (**Update now**, or `astra-arena update` in a terminal).

**Private repository:** the PC needs read access. In GitHub, create a fine-grained token with read-only "Contents" access to this repository, and paste it under Updates → Access token. A public repository needs nothing.

## 9. Moving to another computer, or removing it

Everything moves in one file, and the new computer adapts by itself: it sizes the judge to its own CPU and RAM, and picks its own fixed address.

1. **Old computer:** Control panel → **Move to another computer** → **Create move package** → **Download**. Save it to a USB drive. It holds every competition, question, student, result, photo and setting.
2. **New computer:** get the code (ZIP or `git clone`), right-click `start.sh` → Run as a Program. Under **Moving from another computer** choose the package, then press **Install**. Sign in with the same admin accounts as before.
3. **Old computer:** **Remove from this computer**, then type REMOVE. This:
   - stops the platform;
   - frees its fixed address;
   - closes the firewall port;
   - lets the PC sleep again;
   - removes the app and the menu icon.

   By default the data stays in `/var/lib/astra-arena`. Tick **Also delete all data** to wipe it; a final package is still kept in `/var/backups`.

If both computers are on the same network, the new one takes over the old fixed address once the old one is removed, so **the student link stays the same**. Otherwise share the new link from the control panel.

The same from a terminal: `astra-arena package` (create a move package) and `astra-arena remove` (uninstall). On an installed PC, the panel's **Import** button also brings in a package.

## 10. Troubleshooting

Run **Diagnose & fix** in the control panel (or `astra-arena doctor`) first. It finds most problems and shows the fix.

| Problem | Likely cause | Fix |
|---|---|---|
| Student link changed | Fixed address switched off or blocked by the network | Diagnose → Fixed address → **Set up**; if blocked, ask IT for a DHCP reservation |
| Students cannot open the link | Different network, firewall, wrong IP | Same LAN? Open `http://<ip>:8000/api/health` on the student PC. Press **Open port in firewall**. Check the IP in the control panel |
| "Code Arena is closed" page | Opening hours | Control panel → Opening hours: switch off or add a window |
| "Campus network only" page | ALLOWED_NETWORKS in `/etc/astra-arena.env` does not include that subnet | Add the subnet, then `astra-arena restart` |
| Control panel does not open | Service stopped | `astra-arena restart`, then `astra-arena status` |
| "Port already in use" | Another program on 8000 | Choose another port under Network (e.g. 8080) and share the new link |
| Submissions stuck in "Queued" | Judge stopped | Diagnose → Restart. Check Logs → judge |
| "Compilation Error" for every C++ answer | Compiler missing | Diagnose → Programming languages → **Install** |
| Prompt challenges show "Internal error" | AI model not running | `sudo systemctl restart ollama`; check that AI_* is set in `/etc/astra-arena.env` |
| Slow verdicts with many students | Not enough judge workers | Network → Judge workers +1 (watch Memory), or close other programs |
| Update failed / "Cannot reach GitHub" | No internet, or a private repository without a token | Connect to the internet, or add the token under Updates |
| Forgot the admin password | | `astra-arena reset-password` (asks you to type a new password) |
| Lost the organiser link | | The control panel shows it under Links |
| A student forgot their password | | Students use **Forgot password** (USN + name + date of birth), or Participants → reset |
| Wrong times on competitions | Clock or timezone | Settings → Date & Time → automatic; APP_TIMEZONE in `/etc/astra-arena.env` |
| Need to move to a new PC | | Section 8: create a move package, install the new PC with it, remove the old one |

## 11. Files and commands

| What | Where |
|---|---|
| App | `/opt/astra-arena` |
| Data (database, uploads, backups, logs) | `/var/lib/astra-arena` |
| Settings | `/etc/astra-arena.env` (then `astra-arena restart`) |
| Service | `sudo systemctl status astra-arena` |
| Logs | `astra-arena logs` |
| Uninstall (data kept) | `sudo ./ubuntu/install.sh --uninstall` |
