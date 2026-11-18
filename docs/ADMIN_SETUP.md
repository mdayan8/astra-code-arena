# Admin setup

## First Super Admin

There is no public admin sign-up. The first account is created from the server console:

```bash
docker compose exec web python -m app.manage create-admin     # Docker
python -m app.manage create-admin                              # without Docker
```

The first admin is always a **Super Admin**. All other organisers are created inside the Control Centre
(**Admin Users → New admin**). Leaving the password empty generates a temporary one that must be changed at first sign-in.

## The private admin route

- Set `ADMIN_PATH` in `.env`, or leave it empty and one like `astra-control-3f9a1c2b7e44` is generated on first start.
- `python -m app.manage info` prints it. Share it only with organisers. It is never linked from the student portal,
  it is served with `X-Robots-Tag: noindex` and `<meta name="robots" content="noindex">`, and `/admin` returns 404.
- The route is only an extra layer. Every admin API checks the session and the role on the server, so knowing the route gives nothing without an account.

## Roles

| Capability | Super Admin | Event Admin | Question Reviewer |
|---|:-:|:-:|:-:|
| Create competitions | ✔ | ✔ | |
| Manage a competition (edit, start/pause/extend/end/finalize, participants, monitor, integrity) | all | only ones they organise | |
| Delete draft/cancelled competitions | ✔ | | |
| Question bank: create, duplicate, archive | ✔ | ✔ | edit only |
| Approve questions / save drafts to the bank | ✔ | ✔ | if "can approve" is ticked |
| Test cases + reference validation | ✔ | ✔ | ✔ |
| PDF import | ✔ | ✔ | ✔ |
| AI assistant | ✔ | ✔ | |
| Participants (students, CSV import, password resets) | ✔ | ✔ | |
| Submissions, leaderboards, reports | ✔ | own competitions | |
| Semesters (create, assign rounds, freeze, finalise) | ✔ | view | |
| Admin users, audit log, system settings, backups | ✔ | | |

Only a Super Admin can create or change a Super Admin, and the last active Super Admin cannot be demoted or suspended.
Super Admins can add organisers to any competition (**Competition → Overview → Manage organisers**).

## Sign-in protection

- Passwords are hashed with scrypt; sessions are random tokens in HTTP-only, SameSite=Strict cookies (Secure when `COOKIE_SECURE=true`).
- Every state-changing request needs the per-session CSRF token.
- Students and admins use different cookies, so a student session can never reach admin APIs.
- 5 wrong passwords lock an account for 15 minutes, doubling on each further 5 (max 24 h). Each sign-in attempt is also rate-limited per IP.

## Account recovery

- Forgotten organiser password: a Super Admin uses **Admin Users → Reset password** (temporary password, must be changed).
- Locked-out or forgotten **Super Admin** password: from the server console:

```bash
docker compose exec web python -m app.manage reset-password <username>
```

- Student passwords: students can reset their own (USN + full name + date of birth → password becomes the date of birth, DDMMYYYY), or use **Participants → open student → Reset password**.
- Note: a date-of-birth password is easy for classmates to guess. Encourage students to set their own password in their profile, and keep the fair-play review on.

## Recommended first steps

1. Create the Super Admin, sign in, change nothing else yet.
2. **System Settings**: check that the judge workers and all languages are green; choose whether students may self-register and how names appear on public leaderboards.
3. **Admin Users**: create Event Admins (and Reviewers).
4. **Semesters**: create the current semester.
5. Follow the [Organiser guide](ORGANISER_GUIDE.md) to run a pilot round with 5–10 volunteers before the real event.
