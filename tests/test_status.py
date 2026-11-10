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
        self.conflict, self.gfacts, self.open_now, self.sha_seen, self.held = {}, {}, {}, {}, {}


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
    ("the reviewer's latest verdict approves the current head", "approved", {"review": ("2026-11-09T17:30:00Z", "APPROVED"), "approved": True}),
    ("approved head, then the reviewer replied in a resolved thread", "approved", {"review": ("2026-11-09T17:40:00Z", "COMMENTED"), "approved": True}),
    ("approval dismissed by a new commit", "fix", {"review": ("2026-11-09T17:30:00Z", "APPROVED"), "requested": False}),
    ("commit dated before the comment but force-pushed after, then re-requested", "waiting", {"comment": "2026-11-09T17:20:00Z", "forced": "2026-11-09T17:25:00Z", "requested_at": "2026-11-09T17:26:00Z"}),
    ("Slack post between commit date and force-push is not after the push", "fix", {"threads": 1, "slack": ts("2026-11-09T17:10:00Z"), "forced": "2026-11-09T17:20:00Z", "requested": False}),
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
        pulls.gfacts[ref] = {"req_at": o.get("requested_at", "2026-11-09T17:10:00Z") if requested else "", "reviewer_last": o.get("graphql", ""),
                             "approved": o.get("approved", False), "forced": o.get("forced", "")}
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
    if e["approved"] and e["threads"] == 0:
        return "approved"
    really_pushed = max(e["push"], e["forced"], e["seen"])
    our_handover = max([e["slack"]] + ([e["requested_at"] or max(e["reviewer"], really_pushed, 1)] if e["requested"] else []))
    if e["reviewer"] > our_handover:
        return "fix"
    if e["threads"] and not e["slack"] > really_pushed:
        return "fix"
    return "waiting" if our_handover else "prep"


def fuzz(rounds=50000, seed=7):
    rng = random.Random(seed)
    base = ts("2026-11-09T12:00:00Z")
    moment = lambda: rng.choice([0, 0] + [base + 600 * rng.randint(0, 12) for _ in range(4)])
    failed = 0
    for _ in range(rounds):
        ref = "412"
        e = {"push": base + 600 * rng.randint(0, 12), "forced": moment(), "seen": moment(), "slack": moment(),
             "slack_reviewer": moment(), "comment": moment(), "graphql": moment(), "requested": rng.random() < .5,
             "requested_at": moment(), "threads": rng.choice([0, 0, 1, 3]), "conflict": rng.random() < .1,
             "draft": rng.random() < .1, "paused": rng.random() < .05, "approved": rng.random() < .15,
             "override": rng.choice([None] * 12 + ["fix", "waiting", "decides"])}
        r = rng.choice([None, None, "APPROVED", "CHANGES_REQUESTED", "COMMENTED"])
        e["review"] = (base + 600 * rng.randint(0, 12), r) if r else None
        e["reviewer"] = max(e["slack_reviewer"], e["comment"], e["graphql"], e["review"][0] if e["review"] else 0)
        same_sha = rng.random() < .7
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
        if e["seen"]:
            pulls.sha_seen[ref] = ["abc" if same_sha else "old", e["seen"]]
        if not same_sha:
            e["seen"] = 0
        pulls.gfacts[ref] = {"req_at": iso(e["requested_at"]), "reviewer_last": iso(e["graphql"]),
                             "approved": e["approved"], "forced": iso(e["forced"])}
        pr = {"requested_reviewers": [{"login": REVIEWER}] if e["requested"] else [], "draft": e["draft"], "head": {"sha": "abc"}}
        got, want = status(pulls, ref, pr, e["override"], e["paused"]), oracle(e)
        if got != want:
            failed += 1
            if failed <= 5:
                print(f"FAIL fuzz: got {got}, want {want}, case {e}")
    print(f"fuzz: {rounds - failed}/{rounds} random timelines agree with the independent oracle")
    return failed


def hand_set_expiry():
    import json
    server.load_json = LOAD_JSON
    tmp = Path(tempfile.mkdtemp())
    server.DATA, server.LABEL_LOG = tmp, tmp / "labels.log"
    status_file, paused_file = tmp / "status.json", tmp / "paused.json"
    status_file.write_text('{"412": "decides", "409": "ask", "413": "fix"}')
    paused_file.write_text('["410"]')
    load = lambda f: json.loads(f.read_text())
    t0 = server.time.time()
    pulls = FakePulls()
    for ref in ("412", "409", "410", "413"):
        pulls.push_time[ref] = iso(t0 - 3600)
        pulls.open_now[ref] = ("o/r", {"requested_reviewers": [], "draft": False, "head": {"sha": "abc"}})
    checks = []
    server.expire_hand_set(pulls)
    checks.append(("plain entries are converted and stamped in the file", isinstance(load(status_file)["412"], dict) and "after" in load(paused_file)["410"]))
    checks.append(("nothing new: hand-set labels stay", set(load(status_file)) == {"412", "409", "413"} and "410" in load(paused_file)))
    pulls.push_time["412"] = iso(t0 + 60)
    server.expire_hand_set(pulls)
    checks.append(("our push does not end 'decides'", "412" in load(status_file)))
    pulls.reviewer_last["412"] = iso(t0 + 90)
    server.expire_hand_set(pulls)
    checks.append(("the reviewer speaking ends 'decides'", "412" not in load(status_file)))
    pulls.reviewer_last["413"] = iso(t0 + 90)
    server.expire_hand_set(pulls)
    checks.append(("the reviewer speaking does not end 'fix'", "413" in load(status_file)))
    pulls.push_time["413"] = iso(t0 + 120)
    server.expire_hand_set(pulls)
    checks.append(("our push ends 'fix'", "413" not in load(status_file)))
    pulls.slack["409"] = t0 + 120
    server.expire_hand_set(pulls)
    checks.append(("our Slack post ends 'ask'", "409" not in load(status_file)))
    pulls.slack["410"] = t0 + 130
    pulls.reviewer_last["410"] = iso(t0 + 130)
    server.expire_hand_set(pulls)
    checks.append(("Slack and the reviewer do not end a pause", "410" in load(paused_file)))
    pulls.push_time["410"] = iso(t0 + 140)
    server.expire_hand_set(pulls)
    checks.append(("our push ends a pause", "410" not in load(paused_file)))
    status_file.write_text(json.dumps({"412": {"label": "decides", "after": iso(t0)}}))
    server.expire_hand_set(pulls)
    checks.append(("a stale copy written back keeps its old stamp and expires again", "412" not in load(status_file)))
    checks.append(("every expiry is logged", server.LABEL_LOG.read_text().count("expired") == 5))
    failed = sum(not ok for _, ok in checks)
    for name, ok in checks:
        if not ok:
            print("FAIL", name)
    print(f"hand-set expiry: {len(checks) - failed}/{len(checks)} passed")
    return failed


def slack_parsing():
    class Cfg:
        repos = [("acme/api", ""), ("acme/web", "w")]
        slack = {"token_env": "T", "channel": "C1", "me": "UME", "reviewer": "UREV"}
    history = [
        {"user": "UME", "ts": "100", "text": "#88 w#12 two PRs in one post"},
        {"user": "UME", "ts": "110", "text": "<https://github.com/acme/web/pull/42|#42> link form"},
        {"user": "UREV", "ts": "120", "text": "#90 can you split this"},
        {"user": "UOTHER", "ts": "130", "text": "#91 someone else"},
        {"user": "UME", "ts": "140", "text": "#92 ready", "latest_reply": "170"},
        {"user": "UME", "ts": "150", "text": "deploy done, #93 next"},
    ]
    replies = [{"ts": "140"}, {"user": "UREV", "ts": "150", "text": "rename the flag"},
               {"user": "UME", "ts": "160", "text": "ok, on it"}, {"user": "UME", "ts": "170", "text": "fixed <@UREV>"}]
    server.slack_get = lambda token, method, **params: {"ok": True, "messages": history if method == "conversations.history" else replies}
    server.SLACK_STORE = Path(tempfile.mkdtemp()) / "seen.json"
    server.os.environ["T"] = "x"
    seen = server.slack_latest(Cfg())
    checks = [
        ("every leading ref counts", seen.get("88") == 100 and seen.get("w12") == 100),
        ("link form counts", seen.get("w42") == 110),
        ("reviewer top-level post counts as the reviewer", seen["_reviewer"].get("90") == 120 and "90" not in seen),
        ("other people's posts are ignored", "91" not in seen),
        ("reviewer thread reply counts as the reviewer", seen["_reviewer"].get("92") == 150),
        ("our reply hands over only when it mentions the reviewer", seen.get("92") == 170),
        ("a ref in the middle is not an announcement", "93" not in seen),
    ]
    history.clear()
    checks.append(("posts that scrolled out are remembered", server.slack_latest(Cfg()).get("88") == 100))
    failed = sum(not ok for _, ok in checks)
    for name, ok in checks:
        if not ok:
            print("FAIL", name)
    print(f"slack parsing: {len(checks) - failed}/{len(checks)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if fixed_cases() + fuzz() + hand_set_expiry() + slack_parsing() else 0)
