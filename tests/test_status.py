import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server

REVIEWER = "alex"


def ts(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


class FakeCfg:
    reviewer = {"login": REVIEWER}


class FakePulls(server.Pulls):
    def __init__(self):
        self.cfg = FakeCfg()
        self.push_time, self.reviewer_review, self.reviewer_last = {}, {}, {}
        self.threads, self.slack, self.known_refs = {}, {}, {"412"}
        self.conflict = {}


PUSH = "2026-11-09T17:00:00Z"
CASES = [
    ("requested, nothing else", "waiting", {}),
    ("open bot thread, not announced", "fix", {"threads": 1}),
    ("open bot thread, announced after push", "waiting", {"threads": 1, "slack": ts("2026-11-09T18:00:00Z")}),
    ("announced as #88 for w88", "waiting", {"ref": "w88", "threads": 1, "slack_key": "88", "slack": ts("2026-11-09T18:00:00Z")}),
    ("announced before the last push", "fix", {"threads": 1, "slack": ts("2026-11-09T16:00:00Z")}),
    ("reviewer commented after the announcement", "fix", {"threads": 1, "slack": ts("2026-11-09T18:00:00Z"), "comment": "2026-11-09T18:30:00Z"}),
    ("reviewer asked changes after push", "fix", {"review": ("2026-11-09T17:30:00Z", "CHANGES_REQUESTED")}),
    ("reviewed, pushed after, not requested", "fix", {"review": ("2026-11-09T16:30:00Z", "COMMENTED"), "requested": False}),
    ("draft", "draft", {"draft": True}),
    ("conflict with main", "conflict", {"conflict": True}),
    ("not requested, not announced, never reviewed", "prep", {"requested": False}),
]


def run():
    failed = 0
    for name, want, o in CASES:
        ref = o.get("ref", "412")
        server.load_json = lambda path, default: default
        pulls = FakePulls()
        pulls.push_time[ref] = PUSH
        pulls.threads[ref] = o.get("threads", 0)
        pulls.conflict[ref] = o.get("conflict", False)
        if "slack" in o:
            pulls.slack[o.get("slack_key", ref)] = o["slack"]
        if "review" in o:
            pulls.reviewer_review[ref] = o["review"]
        if "comment" in o:
            pulls.reviewer_last[ref] = o["comment"]
        pr = {"requested_reviewers": [] if o.get("requested") is False else [{"login": REVIEWER}], "draft": o.get("draft", False)}
        got = pulls.review_state(ref, pr)["status"]
        failed += got != want
        print(f"{'ok  ' if got == want else 'FAIL'} {name}: {got} (want {want})")
    print(f"{len(CASES) - failed}/{len(CASES)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    run()
