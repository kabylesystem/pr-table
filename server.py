import argparse
import json
from concurrent.futures import ThreadPoolExecutor
import os
import random
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
DATA = ROOT / "data"
CACHE = Path.home() / ".cache/pr-table/last.json"
ASK_LOG = Path.home() / ".cache/pr-table/ask.log"
LABEL_LOG = Path.home() / ".cache/pr-table/labels.log"
SHA_SEEN = Path.home() / ".cache/pr-table/sha-seen.json"
EXPIRES_ON = {"decides": ("reviewer", "handover"), "waiting": ("reviewer",), "fix": ("push", "handover"),
              "ask": ("slack", "reviewer"), "paused": ("push",)}
ALERT_SECONDS = 45
GRAPHQL_PR = """query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n){
updatedAt headRefOid mergeable reviewThreads(first:100){nodes{isResolved}}
opinion: latestOpinionatedReviews(first:20){nodes{author{login} state commit{oid}}}
forced: timelineItems(last:1,itemTypes:[HEAD_REF_FORCE_PUSHED_EVENT]){nodes{... on HeadRefForcePushedEvent{createdAt}}}
asked: timelineItems(last:10,itemTypes:[REVIEW_REQUESTED_EVENT]){nodes{... on ReviewRequestedEvent{createdAt requestedReviewer{... on User{login}}}}}
reviews(last:50){nodes{id author{login} state submittedAt body comments(first:30){nodes{body}}}}
comments(last:100){nodes{id author{login} createdAt body}}
commits(last:1){nodes{commit{oid committedDate statusCheckRollup{contexts(first:100){nodes{__typename
... on CheckRun{status conclusion isRequired(pullRequestNumber:$n)}
... on StatusContext{state isRequired(pullRequestNumber:$n)}}}}}}}}}}"""

lock = threading.Lock()
changed = threading.Condition(lock)
state = {
    "focus": None, "doing": "", "active": False, "agents": [],
    "next": [], "review": [], "merged": [], "merged_recent": [], "ask": [],
    "alert": None, "party": None, "gh_ok": 0, "version": 0, "ci_fail": [],
    "build": str(int(time.time())), "ui": {}, "visible": True,
}


def publish(**fields):
    with changed:
        if all(state.get(k) == v for k, v in fields.items()):
            return
        state.update(fields)
        state["version"] += 1
        changed.notify_all()


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def iso_ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() if value else 0.0


def log_label(line):
    LABEL_LOG.parent.mkdir(parents=True, exist_ok=True)
    with LABEL_LOG.open("a") as log:
        log.write(f"{datetime.now():%F %T} {line}\n")


def hand_set_files():
    return {"status": DATA / "status.json", "paused": DATA / "paused.json"}


def iso_of(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, path)


def status_label(ref):
    value = load_json(DATA / "status.json", {}).get(ref)
    return value.get("label") if isinstance(value, dict) else value


def hand_set_entries():
    entries = []
    for kind, path in hand_set_files().items():
        data = load_json(path, None)
        if data is None:
            continue
        if isinstance(data, list):
            data = {ref: "paused" for ref in data}
        plain = False
        for ref, value in list(data.items()):
            if not isinstance(value, dict):
                value = {"label": value if kind == "status" else "paused", "after": iso_of(time.time())}
                plain = True
            value.setdefault("label", "paused")
            value.setdefault("after", iso_of(time.time()))
            data[ref] = value if kind == "status" else {"after": value["after"]}
            entries.append((kind, ref, value["label"], iso_ts(value["after"])))
        if plain:
            write_json(path, data)
    return entries


def expire_hand_set(pulls):
    gone = []
    for kind, ref, label, after in hand_set_entries():
        if ref not in pulls.open_now:
            continue
        events = pulls.events(ref, pulls.open_now[ref][1])
        hit = [name for name in EXPIRES_ON.get(label, ("push", "reviewer", "handover")) if events[name] > after]
        if hit:
            gone.append((kind, ref, label, after, hit[0], events[hit[0]]))
    for kind, path in hand_set_files().items():
        drop = {ref for k, ref, *_ in gone if k == kind}
        if drop:
            data = json.loads(path.read_text())
            data = {r: v for r, v in data.items() if r not in drop} if isinstance(data, dict) else [r for r in data if r not in drop]
            write_json(path, data)
    for kind, ref, label, after, why, when in gone:
        log_label(f"{ref} hand-set {label} expired: set after {iso_of(after)}, new {why} event at {iso_of(when)}")
    return gone


class Config:
    def __init__(self, path):
        raw = load_json(path, None)
        if raw is None:
            raise SystemExit(f"Missing or invalid config: {path} (copy config.example.json to config.json)")
        self.author = raw["github"]["author"]
        self.repos = [(r["name"], r.get("label", "")) for r in raw["github"]["repos"]]
        self.reviewer = raw.get("reviewer", {})
        self.intent_command = raw.get("reviewer_intent", {}).get("command") or []
        self.ready_marker = raw.get("ready_marker", "")
        nxt = raw.get("next", {})
        self.clones = [Path(os.path.expanduser(c)) for c in nxt.get("clones", [])]
        self.branch_pattern = re.compile(nxt.get("branch_pattern", r"^(?:[\w-]+/)?(\d+)-"))
        self.issue_pattern = re.compile(nxt.get("issue_pattern", r"(?i)\b[a-z]+-(\d+)\b"))
        self.claude = raw.get("claude", {})
        self.slack = raw.get("slack", {})
        self.ui = {
            "title": raw.get("title", "PR TABLE"),
            "reviewer": self.reviewer.get("name", "Reviewer"),
            "reviewer_figure": self.reviewer.get("figure", ""),
            "crew": raw.get("crew", []),
        }
        self.port = int(raw.get("port", 4950))

    def ref(self, repo, number):
        label = dict(self.repos)[repo]
        return f"{label}{number}"


class GitHub:
    def __init__(self):
        self.token = os.environ.get("GITHUB_TOKEN") or subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()
        if not self.token:
            raise SystemExit("No GitHub token: set GITHUB_TOKEN or log in with `gh auth login`")
        self.cache = {}

    def request(self, url, data=None, etag=True):
        req = urllib.request.Request(url, data=data, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "pr-table",
        })
        cached = self.cache.get(url) if etag else None
        if cached:
            req.add_header("If-None-Match", cached[0])
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                body = json.loads(r.read())
                if etag:
                    self.cache[url] = (r.headers.get("ETag", ""), body)
                return body
        except urllib.error.HTTPError as e:
            if e.code == 304 and cached:
                return cached[1]
            raise

    def get(self, path):
        return self.request(f"https://api.github.com{path}")

    def graphql(self, query, **variables):
        body = json.dumps({"query": query, "variables": variables}).encode()
        result = self.request("https://api.github.com/graphql", data=body, etag=False)
        if result.get("errors"):
            raise RuntimeError(result["errors"][0].get("message", "GraphQL error"))
        return result["data"]


class Pulls:
    def __init__(self, cfg):
        self.cfg = cfg
        self.gh = GitHub()
        self.merged = {}
        self.issue_to_ref = {}
        self.known_refs = set()
        self.updated = {}
        self.seen_reviewer = set()
        self.first_pass = True
        self.reviewer_last = {}
        self.reviewer_review = {}
        self.threads = {}
        self.conflict = {}
        self.gfacts = {}
        self.held = {}
        self.heads = {}
        self.sha_seen = load_json(SHA_SEEN, {})
        self.slack = {}
        self.push_time = {}
        self.open_now = {}
        self.alert = None
        self.since = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%dT00:00:00Z")
        self.tick = 0

    def note_reviewer(self, ref, item_id, when, kind):
        if item_id in self.seen_reviewer:
            return
        self.seen_reviewer.add(item_id)
        self.reviewer_last[ref] = max(self.reviewer_last.get(ref, ""), when)
        if not self.first_pass:
            self.alert = {"ref": ref, "kind": kind, "until": time.time() + ALERT_SECONDS}

    def prefetch(self, paths):
        with ThreadPoolExecutor(max_workers=12) as pool:
            return dict(zip(paths, pool.map(self.gh.get, paths)))

    def poll(self):
        cfg, open_now = self.cfg, {}
        reviewer = cfg.reviewer.get("login", "")
        paths = []
        for repo, _ in cfg.repos:
            paths += [f"/repos/{repo}/pulls?state=open&per_page=100",
                      f"/repos/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page=50",
                      f"/repos/{repo}/pulls?state=all&per_page=100",
                      f"/repos/{repo}/issues/comments?since={self.since}&sort=created&direction=desc&per_page=100",
                      f"/repos/{repo}/pulls/comments?since={self.since}&sort=created&direction=desc&per_page=100"]
        got = self.prefetch(paths)
        fetched = lambda path: got[path] if path in got else self.gh.get(path)
        for repo, _ in cfg.repos:
            for p in fetched(f"/repos/{repo}/pulls?state=open&per_page=100"):
                if p["user"]["login"] == cfg.author:
                    ref = cfg.ref(repo, p["number"])
                    open_now[ref] = (repo, p)
                    if self.heads.get(ref) not in (None, p["head"]["sha"]):
                        self.sha_seen[ref] = [p["head"]["sha"], time.time()]
                        SHA_SEEN.parent.mkdir(parents=True, exist_ok=True)
                        write_json(SHA_SEEN, self.sha_seen)
                    self.heads[ref] = p["head"]["sha"]
            if True:
                self.merged[repo] = [
                    {"ref": cfg.ref(repo, p["number"]), "at": p["merged_at"]}
                    for p in fetched(f"/repos/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page=50")
                    if p["user"]["login"] == cfg.author and p.get("merged_at")]
                for p in fetched(f"/repos/{repo}/pulls?state=all&per_page=100"):
                    if p["user"]["login"] == cfg.author:
                        ref = cfg.ref(repo, p["number"])
                        self.known_refs.add(ref)
                        m = cfg.issue_pattern.search(p["head"]["ref"]) or cfg.issue_pattern.search(p["title"])
                        if m:
                            self.issue_to_ref[m.group(1)] = ref
            for c in fetched(f"/repos/{repo}/issues/comments?since={self.since}&sort=created&direction=desc&per_page=100"):
                if c["user"]["login"] == reviewer:
                    ref = cfg.ref(repo, int(c["issue_url"].rsplit("/", 1)[1]))
                    self.note_reviewer(ref, f"c{c['id']}", c["created_at"], "commented")
            for c in fetched(f"/repos/{repo}/pulls/comments?since={self.since}&sort=created&direction=desc&per_page=100"):
                ref = cfg.ref(repo, int(c["pull_request_url"].rsplit("/", 1)[1]))
                if c["user"]["login"] == reviewer:
                    self.note_reviewer(ref, f"c{c['id']}", c["created_at"], "commented")
        for ref, (repo, p) in open_now.items():
            if self.updated.get(ref) == p["updated_at"]:
                continue
            self.updated[ref] = p["updated_at"]
            head = self.gh.get(f"/repos/{repo}/commits/{p['head']['sha']}")
            self.push_time[ref] = head["commit"]["committer"]["date"]
            for r in self.gh.get(f"/repos/{repo}/pulls/{p['number']}/reviews?per_page=100"):
                if r["user"]["login"] == reviewer and r.get("submitted_at"):
                    self.reviewer_review[ref] = max(self.reviewer_review.get(ref, ("", "")), (r["submitted_at"], r["state"]))
                    kind = {"APPROVED": "approved", "CHANGES_REQUESTED": "asked changes"}.get(r["state"], "commented")
                    self.note_reviewer(ref, f"r{r['id']}", r["submitted_at"], kind)
        self.first_pass = False
        self.tick += 1
        self.open_now = open_now
        return open_now

    def slack_seen(self, ref, who="us"):
        source = self.slack.get("_reviewer", {}) if who == "reviewer" else self.slack
        seen = source.get(ref, 0)
        bare = ref.lstrip("abcdefghijklmnopqrstuvwxyz")
        if bare != ref and bare not in self.known_refs:
            seen = max(seen, source.get(bare, 0))
        return seen

    def reviewer_said(self, ref):
        said = list(self.gfacts.get(ref, {}).get("messages", []))
        texts = self.slack.get("_said") or {}
        bare = ref.lstrip("abcdefghijklmnopqrstuvwxyz")
        for key in [ref] + ([bare] if bare != ref and bare not in self.known_refs else []):
            for ts, text in (texts.get(key) or {}).items():
                said.append({"id": f"slack:{ts}", "at": float(ts), "state": "SLACK", "text": text})
        return sorted(said, key=lambda m: (m["at"], m["id"]))

    def facts(self, ref, p):
        g = self.gfacts.get(ref, {})
        seen = self.sha_seen.get(ref) or ["", 0]
        requested = self.cfg.reviewer.get("login") in {r["login"] for r in p.get("requested_reviewers", [])}
        pushed = max(iso_ts(self.push_time.get(ref, "")), iso_ts(g.get("pushed", "")), iso_ts(g.get("forced", "")),
                     seen[1] if seen[0] == p.get("head", {}).get("sha") else 0)
        said = self.reviewer_said(ref)
        slack = self.slack_seen(ref)
        asked = iso_ts(g.get("req_at", ""))
        req_at = asked if requested else 0.0
        if requested and not req_at:
            req_at = max([m["at"] for m in said] + [pushed, 1.0])
        handover = max(slack, req_at)
        verdict, verdict_at, pending = None, 0.0, False
        for m in said:
            if m["at"] <= handover:
                continue
            label = self.intent.label(m) if getattr(self, "intent", None) else FORCED_INTENT.get(m["state"], "ACTION")
            if label is None:
                pending = True
            elif label in ("ACTION", "DECIDES", "PARK"):
                verdict, verdict_at = label, m["at"]
        return {"pushed": pushed, "approved": bool(g.get("approved")), "reviewer": verdict_at, "verdict": verdict,
                "pending": pending, "slack": slack, "requested": requested, "req_at": req_at, "asked": asked,
                "handover": handover, "threads": self.threads.get(ref, 0), "conflict": bool(self.conflict.get(ref)),
                "draft": bool(p.get("draft"))}

    def events(self, ref, p):
        f = self.facts(ref, p)
        return {"push": f["pushed"], "reviewer": f["reviewer"], "slack": f["slack"], "handover": max(f["slack"], f["asked"])}

    def review_state(self, ref, p):
        f = self.facts(ref, p)
        override = status_label(ref)
        if ref in load_json(DATA / "paused.json", []):
            status, why = "paused", "hand-set pause"
        elif override in ("fix", "waiting", "decides"):
            status, why = override, "hand-set in status.json"
        elif f["draft"]:
            status, why = "draft", "draft PR"
        elif f["conflict"]:
            status, why = "conflict", "conflicts with main"
        elif f["approved"] and f["threads"] == 0:
            status, why = "approved", "the reviewer's latest verdict approves the current head"
        elif f["verdict"] == "ACTION":
            status, why = "fix", "the reviewer asked for something after our last hand-over"
        elif f["verdict"] == "DECIDES":
            status, why = "decides", "the product owner's decision is pending"
        elif f["verdict"] == "PARK" and f["pushed"] > f["reviewer"]:
            status, why = "fix", "we pushed after the reviewer parked it"
        elif f["verdict"] == "PARK":
            status, why = "paused", "the reviewer parked it"
        elif f["threads"] > 0 and f["slack"] <= f["pushed"]:
            status, why = "fix", "open threads, not announced since the last push"
        elif f["handover"]:
            status, why = "waiting", "handed over, reviewer silent since"
        else:
            status, why = "prep", "never handed over"
        since = 0
        if status == "waiting":
            since = int(max(f["handover"], f["pushed"]))
        return {"ref": ref, "status": status, "since": since, "why": why}


def local_next(cfg, known):
    found, cutoff = {}, time.time() - 3 * 86400
    for clone in cfg.clones:
        out = subprocess.run(["git", "-C", str(clone), "for-each-ref", "--format=%(refname:short) %(committerdate:unix)", "refs/heads/"],
                             capture_output=True, text=True).stdout
        for line in out.splitlines():
            name, _, ts = line.rpartition(" ")
            m = cfg.branch_pattern.search(name)
            if m and int(ts) > cutoff and m.group(1) not in known:
                found[m.group(1)] = max(found.get(m.group(1), 0), int(ts))
    return [i for i, _ in sorted(found.items(), key=lambda kv: -kv[1])][:5]


def labels(pulls, open_now):
    try:
        expire_hand_set(pulls)
    except Exception as e:
        print("hand-set:", e, flush=True)
    behind = lambda ref, p: pulls.gfacts.get(ref, {}).get("updated", "") != p["updated_at"] or pulls.gfacts.get(ref, {}).get("head") != p["head"]["sha"]
    for ref, (repo, p) in open_now.items():
        if behind(ref, p):
            try:
                _, pulls.threads[ref], _, conflict, pulls.gfacts[ref] = pr_health(pulls, repo, p["number"])
                if conflict is not None:
                    pulls.conflict[ref] = conflict
            except Exception as e:
                print("checks:", ref, e, flush=True)
    before = {r["ref"]: r for r in state.get("review") or []}
    out = []
    for ref, (_, p) in sorted(open_now.items(), key=lambda kv: kv[1][1]["created_at"]):
        unsure = behind(ref, p) or pulls.facts(ref, p)["pending"]
        if unsure and ref in before:
            if ref not in pulls.held:
                pulls.held[ref] = time.time()
                log_label(f"{ref} held at {before[ref]['status']}: " + ("REST and GraphQL disagree" if behind(ref, p) else "reading the reviewer's latest message"))
            if time.time() - pulls.held[ref] < 100:
                out.append(before[ref])
                continue
        if pulls.held.pop(ref, None) and unsure:
            log_label(f"{ref} released after 100 s on unsure data")
        label = pulls.review_state(ref, p)
        if before.get(ref, {}).get("status") != label["status"]:
            log_label(f"{ref} {before.get(ref, {}).get('status')} -> {label['status']} ({label['why']})")
        out.append(label)
    return out


def github_loop(cfg, pulls):
    while True:
        try:
            open_now = pulls.poll()
            merged = sorted((m for ms in pulls.merged.values() for m in ms), key=lambda m: m["at"], reverse=True)
            week = time.time() - 7 * 86400
            recent = time.time() - 24 * 3600
            alert = pulls.alert if pulls.alert and pulls.alert["until"] > time.time() else None
            review = labels(pulls, open_now)
            fields = {
                "review": review,
                "merged": [m["ref"] for m in merged if iso_ts(m["at"]) > week][:6],
                "merged_recent": [m["ref"] for m in merged if iso_ts(m["at"]) > recent],
                "next": local_next(cfg, set(pulls.issue_to_ref)),
            }
            publish(alert=alert, gh_ok=int(time.time()), **fields)
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps({**fields, "ask": state["ask"], "ci_fail": state["ci_fail"]}))
        except Exception as e:
            print("github:", e, flush=True)
        time.sleep(5)


SLACK_THREADS = {}
SLACK_STORE = Path.home() / ".cache/pr-table/slack-seen.json"
SLACK_DEEP = {"at": 0.0}


def slack_get(token, method, **params):
    req = urllib.request.Request(f"https://slack.com/api/{method}?{urllib.parse.urlencode(params)}",
                                 headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = json.loads(r.read())
    if not body.get("ok"):
        raise RuntimeError(f"Slack: {body.get('error')}")
    return body


def slack_refs(cfg, text):
    labels = {label.lower(): label for _, label in cfg.repos}
    links = {repo.lower(): label for repo, label in cfg.repos}
    lead = re.compile(r"\s*(?:PR\s*)?(?:([A-Za-z]*)#(\d+)|<https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)[^>]*>)[\s,&+/:]*")
    first, pos, found = (text or "").split("\n", 1)[0], 0, []
    while True:
        m = lead.match(first, pos)
        if not m or m.end() == pos:
            return found
        if m.group(2) and m.group(1).lower() in labels:
            found.append(labels[m.group(1).lower()] + m.group(2))
        elif m.group(4) and m.group(3).lower() in links:
            found.append(links[m.group(3).lower()] + m.group(4))
        pos = m.end()


def slack_latest(cfg):
    token = os.environ.get(cfg.slack.get("token_env", "SLACK_TOKEN"), "")
    channel = cfg.slack.get("channel", "")
    if not token or not channel:
        return None
    me, reviewer = cfg.slack.get("me", ""), cfg.slack.get("reviewer", "")
    mention = f"<@{reviewer}>" if reviewer else ""
    deep = time.time() - SLACK_DEEP["at"] > 120
    params = {"channel": channel, "limit": 200 if deep else 100}
    if deep:
        params["oldest"] = f"{time.time() - 21 * 86400:.0f}"
    messages = []
    while True:
        body = slack_get(token, "conversations.history", **params)
        messages += body.get("messages", [])
        cursor = (body.get("response_metadata") or {}).get("next_cursor")
        if not deep or not cursor or not body.get("has_more"):
            break
        params["cursor"] = cursor
    if deep:
        SLACK_DEEP["at"] = time.time()
    ours, theirs, said = {}, {}, {}
    bump = lambda table, refs, ts: [table.__setitem__(r, max(table.get(r, 0.0), ts)) for r in refs]
    for m in messages:
        refs = slack_refs(cfg, m.get("text"))
        if not refs:
            continue
        if reviewer and m.get("user") == reviewer:
            bump(theirs, refs, float(m["ts"]))
            for ref in refs:
                said.setdefault(ref, {})[m["ts"]] = m.get("text") or ""
        elif not me or m.get("user") == me:
            bump(ours, refs, float(m["ts"]))
        if not m.get("latest_reply"):
            continue
        if not me and not reviewer:
            bump(ours, refs, float(m["latest_reply"]))
            continue
        key = (m["ts"], m["latest_reply"])
        if key not in SLACK_THREADS:
            SLACK_THREADS[key] = slack_get(token, "conversations.replies", channel=channel, ts=m["ts"], limit=200).get("messages", [])[1:]
        for reply in SLACK_THREADS[key]:
            if reviewer and reply.get("user") == reviewer:
                bump(theirs, refs, float(reply["ts"]))
                for ref in refs:
                    said.setdefault(ref, {})[reply["ts"]] = reply.get("text") or ""
            elif (not me or reply.get("user") == me) and (not mention or mention in (reply.get("text") or "")):
                bump(ours, refs, float(reply["ts"]))
    stored = load_json(SLACK_STORE, {})
    for key in ("us", "reviewer", "said"):
        stored.setdefault(key, {})
    for ref, texts in said.items():
        kept = {**stored["said"].get(ref, {}), **texts}
        stored["said"][ref] = dict(sorted(kept.items(), key=lambda kv: float(kv[0]))[-10:])
    for table, new in ((stored["us"], ours), (stored["reviewer"], theirs)):
        for ref, ts in new.items():
            table[ref] = max(table.get(ref, 0.0), ts)
    SLACK_STORE.parent.mkdir(parents=True, exist_ok=True)
    write_json(SLACK_STORE, stored)
    return {**stored["us"], "_reviewer": stored["reviewer"], "_said": stored["said"]}


def pr_health(pulls, repo, number):
    owner, name = repo.split("/")
    pr = pulls.gh.graphql(GRAPHQL_PR, o=owner, r=name, n=number)["repository"]["pullRequest"]
    threads = sum(not t["isResolved"] for t in pr["reviewThreads"]["nodes"])
    commit = pr["commits"]["nodes"][0]["commit"] if pr["commits"]["nodes"] else {}
    contexts = ((commit.get("statusCheckRollup") or {}).get("contexts") or {}).get("nodes", [])
    green, failing = True, False
    for c in contexts:
        if not c.get("isRequired"):
            continue
        if c["__typename"] == "CheckRun":
            green &= c["status"] == "COMPLETED" and c["conclusion"] in ("SUCCESS", "SKIPPED", "NEUTRAL")
            failing |= c["conclusion"] in ("FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE")
        else:
            green &= c["state"] == "SUCCESS"
            failing |= c["state"] in ("FAILURE", "ERROR")
    login = pulls.cfg.reviewer.get("login")
    asked = [e["createdAt"] for e in pr["asked"]["nodes"] if (e.get("requestedReviewer") or {}).get("login") == login]
    verdict = next((r for r in pr["opinion"]["nodes"] if (r.get("author") or {}).get("login") == login), None)
    facts = {"updated": pr["updatedAt"], "head": pr["headRefOid"], "pushed": commit.get("committedDate", ""),
             "forced": max((e["createdAt"] for e in pr["forced"]["nodes"] if e), default=""),
             "approved": bool(verdict and verdict["state"] == "APPROVED" and (verdict.get("commit") or {}).get("oid") == pr["headRefOid"]),
             "messages": reviewer_messages(pr, login),
             "req_at": max(asked, default="")}
    return green, threads, failing, {"CONFLICTING": True, "MERGEABLE": False}.get(pr.get("mergeable")), facts


FORCED_INTENT = {"CHANGES_REQUESTED": "ACTION", "APPROVED": "ACTION"}
INTENT_LABELS = ("ACTION", "DECIDES", "PARK", "WAIT", "ACK")
INTENT_CACHE = Path.home() / ".cache/pr-table/intent.json"


class Intent:
    def __init__(self, command):
        self.command = command
        self.cache = load_json(INTENT_CACHE, {})
        self.first_seen, self.queued = {}, set()
        self.jobs = __import__("queue").Queue()
        self.lock = threading.Lock()
        for _ in range(3):
            threading.Thread(target=self.worker, daemon=True).start()

    def label(self, m):
        if m["state"] in FORCED_INTENT:
            return FORCED_INTENT[m["state"]]
        if not m["text"]:
            return "ACK"
        if m["id"] in self.cache:
            return self.cache[m["id"]]
        first = self.first_seen.setdefault(m["id"], time.time())
        with self.lock:
            if m["id"] not in self.queued:
                self.queued.add(m["id"])
                self.jobs.put(m)
        return "ACTION" if time.time() - first > 90 else None

    def vote(self, prompt):
        try:
            out = subprocess.run(self.command + [prompt], capture_output=True, text=True, timeout=120).stdout.strip()
        except Exception:
            return None
        word = (out.split() or [""])[-1].strip(".:*`").upper()
        return word if word in INTENT_LABELS else None

    def worker(self):
        while True:
            m = self.jobs.get()
            prompt = (ROOT / "prompts" / "reviewer-intent.txt").read_text().replace("{text}", m["text"][:8000])
            with ThreadPoolExecutor(3) as pool:
                votes = [v for v in pool.map(lambda _: self.vote(prompt), range(3)) if v]
            if votes and max(votes.count(v) for v in votes) < 2:
                with ThreadPoolExecutor(2) as pool:
                    votes += [v for v in pool.map(lambda _: self.vote(prompt), range(2)) if v]
            label = max(INTENT_LABELS, key=lambda l: (votes.count(l), -INTENT_LABELS.index(l))) if votes else None
            with self.lock:
                if label:
                    self.cache[m["id"]] = label
                    INTENT_CACHE.parent.mkdir(parents=True, exist_ok=True)
                    write_json(INTENT_CACHE, self.cache)
                self.queued.discard(m["id"])
            log_label(f"read reviewer message {m['id']} as {label or 'unreadable'} (votes {' '.join(votes)}): {m['text'][:80]!r}")
            if not label:
                time.sleep(30)


def reviewer_messages(pr, login):
    said = []
    for r in pr["reviews"]["nodes"]:
        if (r.get("author") or {}).get("login") == login and r.get("submittedAt"):
            text = "\n\n".join([r.get("body") or ""] + [c.get("body") or "" for c in r["comments"]["nodes"]]).strip()
            said.append({"id": r["id"], "at": iso_ts(r["submittedAt"]), "state": r["state"], "text": text})
    for c in pr["comments"]["nodes"]:
        if (c.get("author") or {}).get("login") == login:
            said.append({"id": c["id"], "at": iso_ts(c["createdAt"]), "state": "COMMENT", "text": (c.get("body") or "").strip()})
    return said


def ask_verdict(cfg, pulls, ref, repo, p, slack, verdicts):
    requested = {r["login"] for r in p.get("requested_reviewers", [])}
    facts = pulls.facts(ref, p)
    posted_since_push = slack is not None and facts["pushed"] and pulls.slack_seen(ref) > facts["pushed"]
    if status_label(ref) == "ask":
        if posted_since_push:
            return False, "already posted after the last push", True
        return True, "forced by status.json", False
    if p.get("draft"):
        return False, "draft", True
    if ref in load_json(DATA / "paused.json", []):
        return False, "paused", True
    if facts["pending"]:
        return False, "reading the reviewer's latest message", False
    if facts["verdict"]:
        return False, f"the reviewer's latest message after our hand-over reads as {facts['verdict']}", True
    if cfg.reviewer.get("login") not in requested:
        return False, "reviewer not requested", True
    if cfg.ready_marker and cfg.ready_marker.lower() not in (p.get("body") or "").lower():
        return False, "ready marker missing", True
    if not facts["pushed"]:
        return False, "push time unknown", False
    if posted_since_push:
        return False, "already posted after the last push", True
    key = (p["head"]["sha"], p["updated_at"])
    cached = verdicts.get(ref)
    if not cached or cached[0] != key or time.time() - cached[1] > 120:
        verdicts[ref] = (key, time.time(), *pr_health(pulls, repo, p["number"])[:2])
    _, _, green, threads = verdicts[ref]
    if not green:
        return False, "required checks not green", False
    if threads:
        return False, f"{threads} unresolved thread(s)", False
    return True, "all conditions met", False


def ask_loop(cfg, pulls):
    slack, slack_at, verdicts, strikes = None, 0, {}, {}
    while pulls.tick == 0:
        time.sleep(1)
    shown = set(state.get("ask") or [])
    while True:
        try:
            if time.time() - slack_at > (10 if shown else 20):
                slack, slack_at = slack_latest(cfg), time.time()
                pulls.slack = slack or {}
            keep = set()
            for ref, (repo, p) in list(pulls.open_now.items()):
                try:
                    ok, why, firm = ask_verdict(cfg, pulls, ref, repo, p, slack, verdicts)
                except Exception as e:
                    ok, why, firm = ref in shown, f"check failed: {e}", False
                if ok:
                    strikes.pop(ref, None)
                    keep.add(ref)
                elif ref in shown and not firm and strikes.get(ref, 0) < 2:
                    strikes[ref] = strikes.get(ref, 0) + 1
                    keep.add(ref)
                if (ref in keep) != (ref in shown):
                    ASK_LOG.parent.mkdir(parents=True, exist_ok=True)
                    with ASK_LOG.open("a") as log:
                        log.write(f"{datetime.now():%F %T} {ref} {'ON' if ref in keep else 'OFF'} {why}\n")
            shown = keep
            publish(ask=sorted(shown))
        except Exception as e:
            print("ask:", e, flush=True)
        time.sleep(5 if shown else 30)


def threads_loop(pulls):
    while pulls.tick == 0:
        time.sleep(1)
    failing = set(state.get("ci_fail") or [])
    while True:
        for ref, (repo, p) in list(pulls.open_now.items()):
            try:
                _, pulls.threads[ref], broken, conflict, pulls.gfacts[ref] = pr_health(pulls, repo, p["number"])
                if conflict is not None:
                    pulls.conflict[ref] = conflict
                (failing.add if broken else failing.discard)(ref)
            except Exception as e:
                print("checks:", ref, e, flush=True)
        failing &= set(pulls.open_now)
        publish(ci_fail=sorted(failing))
        time.sleep(15)


def table_visible():
    try:
        clients = json.loads(subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, text=True, timeout=5).stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    win = next((c for c in clients if c.get("title", "").startswith("PR Table")), None)
    return bool(win) and win["workspace"]["name"] != "special:prtable"


def visible_loop():
    while True:
        seen = table_visible()
        if seen is not None:
            publish(visible=seen)
        time.sleep(1)


def find_session(cfg):
    workspace = cfg.claude.get("hyprland_workspace")
    if cfg.claude.get("session_file"):
        return Path(os.path.expanduser(cfg.claude["session_file"]))
    projects = Path.home() / ".claude/projects"
    if workspace is not None:
        try:
            clients = json.loads(subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, text=True).stdout)
        except (OSError, ValueError):
            clients = []
        stack, seen = [c["pid"] for c in clients if c["workspace"]["id"] == int(workspace)], set()
        while stack:
            pid = stack.pop()
            if pid in seen:
                continue
            seen.add(pid)
            try:
                argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
                stack += [int(c) for c in Path(f"/proc/{pid}/task/{pid}/children").read_text().split()]
            except OSError:
                continue
            if argv and os.path.basename(argv[0].decode()) == "claude":
                project = projects / re.sub(r"[^A-Za-z0-9]", "-", os.readlink(f"/proc/{pid}/cwd"))
                files = sorted(project.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
                return files[0] if files else None
        return None
    files = sorted(projects.glob("*/*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
    return files[0] if files else None


REF_RX = re.compile(r"\b([A-Za-z]*)#(\d{1,5})\b")


class Transcript:
    def __init__(self, cfg, pulls):
        self.cfg, self.pulls = cfg, pulls
        self.path, self.offset, self.last_write = None, 0, 0
        self.focus, self.doing, self.agents = None, "", {}

    def refs_in(self, text):
        known, labels, out = self.pulls.known_refs, {l.lower(): l for _, l in self.cfg.repos}, []
        for m in REF_RX.finditer(text):
            label = labels.get(m.group(1).lower(), "")
            ref = f"{label}{m.group(2)}"
            if ref not in known:
                ref = next((f"{l}{m.group(2)}" for l in labels.values() if f"{l}{m.group(2)}" in known), ref)
            if ref in known:
                out.append((m.start(), ref))
        for m in self.cfg.issue_pattern.finditer(text):
            ref = self.pulls.issue_to_ref.get(m.group(1))
            if ref:
                out.append((m.start(), ref))
        return [r for _, r in sorted(out)]

    def attach(self, path):
        if path == self.path:
            return
        self.path, self.focus, self.doing, self.agents = path, None, "", {}
        self.offset = max(0, path.stat().st_size - 40_000_000)
        self.read(skip_partial=self.offset > 0)

    def read(self, skip_partial=False):
        with open(self.path, "rb") as f:
            f.seek(self.offset)
            data = f.read()
        if skip_partial:
            nl = data.find(b"\n")
            data, self.offset = data[nl + 1:], self.offset + nl + 1
        end = data.rfind(b"\n")
        if end < 0:
            return
        chunk, self.offset = data[:end], self.offset + end + 1
        for line in chunk.splitlines():
            try:
                self.event(json.loads(line))
            except ValueError:
                continue

    def event(self, d):
        content = (d.get("message") or {}).get("content")
        if d.get("type") == "assistant" and isinstance(content, list):
            for c in content:
                if c.get("type") == "tool_use":
                    inp = c.get("input", {})
                    if inp.get("description"):
                        self.doing = inp["description"]
                    refs = self.refs_in(json.dumps(inp))
                    if refs:
                        self.focus = refs[-1]
                    if c.get("name") in ("Agent", "Task"):
                        self.agents[c["id"]] = {"text": inp.get("description", "agent"), "agent": None}
                elif c.get("type") == "text":
                    refs = self.refs_in(c.get("text", ""))
                    if refs:
                        self.focus = refs[-1]
        elif d.get("type") == "user":
            if isinstance(content, list):
                for c in content:
                    if c.get("type") == "tool_result" and c.get("tool_use_id") in self.agents:
                        body = c.get("content")
                        body = body if isinstance(body, str) else json.dumps(body)
                        m = re.search(r"agentId: ([0-9a-f]+)", body)
                        if m:
                            self.agents[c["tool_use_id"]]["agent"] = m.group(1)
                        else:
                            self.agents.pop(c["tool_use_id"], None)
            elif isinstance(content, str) and "<task-notification>" in content:
                m = re.search(r"<tool-use-id>(\S+?)</tool-use-id>", content)
                s = re.search(r"<status>(\w+)</status>", content)
                if m and s and s.group(1) != "running":
                    self.agents.pop(m.group(1), None)

    def agent_file(self, agent_id):
        return self.path.with_suffix("") / "subagents" / f"agent-{agent_id}.jsonl"

    def agent_doing(self, agent_id):
        f = self.agent_file(agent_id)
        try:
            with open(f, "rb") as fh:
                fh.seek(max(0, f.stat().st_size - 400_000))
                tail = fh.read().splitlines()
        except OSError:
            return ""
        for line in reversed(tail):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            for c in (d.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("input", {}).get("description"):
                    return c["input"]["description"]
        return ""

    def step(self):
        if not self.pulls.known_refs:
            return
        path = find_session(self.cfg)
        if not path or not path.exists():
            return
        self.attach(path)
        mtime = path.stat().st_mtime
        if mtime != self.last_write:
            self.last_write = mtime
            self.read()
        agents = []
        for k, v in self.agents.items():
            try:
                alive = v["agent"] and time.time() - self.agent_file(v["agent"]).stat().st_mtime < 180
            except OSError:
                alive = False
            if alive:
                agents.append({"id": k, "text": v["text"], "doing": self.agent_doing(v["agent"])})
        publish(focus=self.focus, doing=self.doing, active=time.time() - mtime < 90, agents=agents)


def transcript_loop(t):
    while True:
        try:
            t.step()
        except Exception as e:
            print("transcript:", e, flush=True)
        time.sleep(0.5)


def demo_loop():
    actions = [
        "Read the failing test output", "Run the unit tests for the parser", "Fix the null check in the importer",
        "Push the fix and wait for CI", "Reply to the review comment", "Search the codebase for the old flag",
        "Update the migration script", "Re-run the flaky integration test",
    ]
    review = [{"ref": "412", "status": "ask"}, {"ref": "409", "status": "fix"}, {"ref": "w88", "status": "waiting"},
              {"ref": "405", "status": "decides"}, {"ref": "w86", "status": "draft"}]
    merged = ["401", "w84", "398", "395", "w80", "392"]
    nxt, number, tick = ["131", "128", "127"], 413, 0
    publish(review=[{**r, "status": "waiting" if r["status"] == "ask" else r["status"]} for r in review],
            merged=merged, next=nxt, ask=["412"], merged_recent=["401"])
    while True:
        tick += 1
        publish(focus="409", doing=random.choice(actions), active=True, gh_ok=int(time.time()),
                agents=[{"id": "a1", "text": "Fix the importer edge case", "doing": random.choice(actions)},
                        {"id": "a2", "text": "Write the release notes", "doing": random.choice(actions)}])
        if tick % 45 == 0:
            ref = str(number)
            number += 1
            merged = [ref] + merged[:5]
            publish(merged=merged, merged_recent=[ref])
        if tick % 70 == 20:
            publish(alert={"ref": "409", "kind": "commented", "until": time.time() + 12})
        if tick % 70 == 32:
            publish(alert=None)
        if tick % 70 == 52:
            publish(ci_fail=["w88"])
        if tick % 70 == 66:
            publish(ci_fail=[])
        time.sleep(1)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        route, _, query = self.path.partition("?")
        if route == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            seen = -1
            try:
                while True:
                    with changed:
                        changed.wait_for(lambda: state["version"] != seen, timeout=15)
                        snap, seen = json.dumps(state), state["version"]
                    self.wfile.write(f"data: {snap}\n\n".encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
        if route == "/state":
            with changed:
                body = json.dumps(state).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if route == "/visible":
            publish(visible=query != "on=0")
            self.send_response(204)
            self.end_headers()
            return
        if route == "/pointer":
            publish(pointer=time.time())
            self.send_response(204)
            self.end_headers()
            return
        if route == "/celebrate":
            params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
            ref = re.sub(r"[^A-Za-z0-9]", "", params.get("ref", "")) or (state["merged"][0] if state["merged"] else "PR")
            publish(party={"ref": ref, "id": time.time()})
            self.send_response(204)
            self.end_headers()
            return
        name = "index.html" if route in ("/", "/index.html") else route.lstrip("/")
        f = (WEB / name).resolve()
        if WEB not in f.parents or not f.is_file():
            self.send_error(404)
            return
        body = f.read_bytes()
        types = {"html": "text/html", "js": "text/javascript", "css": "text/css", "png": "image/png", "svg": "image/svg+xml", "webp": "image/webp", "mp3": "audio/mpeg"}
        self.send_response(200)
        self.send_header("Content-Type", types.get(f.suffix[1:], "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


def main():
    ap = argparse.ArgumentParser(description="PR Table: a live wall display of your pull requests")
    ap.add_argument("--config", default=str(ROOT / "config.json"))
    ap.add_argument("--demo", action="store_true", help="run with fake data, no GitHub needed")
    ap.add_argument("--port", type=int)
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to show the table on a TV or Raspberry Pi on your network")
    args = ap.parse_args()
    ThreadingHTTPServer.daemon_threads = True
    if args.demo:
        publish(ui={"title": "PR TABLE", "reviewer": "Alex", "reviewer_figure": "assets/crew/crew-1.png",
                    "crew": [f"assets/crew/crew-{i}.png" for i in range(1, 5)]})
        threading.Thread(target=demo_loop, daemon=True).start()
        port = args.port or 4950
    else:
        cfg = Config(args.config)
        publish(ui=cfg.ui, **{k: v for k, v in load_json(CACHE, {}).items() if k in state})
        pulls = Pulls(cfg)
        stored = load_json(SLACK_STORE, {})
        pulls.slack = {**stored.get("us", {}), "_reviewer": stored.get("reviewer", {}), "_said": stored.get("said", {})}
        if cfg.intent_command:
            pulls.intent = Intent(cfg.intent_command)
        threading.Thread(target=github_loop, args=(cfg, pulls), daemon=True).start()
        threading.Thread(target=ask_loop, args=(cfg, pulls), daemon=True).start()
        threading.Thread(target=threads_loop, args=(pulls,), daemon=True).start()
        if shutil.which("hyprctl"):
            threading.Thread(target=visible_loop, daemon=True).start()
        threading.Thread(target=transcript_loop, args=(Transcript(cfg, pulls),), daemon=True).start()
        port = args.port or cfg.port
    print(f"PR Table on http://{args.host}:{port}", flush=True)
    ThreadingHTTPServer((args.host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
