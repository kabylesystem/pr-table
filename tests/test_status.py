import json
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


class FakeIntent:
    def __init__(self, labels):
        self.labels = labels

    def label(self, m):
        if m["state"] in server.FORCED_INTENT:
            return server.FORCED_INTENT[m["state"]]
        return self.labels.get(m["id"])


class FakeCfg:
    reviewer = {"login": REVIEWER}


class FakePulls(server.Pulls):
    def __init__(self):
        self.cfg = FakeCfg()
        self.push_time, self.reviewer_review, self.reviewer_last = {}, {}, {}
        self.threads, self.slack, self.known_refs = {}, {"_reviewer": {}, "_said": {}}, {"1911", "1899"}
        self.conflict, self.gfacts, self.open_now, self.sha_seen, self.held = {}, {}, {}, {}, {}
        self.intent = FakeIntent({})


def said(pulls, ref, when, label, where="comment", state="COMMENT"):
    mid = f"{where}-{when}"
    if where == "slack":
        pulls.slack["_said"].setdefault(ref, {})[str(when)] = "text"
        mid = f"slack:{when}"
    else:
        pulls.gfacts[ref].setdefault("messages", []).append({"id": mid, "at": when, "state": state, "text": "text"})
    if label:
        pulls.intent.labels[mid] = label


def status(pulls, ref, pr, overrides=None, paused=None):
    files = {"status.json": overrides or {}, "paused.json": list(paused or [])}
    server.load_json = lambda path, default: files.get(Path(path).name, default)
    return pulls.review_state(ref, pr)["status"]


PUSH = "2026-11-09T17:00:00Z"
CASES = []


def case(name, want, **setup):
    CASES.append((name, want, setup))


T = lambda hhmm: ts(f"2026-11-09T{hhmm}:00Z")
case("requested, nothing else", "waiting")
case("open bot thread, not announced", "fix", threads=1)
case("open bot thread, announced after push", "waiting", threads=1, slack=T("18:00"))
case("announced as #114 for w114", "waiting", ref="w114", threads=1, slack_key="114", slack=T("18:00"))
case("announced before the last push", "fix", threads=1, slack=T("16:00"))
case("the reviewer asks something after the announcement", "fix", slack=T("18:00"), said=[(T("18:30"), "ACTION")])
case("the reviewer only thanks after the announcement", "waiting", slack=T("18:00"), said=[(T("18:30"), "ACK")])
case("the reviewer says he will look tomorrow", "waiting", said=[(T("18:30"), "WAIT")])
case("the reviewer passes the call to the owner", "decides", said=[(T("18:30"), "DECIDES")])
case("the reviewer parks it", "paused", said=[(T("18:30"), "PARK")])
case("we pushed after the reviewer parked it", "fix", said=[(T("16:30"), "PARK")], requested=False, requested_at="", slack=T("16:00"))
case("ask, then a thank-you: the ask still stands", "fix", said=[(T("18:30"), "ACTION"), (T("18:40"), "ACK")])
case("ask, then a decision for the owner: the owner decides", "decides", said=[(T("18:30"), "ACTION"), (T("18:40"), "DECIDES")])
case("the reviewer asked changes after push", "fix", said=[(T("17:30"), None, "review", "CHANGES_REQUESTED")])
case("the reviewer reviewed before our push, not requested", "fix", said=[(T("16:30"), "ACTION", "review", "COMMENTED")], requested=False)
case("the reviewer asked, then we requested him again", "waiting", said=[(T("18:30"), "ACTION")], requested_at="2026-11-09T19:00:00Z")
case("the reviewer asks in the Slack thread after our post", "fix", slack=T("18:00"), said=[(T("18:20"), "ACTION", "slack")])
case("we answered with @the reviewer after his Slack ask", "waiting", slack=T("18:40"), said=[(T("18:20"), "ACTION", "slack")])
case("the reviewer's message not read yet: undecided", "pending", said=[(T("18:30"), None)])
case("conflict with main beats waiting", "conflict", conflict=True)
case("not requested, not announced, never reviewed", "prep", requested=False)
case("paused wins", "paused", paused=True, threads=1)
case("hand-set decides", "decides", override="decides", threads=1)
case("the reviewer's latest verdict approves the current head", "approved", approved=True)
case("approved head, then the reviewer asked in a resolved thread", "approved", approved=True, said=[(T("17:40"), "ACTION")])
case("approval dismissed by our new commit", "fix", said=[(T("16:30"), None, "review", "APPROVED")], requested=False)
case("commit dated before the reviewer's ask but force-pushed after, then re-requested", "waiting", said=[(T("17:20"), "ACTION")], forced="2026-11-09T17:25:00Z", requested_at="2026-11-09T17:26:00Z")
case("Slack post between commit date and force-push is not after the push", "fix", threads=1, slack=T("17:10"), forced="2026-11-09T17:20:00Z", requested=False)


def build(o):
    ref = o.get("ref", "1911")
    pulls = FakePulls()
    pulls.push_time[ref] = PUSH
    pulls.threads[ref] = o.get("threads", 0)
    pulls.conflict[ref] = o.get("conflict", False)
    requested = o.get("requested") is not False
    pulls.gfacts[ref] = {"req_at": o.get("requested_at", "2026-11-09T17:10:00Z") if requested else o.get("requested_at", ""),
                         "approved": o.get("approved", False), "forced": o.get("forced", "")}
    if "slack" in o:
        pulls.slack[o.get("slack_key", ref)] = o["slack"]
    for item in o.get("said", []):
        said(pulls, ref, *item)
    pr = {"requested_reviewers": [{"login": REVIEWER}] if requested else [], "draft": o.get("draft", False), "head": {"sha": "abc"}}
    return ref, pulls, pr


def fixed_cases():
    failed = 0
    for name, want, o in CASES:
        ref, pulls, pr = build(o)
        if want == "pending":
            got = "pending" if pulls.facts(ref, pr)["pending"] else "decided"
        else:
            got = status(pulls, ref, pr, {ref: o["override"]} if "override" in o else None, [ref] if o.get("paused") else None)
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
    request = 0
    if e["requested"]:
        request = e["requested_at"] or max([m[0] for m in e["said"]] + [really_pushed, 1])
    our_handover = max(e["slack"], request)
    meaning = {"CHANGES_REQUESTED": "ACTION", "APPROVED": "ACTION"}
    latest = None
    for when, label, where, state in sorted(e["said"], key=lambda m: m[0]):
        label = meaning.get(state, label)
        if when > our_handover and label in ("ACTION", "DECIDES", "PARK"):
            latest = (when, label)
    if latest and latest[1] == "ACTION":
        return "fix"
    if latest and latest[1] == "DECIDES":
        return "decides"
    if latest and latest[1] == "PARK":
        return "fix" if really_pushed > latest[0] else "paused"
    if e["threads"] and not e["slack"] > really_pushed:
        return "fix"
    return "waiting" if our_handover else "prep"


def fuzz(rounds=50000, seed=7):
    rng = random.Random(seed)
    base = ts("2026-11-09T12:00:00Z")
    moment = lambda: rng.choice([0, 0] + [base + 600 * rng.randint(0, 12) + 7 for _ in range(4)])
    failed = 0
    for _ in range(rounds):
        ref = "1911"
        e = {"push": base + 600 * rng.randint(0, 12), "forced": moment(), "seen": moment(), "slack": moment(),
             "requested": rng.random() < .5, "requested_at": moment(), "threads": rng.choice([0, 0, 1, 3]),
             "conflict": rng.random() < .1, "draft": rng.random() < .1, "paused": rng.random() < .05,
             "approved": rng.random() < .15, "override": rng.choice([None] * 12 + ["fix", "waiting", "decides"])}
        e["said"] = []
        for i in range(rng.choice([0, 1, 1, 2, 3])):
            when = base + 600 * rng.randint(0, 12) + 13 + i
            where = rng.choice(["comment", "review", "slack"])
            state = rng.choice(["COMMENTED", "COMMENTED", "CHANGES_REQUESTED", "APPROVED"]) if where == "review" else "COMMENT"
            e["said"].append((when, rng.choice(["ACTION", "DECIDES", "PARK", "WAIT", "ACK"]), where, state))
        same_sha = rng.random() < .7
        if not e["requested"]:
            e["requested_at"] = 0
        pulls = FakePulls()
        pulls.push_time[ref] = iso(e["push"])
        pulls.threads[ref], pulls.conflict[ref] = e["threads"], e["conflict"]
        pulls.gfacts[ref] = {"req_at": iso(e["requested_at"]), "approved": e["approved"], "forced": iso(e["forced"])}
        if e["slack"]:
            pulls.slack[ref] = e["slack"]
        for item in e["said"]:
            said(pulls, ref, *item)
        if e["seen"]:
            pulls.sha_seen[ref] = ["abc" if same_sha else "old", e["seen"]]
        if not same_sha:
            e["seen"] = 0
        pr = {"requested_reviewers": [{"login": REVIEWER}] if e["requested"] else [], "draft": e["draft"], "head": {"sha": "abc"}}
        got = status(pulls, ref, pr, {ref: e["override"]} if e["override"] else None, [ref] if e["paused"] else None)
        want = oracle(e)
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
        pulls.gfacts[ref] = {}
    checks = []
    server.expire_hand_set(pulls)
    checks.append(("plain entries are converted and stamped in the file", isinstance(load(status_file)["412"], dict) and "after" in load(paused_file)["410"]))
    checks.append(("nothing new: hand-set labels stay", set(load(status_file)) == {"412", "409", "413"} and "410" in load(paused_file)))
    pulls.push_time["412"] = iso(t0 + 60)
    server.expire_hand_set(pulls)
    checks.append(("our push does not end 'decides'", "412" in load(status_file)))
    said(pulls, "412", t0 + 80, "ACK")
    server.expire_hand_set(pulls)
    checks.append(("a thank-you from the reviewer does not end 'decides'", "412" in load(status_file)))
    said(pulls, "412", t0 + 90, "ACTION")
    server.expire_hand_set(pulls)
    checks.append(("the reviewer speaking ends 'decides'", "412" not in load(status_file)))
    said(pulls, "413", t0 + 90, "ACTION")
    server.expire_hand_set(pulls)
    checks.append(("the reviewer speaking does not end 'fix'", "413" in load(status_file)))
    pulls.push_time["413"] = iso(t0 + 120)
    server.expire_hand_set(pulls)
    checks.append(("our push ends 'fix'", "413" not in load(status_file)))
    pulls.slack["409"] = t0 + 120
    server.expire_hand_set(pulls)
    checks.append(("our Slack post ends 'ask'", "409" not in load(status_file)))
    pulls.slack["410"] = t0 + 130
    said(pulls, "410", t0 + 131, "ACTION")
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
    checks.append(("the reviewer's words are kept for reading", seen["_said"].get("92", {}).get("150") == "rename the flag"))
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
