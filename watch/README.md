# ASTRA remote watch

A small, separate helper that watches the ASTRA Code Arena server and sends phone alerts
(via [ntfy](https://ntfy.sh)) plus a tiny status page. It lives outside the platform code,
uses only Python's standard library, and **never writes to the platform**: it opens the
database read-only (`?mode=ro`) and pings `http://127.0.0.1:<port>/api/public/info`.

## What it alerts on (checked every 30 s)

| Alert | When |
|---|---|
| Platform errors | new rows in `error_log` (grouped by source; web/worker/database = urgent) |
| Judge internal error | a submission gets verdict `IE` |
| Judge backlog | a submission queued > 60 s, or more than 50 queued (and when it clears) |
| Web server down | `/api/public/info` fails twice in a row (urgent), and when it is back |
| Service not active | `systemctl is-active astra-arena` is not `active` (urgent) |
| Round started / paused / ended | competition status changes, with participant and submission counts |
| Machine | disk free < 2 GB, RAM > 90%, load > 2 x cores |
| Summary | every 15 min while a round is live, else every 3 h |

At most one alert per kind every 5 minutes; extra ones are counted and folded into the next one.
If the internet is down, alerts wait and are retried on the next check.

## Privacy

No student names, USNs or emails leave the machine. Alerts and the page show only counts,
competition titles/codes, verdict counts and error messages. Error messages are cut to
600 characters and anything that looks like a USN (`4PM23CS001`) or an email is replaced
with `[usn]` / `[email]`.

The ntfy topic name is the only secret: anyone who knows it can read the alerts (and the
dashboard link sent through it). It is a long random string; do not share it.

## Install (on the Ubuntu server)

```bash
sudo watch/install.sh              # service + dashboard + Cloudflare tunnel (if cloudflared is installed)
sudo watch/install.sh --no-tunnel  # dashboard reachable only on the server itself
```

This installs one file outside this folder, `/etc/systemd/system/astra-watch.service`
(runs as root because the platform DB is root-only; `Restart=always`). It prints the
ntfy subscribe link and the dashboard URL.

Files it creates at run time:
- `/etc/astra-watch.json` (mode 600): ntfy server, topic, dashboard key, interval, DB path. Edit and
  `sudo systemctl restart astra-watch` to change. Set `ntfy_url` for a self-hosted ntfy.
- `/var/lib/astra-watch/state.json`: last seen ids (so restarts do not re-send old alerts).

When run as a normal user (not root) both files live next to the script instead
(`watch/config.json`, `watch/state.json`).

Show the links again any time: `sudo python3 watch/watch.py --info`

## Subscribe on your phone

- **App:** install "ntfy" (Android / iOS), tap **+**, enter the topic name, Subscribe.
- **Browser:** open the `https://ntfy.sh/<topic>` link and allow notifications.

Test it: `sudo python3 watch/watch.py --test-alert`

## Dashboard from anywhere

The page runs on `http://127.0.0.1:8790/?k=<key>` (wrong or missing key = 403).
It refreshes every 15 s and shows live rounds, participants, submissions per minute
(last hour), verdicts, warnings, the judge queue, disk/RAM/load and the last 20 errors.

With `cloudflared` installed, the service opens a Cloudflare quick tunnel and sends the
`https://....trycloudflare.com/?k=...` link to your ntfy topic (again whenever the tunnel
restarts, because the address changes). Install cloudflared on Ubuntu:

```bash
curl -L -o /tmp/cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i /tmp/cloudflared.deb
sudo systemctl restart astra-watch
```

## Stop / remove

```bash
sudo systemctl stop astra-watch        # stop for now
sudo watch/install.sh --uninstall      # remove the service (config kept in /etc/astra-watch.json)
```

## Self-test (no network, no real alerts)

```bash
python3 watch/watch.py --selftest
```

Builds a temporary database, runs the checks with a fake sender and checks: new error,
IE, stuck queue, round start, summary, 5-minute collapsing, USN/email scrubbing, server
down/up after 2 failures, retry after a failed send, broken DB path does not crash,
dashboard JSON + wrong-key rejection, and that the DB connection is really read-only.

Logs: `journalctl -u astra-watch -f`

## Watch everything from your laptop

Open `watch/laptop.html` (double-click it, or drag it into Chrome / Safari) on any computer. On first open, paste the
alert link the installer printed (`https://ntfy.sh/...`). The page then shows, live:

- live rounds with students, submissions and how many have finished
- students, submissions in the last 15 minutes, judge queue, fair-play warnings, errors
- server PC memory, CPU load, free disk, judge activity, oldest waiting submission
- submissions per minute (last hour) and the verdict mix
- every alert of the last 12 hours, new ones with a sound (and a pop-up if you allow it)
- recent errors, with **Copy for Claude** to paste the details into a chat for a fix

It finds the server's status link by itself from the alerts (the link changes whenever the tunnel restarts).
Nothing is installed on the laptop and the page never changes anything on the server.
