# Deployment on the college server

> **Recommended: Ubuntu one-command install with a control panel.** See [UBUNTU.md](UBUNTU.md) (`sudo ./ubuntu/install.sh`). The Docker route below still works.

## 1. What to ask the college IT team

Send them this list. Nothing here is hard-coded: the IP, port and route are all configuration.

1. **Any spare Linux machine** (an old lab PC is fine; Ubuntu 22.04/24.04 is easiest). Measured: the platform itself uses about
   **150 MB of RAM**; the load test with 300 simultaneous students and 4 judge processes judged every submission in under 3 s.
   - Up to ~300 students: **4 CPU cores, 4 GB RAM, 15 GB free disk** is comfortable. 2 cores / 2 GB works for ~100.
   - With the optional AI assistant: add ~2 GB RAM (the model only loads while an organiser uses it).
2. **A static or reserved LAN IP** for that server.
3. **One approved TCP port** (for example 8000 or 80) open inbound on the server firewall.
4. **Reachability from the lab VLAN(s) and the Wi-Fi network(s)** students will use. Ask them specifically about:
   - VLAN separation: is there routing from the lab and Wi-Fi subnets to the server subnet?
   - Wireless client isolation: it is fine for client-to-client traffic to be blocked, as long as Wi-Fi clients can reach the *server IP*.
   - Firewalls between those subnets, and the captive portal (students must be signed in to Wi-Fi first).
   - If routing is not possible, a **dedicated event network** (a lab switch or event SSID on the server's subnet).
5. Optional: a local DNS name (e.g. `arena.pesitm.local`) pointing to the server IP.

Do not try to bypass the institution's network rules. If something is blocked, ask IT to allow it.

## 2. Install with one command (recommended)

Copy the `astra-code-arena` folder to the server (USB, `scp` or git), open a terminal in it and run:

```bash
./deploy.sh              # or: ./deploy.sh --with-ai
```

It asks **no questions**: it installs Docker if missing, uses port 8000 on all of the server's addresses, sizes the judge to
the CPU count, builds and starts everything, creates the first Super Admin and prints the student link, the organiser link and
the one-time admin password. Need a different IP or port? `./deploy.sh --reconfigure`.
Re-run it to update (it backs up first). Change IP or port later with `./deploy.sh --reconfigure`.

**Server without internet:** on any machine that has internet and Docker, run `./deploy.sh --bundle`. It produces
`astra-offline.tar.gz` (the app, all compilers and the AI runtime) and `ai-models.tgz` (the small AI model). Copy the whole
folder to the server (USB drive is fine) and run `./deploy.sh --offline`. Nothing is downloaded on the server. (Docker itself
must already be installed there; IT can install the `docker.io` and `docker-compose-v2` .deb packages offline.)
The manual steps below do the same thing.

### Manual Docker install

```bash
# on the server
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER && newgrp docker
# copy the astra-code-arena folder to the server (USB, scp or git), then:
cd astra-code-arena
cp .env.example .env
nano .env                    # set APP_PORT (the approved port) and JUDGE_WORKERS (≈ CPU cores - 2)
docker compose up -d --build
docker compose exec web python -m app.manage create-admin
docker compose exec web python -m app.manage info
```

- The first build needs internet (it downloads Python packages and compilers). **After that the platform runs fully offline.** To install on a server without internet, build on a machine that has it and copy the image: `docker save astra-arena:latest | gzip > astra.tar.gz`, then `docker load < astra.tar.gz` on the server.
- Want fewer/more languages? Edit `ARG EXTRA_LANGS` in the `Dockerfile` (`"java node"` by default; add `go rust`; C, C++ and Python are always installed). Each competition chooses which installed languages it allows.
- Data lives in the Docker volume `astra-data` (database, uploads, backups). It survives `docker compose down`, upgrades and reboots.
- Both containers restart automatically after a crash or reboot (`restart: unless-stopped`).

Check it works:

```bash
docker compose ps                     # web and judge "running"
curl http://localhost:$APP_PORT/api/health
docker compose logs -f judge          # shows "sandbox: isolated (dedicated uid + rlimits, no network)"
```

Then open **Admin → System Settings**: judge workers green, all languages green.

## 3. Install without Docker (plain Linux)

Works, but the judge must run as **root** on Linux to get the per-job sandbox user, and you lose the container's
no-network/read-only guarantees (a fresh network namespace is still created per run).

```bash
sudo apt install -y python3 python3-venv gcc g++ tesseract-ocr default-jdk-headless nodejs
sudo mkdir -p /opt/astra && sudo cp -r . /opt/astra && cd /opt/astra
sudo python3 -m venv .venv && sudo .venv/bin/pip install -r requirements.txt
sudo cp .env.example .env && sudo nano .env
sudo .venv/bin/python -m app.manage create-admin
```

`/etc/systemd/system/astra-web.service`:

```ini
[Unit]
Description=ASTRA Code Arena web
After=network.target
[Service]
WorkingDirectory=/opt/astra
ExecStart=/opt/astra/.venv/bin/python -m app.main
Restart=always
User=root
[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/astra-judge.service`: the same with `ExecStart=/opt/astra/.venv/bin/python -m app.worker`.
Then `sudo systemctl enable --now astra-web astra-judge`.

## 4. College LAN / Wi-Fi checklist (do this a few days before the event)

| Check | How | Expected |
|---|---|---|
| Server reachable from a **wired lab PC** | open `http://SERVER-IP:PORT/` | landing page loads |
| Server reachable from a **student phone/laptop on Wi-Fi** | same URL after Wi-Fi sign-in | landing page loads |
| Port open | from a lab PC: `curl http://SERVER-IP:PORT/api/health` | `{"ok":true}` |
| Admin route only works with credentials | open the private route on a lab PC | login screen; dashboard only after sign-in |
| Works **without internet** | ask IT to block the server's internet for 10 minutes, then run a test round | everything works except the AI assistant |
| Editor loads offline | open a practice problem on a lab PC | Monaco editor appears with syntax colours |
| Judge throughput | run `python3 tests/load_test.py 300 120` on the server | `RESULT: PASS` |

If a Wi-Fi device cannot reach the server but a lab PC can, the cause is almost always VLAN routing, client isolation
or a firewall rule. Give IT the server IP + port and ask for a rule allowing the Wi-Fi subnet to reach it.

## 4b. Automatic updates from GitHub

The server **pulls** from GitHub (GitHub cannot reach a server inside the college LAN, so nothing is pushed to it).

1. Put this folder in a GitHub repository (private is fine).
2. On the server, clone it instead of copying: `git clone https://github.com/<you>/astra-code-arena.git && cd astra-code-arena`.
   For a private repo, create a read-only **deploy key** (GitHub → repo → Settings → Deploy keys) and clone over SSH.
3. Run `./deploy.sh` once by hand (it asks for IP and port and creates the Super Admin).
4. Run `./deploy.sh --enable-autoupdate`.

From then on, every 5 minutes `scripts/auto-update.sh` checks the `main` branch. When you push a change it backs up the database,
fast-forwards to the new code and rebuilds, usually within 5–10 minutes. It **waits while any competition is live or paused**,
never overwrites local edits on the server, and logs to `logs/auto-update.log`. Database changes ship as numbered migrations
and apply automatically. The server needs outbound access to github.com for this (ask IT); without it, update with a USB copy + `./deploy.sh`.

## 5. Contest network: campus only, no internet

Goal: during a round, student devices can reach **only** the Code Arena server, not the internet. Three layers:

**a) Firewall (the real block). Ask IT to do this on the Sophos firewall.** The same Sophos that shows the "Sign in to access this network" page can give a contest network or user group that reaches only the server:
1. Create a host object for the Code Arena server (its LAN IP) and a service object for its port (`APP_PORT`, TCP).
2. Use a dedicated **contest SSID / VLAN** for the lab, or a **contest user group** that students sign in with on the captive portal.
3. Add a firewall rule at the top: source = contest network or group, destination = Code Arena server, service = APP_PORT, action **Allow**.
4. Add the next rule: source = contest network or group, destination = Any (WAN / Internet), action **Drop**. Also block DNS to outside resolvers. Optionally give a local DNS name (e.g. `arena.pesitm.local`) for the server.
5. Exempt the captive-portal sign-in from this rule, or let the contest SSID skip the portal, so students can still connect.
6. Switch the rule on for the contest window and off afterwards (Sophos supports a schedule on the rule).

**b) Platform lock (only campus devices can open it).** In `.env` set `ALLOWED_NETWORKS` to the lab and Wi-Fi subnets, e.g.
`ALLOWED_NETWORKS=10.20.0.0/16,172.16.40.0/22`, then `docker compose up -d`. Anyone else gets a "campus network only" page.
Check that the IPs shown under **Participants → Last IP** are the real student IPs (not a single gateway address) before turning this on.

**c) Detection in the arena.** While a round is live, every student's browser quietly checks every 45 s whether the public
internet is reachable. If it is, the student sees an "Internet detected" badge and organisers see it immediately in
**Live monitor → Fair-play flags** and in the **Integrity** report (an *Internet* column, and it raises the risk score).
Pastes, copies, tab switches, full-screen exits, AI-tool use and code snapshots are reported the same way.

## 6. HTTPS (optional)

Over plain `http://` on a closed LAN the platform works as is (`COOKIE_SECURE=false`). If IT provides a certificate,
put a reverse proxy in front (Caddy or nginx), proxy to the web container, and set `COOKIE_SECURE=true` in `.env`.

## 7. Updating

```bash
docker compose exec web python -m app.manage backup   # always back up first
# copy the new code over the old folder (keep your .env)
docker compose up -d --build
```

Schema changes ship as numbered entries in `MIGRATIONS` in `app/db.py` and run automatically on start.

## 8. Optional: local AI model

`./deploy.sh --with-ai` runs [Ollama](https://ollama.com) as a private container next to the platform (never exposed on the network)
and downloads **qwen2.5-coder:1.5b** (~1 GB download, ~1.5 GB RAM only while generating; unloaded after 5 idle minutes).

Small models make mistakes, so the platform wraps them in a **harness**: the model writes the problem plus C and Python
solutions, the sandbox runs both on every test, and any compile error or disagreement is sent back to the model with the
exact failing input and outputs, up to 3 repair rounds. Only a question whose references agree reaches "validated", and a human
still approves it. If the server has RAM to spare, a bigger coder model gives better questions with fewer repairs:
change `AI_MODEL` in `.env` (e.g. `qwen2.5-coder:3b` ≈ 2.5 GB or `qwen2.5-coder:7b` ≈ 5 GB), run
`docker compose exec ollama ollama pull <model>`, then `docker compose up -d`.
Any OpenAI-compatible endpoint also works (`AI_BASE_URL`, `AI_MODEL`, `AI_API_KEY`). The AI is optional: everything else works without it.
`AI_CONCURRENCY` (default 2) caps simultaneous AI calls so a small server never runs out of RAM or CPU; extra requests wait or get a polite "busy, try again".
Note: if the server itself has no internet, the AI still works because the model runs locally.
