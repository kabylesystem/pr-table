import random
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server

REVIEWER = "alex"
LOAD_JSON = server.load_json


def ts(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if t else ""


class FakeCfg:
    reviewer = {"login": REVIEWER}


class FakePulls(server.Pulls):
    def __init__(self):
        self.cfg = FakeCfg()
        self.push_time, self.reviewer_review, self.reviewer_last = {}, {}, {}
        self.threads, self.slack, self.known_refs = {}, {"_reviewer": {}}, {"412"}
        self.conflict, self.gfacts, self.open_now = {}, {}, {}


def status(pulls, ref, pr, override=None, paused=False):
    files = {"status.json": {ref: override} if override else {}, "paused.json": [ref] if paused else []}
    server.load_json = lambda path, default: files.get(Path(path).name, default)
    return pulls.review_state(ref, pr)["status"]


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
    ("reviewer commented, then we requested again", "waiting", {"comment": "2026-11-09T18:30:00Z", "requested_at": "2026-11-09T19:00:00Z"}),
    ("reviewer commented while still requested from before", "fix", {"comment": "2026-11-09T18:30:00Z"}),
    ("reviewer replied in the Slack thread after our post", "fix", {"slack": ts("2026-11-09T18:00:00Z"), "slack_reviewer": ts("2026-11-09T18:20:00Z")}),
    ("we answered in the Slack thread after the reviewer", "waiting", {"slack": ts("2026-11-09T18:40:00Z"), "slack_reviewer": ts("2026-11-09T18:20:00Z")}),
    ("reviewer comment seen only by GraphQL", "fix", {"graphql": "2026-11-09T18:30:00Z"}),
    ("approved after the last push", "approved", {"review": ("2026-11-09T17:30:00Z", "APPROVED")}),
    ("approved, we pushed after", "fix", {"review": ("2026-11-09T16:30:00Z", "APPROVED"), "requested": False}),
    ("hand-set decides", "decides", {"override": "decides", "threads": 1}),
    ("paused wins", "paused", {"paused": True, "threads": 1}),
    ("draft", "draft", {"draft": True}),
    ("conflict with main", "conflict", {"conflict": True}),
    ("not requested, not announced, never reviewed", "prep", {"requested": False}),
]


def fixed_cases():
    failed = 0
    for name, want, o in CASES:
        ref = o.get("ref", "412")
        pulls = FakePulls()
        pulls.push_time[ref] = PUSH
        pulls.threads[ref], pulls.conflict[ref] = o.get("threads", 0), o.get("conflict", False)
        if "slack" in o:
            pulls.slack[o.get("slack_key", ref)] = o["slack"]
        if "slack_reviewer" in o:
            pulls.slack["_reviewer"][ref] = o["slack_reviewer"]
        if "review" in o:
            pulls.reviewer_review[ref] = o["review"]
        if "comment" in o:
            pulls.reviewer_last[ref] = o["comment"]
        requested = o.get("requested", True)
        pulls.gfacts[ref] = {"req_at": o.get("requested_at", "2026-11-09T17:10:00Z") if requested else "", "reviewer_last": o.get("graphql", "")}
        pr = {"requested_reviewers": [{"login": REVIEWER}] if requested else [], "draft": o.get("draft", False)}
        got = status(pulls, ref, pr, o.get("override"), o.get("paused", False))
        failed += got != want
        if got != want:
            print(f"FAIL {name}: {got} (want {want})")
    print(f"fixed cases: {len(CASES) - failed}/{len(CASES)} passed")
    return failed


def oracle(e):
    if e["paused"]:
        return "paused"
    if e["override"]:
        return e["override"]
    if e["draft"]:
        return "draft"
    if e["conflict"]:
        return "conflict"
    if e["review"] and e["review"][1] == "APPROVED" and e["review"][0] > e["push"] and e["threads"] == 0:
        return "approved"
    our_handover = max([e["slack"]] + ([e["requested_at"] or max(e["reviewer"], e["push"], 1)] if e["requested"] else []))
    if e["reviewer"] > our_handover:
        return "fix"
    if e["threads"] and not e["slack"] > e["push"]:
        return "fix"
    return "waiting" if our_handover else "prep"


def fuzz(rounds=50000, seed=7):
    rng = random.Random(seed)
    base = ts("2026-11-09T12:00:00Z")
    moment = lambda: rng.choice([0, 0] + [base + 600 * rng.randint(0, 12) for _ in range(4)])
    failed = 0
    for _ in range(rounds):
        ref = "412"
        e = {"push": base + 600 * rng.randint(0, 12), "slack": moment(), "slack_reviewer": moment(),
             "comment": moment(), "graphql": moment(), "requested": rng.random() < .5, "requested_at": moment(),
             "threads": rng.choice([0, 0, 1, 3]), "conflict": rng.random() < .1, "draft": rng.random() < .1,
             "paused": rng.random() < .05, "override": rng.choice([None] * 12 + ["fix", "waiting", "decides"])}
        r = rng.choice([None, None, "APPROVED", "CHANGES_REQUESTED", "COMMENTED"])
        e["review"] = (base + 600 * rng.randint(0, 12), r) if r else None
        e["reviewer"] = max(e["slack_reviewer"], e["comment"], e["graphql"], e["review"][0] if e["review"] else 0)
        if not e["requested"]:
            e["requested_at"] = 0
        pulls = FakePulls()
        pulls.push_time[ref] = iso(e["push"])
        pulls.threads[ref], pulls.conflict[ref] = e["threads"], e["conflict"]
        if e["slack"]:
            pulls.slack[ref] = e["slack"]
        if e["slack_reviewer"]:
            pulls.slack["_reviewer"][ref] = e["slack_reviewer"]
        if e["review"]:
            pulls.reviewer_review[ref] = (iso(e["review"][0]), e["review"][1])
        if e["comment"]:
            pulls.reviewer_last[ref] = iso(e["comment"])
        pulls.gfacts[ref] = {"req_at": iso(e["requested_at"]), "reviewer_last": iso(e["graphql"])}
        pr = {"requested_reviewers": [{"login": REVIEWER}] if e["requested"] else [], "draft": e["draft"]}
        got, want = status(pulls, ref, pr, e["override"], e["paused"]), oracle(e)
        if got != want:
            failed += 1
            if failed <= 5:
                print(f"FAIL fuzz: got {got}, want {want}, case {e}")
    print(f"fuzz: {rounds - failed}/{rounds} random timelines agree with the independent oracle")
    return failed


def hand_set_expiry():
    server.load_json = LOAD_JSON
    tmp = Path(tempfile.mkdtemp())
    server.DATA, server.STAMPS, server.LABEL_LOG = tmp, tmp / "stamps.json", tmp / "labels.log"
    (tmp / "status.json").write_text('{"412": "decides", "409": "ask"}')
    (tmp / "paused.json").write_text('["410"]')
    set_at = (tmp / "status.json").stat().st_mtime
    pulls = FakePulls()
    for ref in ("412", "409", "410"):
        pulls.push_time[ref] = iso(set_at - 3600)
        pulls.open_now[ref] = ("o/r", {"requested_reviewers": [], "draft": False})
    read = lambda name: (tmp / name).read_text()
    checks = []
    server.expire_hand_set(pulls)
    checks.append(("nothing new: hand-set labels stay", '"decides"' in read("status.json") and "410" in read("paused.json")))
    pulls.reviewer_last["412"] = iso(set_at + 60)
    server.expire_hand_set(pulls)
    checks.append(("reviewer commented after 'decides' was set: removed", '"412"' not in read("status.json")))
    checks.append(("the other hand-set label is untouched", '"409": "ask"' in read("status.json")))
    pulls.slack["409"] = set_at + 120
    server.expire_hand_set(pulls)
    checks.append(("posted on Slack after 'ask' was set: removed", read("status.json") == "{}"))
    pulls.push_time["410"] = iso(set_at + 30)
    server.expire_hand_set(pulls)
    checks.append(("pushed after the pause: removed", read("paused.json") == "[]"))
    checks.append(("every expiry is logged", read("labels.log").count("expired") == 3))
    failed = sum(not ok for _, ok in checks)
    for name, ok in checks:
        if not ok:
            print("FAIL", name)
    print(f"hand-set expiry: {len(checks) - failed}/{len(checks)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if fixed_cases() + fuzz() + hand_set_expiry() else 0)
