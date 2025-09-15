## GitHub Commit Farm

A small Python daemon that schedules 5–12 commits across the day and optionally pushes to your remote.

### Features
- Schedules a random number of commits per day (default 5–12)
- Spreads commits uniformly at random across a working window (default 10:00–22:00)
- Appends a line to a configurable file, commits, and pushes
- Persists schedule state to survive restarts
- Works as a systemd user service

### Requirements
- Python 3.8+
- git installed and available in PATH
- A git repository (initialized and with remote set if you enable push)

### Setup
1. Clone or copy this folder somewhere permanent, e.g. `~/Scripts/GitHub_Commit_Farm`.
2. Create `.env` next to the script using `env.example` as a template. Example:
```ini
REPO_PATH=/absolute/path/to/your/git/repository
WORK_START_HOUR=10
WORK_END_HOUR=22
MIN_COMMITS=5
MAX_COMMITS=12
COMMIT_FILE=farm_activity.md
COMMIT_MESSAGE_TEMPLATE=chore: update activity log
GIT_PUSH=true
USER_NAME=Your Name
USER_EMAIL=you@example.com
```
- `REPO_PATH` must point to a valid git repo (with `.git`).
- If `GIT_PUSH=true`, ensure your repo has a configured remote and credential helper.
- `USER_NAME` and `USER_EMAIL` are optional; if set and repo lacks them, they will be configured locally.

3. Test run in foreground:
```bash
python3 commit_farm.py
```
You should see logs and it will idle until the first scheduled commit time.

### Run as a systemd user service
1. Copy the provided unit file:
```bash
mkdir -p ~/.config/systemd/user
cp github-commit-farm.service ~/.config/systemd/user/
```
2. Ensure your `.env` exists at `~/Scripts/GitHub_Commit_Farm/.env` and points at your repo.
3. Reload and enable:
```bash
systemctl --user daemon-reload
systemctl --user enable --now github-commit-farm.service
```
4. Check status and logs:
```bash
systemctl --user status github-commit-farm.service | cat
journalctl --user -u github-commit-farm.service -f | cat
```

### Notes
- The script writes `state.json` next to itself to track each day's schedule.
- If it restarts midday, it resumes remaining commits for the same day.
- Change your window or commit counts in `.env` and restart the service.

### Uninstall
```bash
systemctl --user disable --now github-commit-farm.service
rm ~/.config/systemd/user/github-commit-farm.service
systemctl --user daemon-reload
``` 