#!/usr/bin/env python3

import argparse
import os
import sys
import time
import json
import random
import signal
import subprocess
from datetime import datetime, timedelta
from typing import List, Optional, Tuple


STATE_FILE_NAME = "state.json"
DEFAULT_COMMIT_FILE = "farm_activity.md"


class GracefulKiller:
    def __init__(self) -> None:
        self.should_terminate = False
        signal.signal(signal.SIGINT, self._handler)
        signal.signal(signal.SIGTERM, self._handler)

    def _handler(self, signum, frame) -> None:  # type: ignore[no-untyped-def]
        self.should_terminate = True


def log(message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def load_env_from_dotenv(dotenv_path: str) -> None:
    if not os.path.isfile(dotenv_path):
        return
    try:
        with open(dotenv_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if "=" not in stripped:
                    continue
                key, value = stripped.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and value and key not in os.environ:
                    os.environ[key] = value
    except Exception as exc:
        log(f"Warning: Failed to read .env file: {exc}")


def get_script_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def read_config() -> dict:
    script_dir = get_script_dir()
    dotenv_path = os.path.join(script_dir, ".env")
    load_env_from_dotenv(dotenv_path)

    config = {
        "repo_path": os.environ.get("REPO_PATH", ""),
        "work_start_hour": int(os.environ.get("WORK_START_HOUR", "10")),
        "work_end_hour": int(os.environ.get("WORK_END_HOUR", "22")),
        "min_commits": int(os.environ.get("MIN_COMMITS", "5")),
        "max_commits": int(os.environ.get("MAX_COMMITS", "12")),
        "commit_file": os.environ.get("COMMIT_FILE", DEFAULT_COMMIT_FILE),
        "commit_message_template": os.environ.get(
            "COMMIT_MESSAGE_TEMPLATE", "chore: update activity log"
        ),
        "git_push": os.environ.get("GIT_PUSH", "true").lower() in ("1", "true", "yes", "on"),
        "git_user_name": os.environ.get("USER_NAME", ""),
        "git_user_email": os.environ.get("USER_EMAIL", ""),
    }

    # Validate and normalize
    if not config["repo_path"]:
        log("ERROR: REPO_PATH is not set. Set it in .env or environment.")
        sys.exit(1)

    if not os.path.isdir(config["repo_path"]):
        log(f"ERROR: REPO_PATH does not exist: {config['repo_path']}")
        sys.exit(1)

    for key in ("work_start_hour", "work_end_hour"):
        hour = config[key]
        if hour < 0 or hour > 23:
            log(f"ERROR: {key} must be between 0 and 23 inclusive. Got {hour}")
            sys.exit(1)

    if config["work_end_hour"] <= config["work_start_hour"]:
        log(
            "ERROR: WORK_END_HOUR must be greater than WORK_START_HOUR and on the same day."
        )
        sys.exit(1)

    if config["min_commits"] < 1 or config["max_commits"] < 1:
        log("ERROR: MIN_COMMITS and MAX_COMMITS must be positive integers.")
        sys.exit(1)

    if config["min_commits"] > config["max_commits"]:
        log("ERROR: MIN_COMMITS cannot be greater than MAX_COMMITS.")
        sys.exit(1)

    return config


def run_git_command(args: List[str], cwd: str, allow_fail: bool = False) -> Tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if proc.returncode != 0 and not allow_fail:
            log(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        log("ERROR: git not found in PATH")
        sys.exit(1)


def ensure_git_repo(repo_path: str) -> None:
    git_dir = os.path.join(repo_path, ".git")
    if not os.path.isdir(git_dir):
        log(f"ERROR: {repo_path} is not a git repository (missing .git). Initialize it manually.")
        sys.exit(1)


def maybe_set_git_identity(repo_path: str, name: str, email: str) -> None:
    if not name or not email:
        return
    # Set only if not configured at repo level
    code_name, out_name, _ = run_git_command(["config", "--get", "user.name"], repo_path, allow_fail=True)
    code_email, out_email, _ = run_git_command(["config", "--get", "user.email"], repo_path, allow_fail=True)
    if code_name != 0 or not out_name.strip():
        run_git_command(["config", "user.name", name], repo_path, allow_fail=True)
    if code_email != 0 or not out_email.strip():
        run_git_command(["config", "user.email", email], repo_path, allow_fail=True)


def state_file_path() -> str:
    return os.path.join(get_script_dir(), STATE_FILE_NAME)


def read_state() -> dict:
    path = state_file_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def write_state(state: dict) -> None:
    path = state_file_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as exc:
        log(f"Warning: Failed to write state file: {exc}")


def today_str(dt: Optional[datetime] = None) -> str:
    if dt is None:
        dt = datetime.now()
    return dt.strftime("%Y-%m-%d")


def generate_schedule_for_today(
    start_hour: int,
    end_hour: int,
    min_commits: int,
    max_commits: int,
) -> List[str]:
    now = datetime.now()
    day_start = now.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    day_end = now.replace(hour=end_hour, minute=0, second=0, microsecond=0)

    if now > day_end:
        # If we've already passed the window today, schedule nothing
        return []

    # If we are already within the window, we still schedule across the full window
    total_seconds = int((day_end - day_start).total_seconds())
    if total_seconds <= 0:
        return []

    commit_count = random.randint(min_commits, max_commits)

    # Sample unique offsets to spread commits across the window
    # If commit_count exceeds total_seconds, we'll clamp (unlikely in practice)
    commit_count = min(commit_count, max(1, total_seconds))

    offsets = sorted(random.sample(range(total_seconds), commit_count))
    times: List[str] = []
    for offset in offsets:
        scheduled_time = day_start + timedelta(seconds=offset)
        # Skip times already in the past; we only keep future times
        if scheduled_time <= now:
            continue
        times.append(scheduled_time.isoformat())

    return times


def next_scheduled_time(scheduled_iso_times: List[str]) -> Optional[datetime]:
    if not scheduled_iso_times:
        return None
    for iso_ts in scheduled_iso_times:
        try:
            scheduled_dt = datetime.fromisoformat(iso_ts)
            if scheduled_dt > datetime.now():
                return scheduled_dt
        except Exception:
            continue
    return None


def append_activity_line(repo_path: str, commit_file: str) -> None:
    file_path = os.path.join(repo_path, commit_file)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    phrases = [
        "Automated maintenance",
        "Routine update",
        "Sync notes",
        "Housekeeping",
        "Keep-alive",
        "Log entry",
        "Notes refresh",
    ]
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    phrase = random.choice(phrases)

    # Ensure file exists with a header
    if not os.path.exists(file_path):
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("# Activity Log\n\n")

    with open(file_path, "a", encoding="utf-8") as f:
        f.write(f"- {timestamp} — {phrase}\n")


def perform_commit(repo_path: str, commit_file: str, commit_message_template: str, push: bool) -> None:
    run_git_command(["add", commit_file], repo_path)
    commit_message = f"{commit_message_template} ({datetime.now().isoformat(timespec='seconds')})"
    code, _, err = run_git_command(["commit", "-m", commit_message], repo_path, allow_fail=True)

    if code != 0:
        if "nothing to commit" in err.lower():
            log("No changes to commit; skipping.")
            return
        log("Commit failed; see logs above.")
        return

    log("Commit created.")

    if push:
        code_push, _, err_push = run_git_command(["push"], repo_path, allow_fail=True)
        if code_push != 0:
            log(f"Warning: git push failed: {err_push.strip()}")
        else:
            log("Pushed to remote.")


def main() -> None:
    parser = argparse.ArgumentParser(description="GitHub Commit Farm daemon")
    parser.add_argument("--now", action="store_true", help="Perform one immediate commit and exit")
    args = parser.parse_args()

    config = read_config()
    repo_path = config["repo_path"]

    ensure_git_repo(repo_path)
    maybe_set_git_identity(repo_path, config["git_user_name"], config["git_user_email"])

    if args.now:
        append_activity_line(repo_path, config["commit_file"])
        perform_commit(
            repo_path,
            config["commit_file"],
            config["commit_message_template"],
            config["git_push"],
        )
        log("Immediate commit completed.")
        return

    killer = GracefulKiller()

    # Initialize or refresh state
    state = read_state()
    state_date = state.get("date")
    scheduled_times: List[str] = state.get("scheduled_times", []) if isinstance(state.get("scheduled_times"), list) else []

    def refresh_schedule() -> List[str]:
        return generate_schedule_for_today(
            config["work_start_hour"],
            config["work_end_hour"],
            config["min_commits"],
            config["max_commits"],
        )

    if state_date != today_str():
        scheduled_times = refresh_schedule()
        state = {"date": today_str(), "scheduled_times": scheduled_times}
        write_state(state)
        log(f"New schedule for {state['date']}: {len(scheduled_times)} commits queued.")
    else:
        # Purge past times in case of restart
        now = datetime.now()
        scheduled_times = [t for t in scheduled_times if datetime.fromisoformat(t) > now]
        state["scheduled_times"] = scheduled_times
        write_state(state)
        log(f"Resuming schedule for {state['date']}: {len(scheduled_times)} commits remaining.")

    while not killer.should_terminate:
        now = datetime.now()
        # If the day has rolled over or window ended, regenerate schedule
        if today_str(now) != state.get("date"):
            scheduled_times = refresh_schedule()
            state = {"date": today_str(now), "scheduled_times": scheduled_times}
            write_state(state)
            log(f"New schedule for {state['date']}: {len(scheduled_times)} commits queued.")

        nxt = next_scheduled_time(scheduled_times)
        if nxt is None:
            # Sleep until next day start minus small margin
            next_day = (now + timedelta(days=1)).replace(hour=config["work_start_hour"], minute=0, second=0, microsecond=0)
            sleep_seconds = max(30.0, (next_day - now).total_seconds())
            log("No commits left today. Sleeping until next window.")
            # Sleep in small chunks to handle termination
            slept = 0.0
            while slept < sleep_seconds and not killer.should_terminate:
                time.sleep(min(60.0, sleep_seconds - slept))
                slept += min(60.0, sleep_seconds - slept)
            continue

        # Sleep until next scheduled commit
        delta = (nxt - now).total_seconds()
        if delta > 0:
            log(f"Next commit at {nxt.strftime('%H:%M:%S')} (in {int(delta)}s)")
            slept = 0.0
            while slept < delta and not killer.should_terminate:
                step = min(30.0, delta - slept)
                time.sleep(step)
                slept += step

        if killer.should_terminate:
            break

        # Make a small change and commit
        append_activity_line(repo_path, config["commit_file"])
        perform_commit(
            repo_path,
            config["commit_file"],
            config["commit_message_template"],
            config["git_push"],
        )

        # Remove the time we just used and persist state
        scheduled_times = [t for t in scheduled_times if t != nxt.isoformat()]
        state["scheduled_times"] = scheduled_times
        write_state(state)

        # Short pause before checking next schedule
        time.sleep(2)

    log("Shutting down.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"Fatal error: {exc}")
        sys.exit(1) 