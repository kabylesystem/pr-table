import argparse
import json
import os
import random
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
DATA = ROOT / "data"
CACHE = Path.home() / ".cache/pr-table/last.json"
ASK_LOG = Path.home() / ".cache/pr-table/ask.log"
ALERT_SECONDS = 45
GRAPHQL_PR = """query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n){
reviewThreads(first:100){nodes{isResolved}}
commits(last:1){nodes{commit{statusCheckRollup{contexts(first:100){nodes{__typename
... on CheckRun{status conclusion isRequired(pullRequestNumber:$n)}
... on StatusContext{state isRequired(pullRequestNumber:$n)}}}}}}}}}}"""

lock = threading.Lock()
changed = threading.Condition(lock)
state = {
    "focus": None, "doing": "", "active": False, "agents": [],
    "next": [], "review": [], "merged": [], "merged_recent": [], "ask": [],
    "alert": None, "party": None, "gh_ok": 0, "version": 0, "ci_fail": [],
    "build": str(int(time.time())), "ui": {},
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
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


class Config:
    def __init__(self, path):
        raw = load_json(path, None)
        if raw is None:
            raise SystemExit(f"Missing or invalid config: {path} (copy config.example.json to config.json)")
        self.author = raw["github"]["author"]
        self.repos = [(r["name"], r.get("label", "")) for r in raw["github"]["repos"]]
        self.reviewer = raw.get("reviewer", {})
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

    def poll(self):
        cfg, open_now = self.cfg, {}
        reviewer = cfg.reviewer.get("login", "")
        for repo, _ in cfg.repos:
            for p in self.gh.get(f"/repos/{repo}/pulls?state=open&per_page=100"):
                if p["user"]["login"] == cfg.author:
                    open_now[cfg.ref(repo, p["number"])] = (repo, p)
            if self.tick % 3 == 0:
                self.merged[repo] = [
                    {"ref": cfg.ref(repo, p["number"]), "at": p["merged_at"]}
                    for p in self.gh.get(f"/repos/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page=50")
                    if p["user"]["login"] == cfg.author and p.get("merged_at")]
                for p in self.gh.get(f"/repos/{repo}/pulls?state=all&per_page=100"):
                    if p["user"]["login"] == cfg.author:
                        ref = cfg.ref(repo, p["number"])
                        self.known_refs.add(ref)
                        m = cfg.issue_pattern.search(p["head"]["ref"]) or cfg.issue_pattern.search(p["title"])
                        if m:
                            self.issue_to_ref[m.group(1)] = ref
            for c in self.gh.get(f"/repos/{repo}/issues/comments?since={self.since}&sort=created&direction=desc&per_page=100"):
                if c["user"]["login"] == reviewer:
                    ref = cfg.ref(repo, int(c["issue_url"].rsplit("/", 1)[1]))
                    self.note_reviewer(ref, f"c{c['id']}", c["created_at"], "commented")
            for c in self.gh.get(f"/repos/{repo}/pulls/comments?since={self.since}&sort=created&direction=desc&per_page=100"):
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

    def review_state(self, ref, p):
        pushed = self.push_time.get(ref, "")
        requested = {r["login"] for r in p.get("requested_reviewers", [])}
        override = load_json(DATA / "status.json", {}).get(ref)
        if ref in load_json(DATA / "paused.json", []):
            status = "paused"
        elif override in ("fix", "waiting", "decides"):
            status = override
        elif p.get("draft"):
            status = "draft"
        elif self.threads.get(ref, 0) > 0 or self.reviewer_review.get(ref, ("", ""))[1] == "CHANGES_REQUESTED" and self.reviewer_review[ref][0] > pushed:
            status = "fix"
        elif self.cfg.reviewer.get("login") in requested:
            status = "waiting"
        elif self.reviewer_review.get(ref) and pushed > self.reviewer_review[ref][0]:
            status = "fix"
        else:
            status = "waiting"
        return {"ref": ref, "status": status}


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


def github_loop(cfg, pulls):
    while True:
        try:
            open_now = pulls.poll()
            merged = sorted((m for ms in pulls.merged.values() for m in ms), key=lambda m: m["at"], reverse=True)
            week = time.time() - 7 * 86400
            recent = time.time() - 3 * 3600
            alert = pulls.alert if pulls.alert and pulls.alert["until"] > time.time() else None
            review = [pulls.review_state(ref, p) for ref, (_, p) in sorted(open_now.items(), key=lambda kv: kv[1][1]["created_at"])]
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
        time.sleep(10)


def slack_latest(cfg):
    token = os.environ.get(cfg.slack.get("token_env", "SLACK_TOKEN"), "")
    channel = cfg.slack.get("channel", "")
    if not token or not channel:
        return None
    req = urllib.request.Request(f"https://slack.com/api/conversations.history?channel={channel}&limit=100",
                                 headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = json.loads(r.read())
    if not body.get("ok"):
        raise RuntimeError(f"Slack: {body.get('error')}")
    labels = {label for _, label in cfg.repos}
    latest = {}
    for m in body.get("messages", []):
        hit = re.match(r"\s*(?:PR\s*)?([A-Za-z]*)#(\d+)", m.get("text") or "")
        if not hit or hit.group(1).lower() not in {l.lower() for l in labels} | {""}:
            continue
        label = next((l for l in labels if l.lower() == hit.group(1).lower()), "")
        ref = f"{label}{hit.group(2)}"
        ts = max(float(m.get("ts", 0)), float(m.get("latest_reply") or 0))
        latest[ref] = max(latest.get(ref, 0.0), ts)
    return latest


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
    return green, threads, failing


def ask_verdict(cfg, pulls, ref, repo, p, slack, verdicts):
    requested = {r["login"] for r in p.get("requested_reviewers", [])}
    if p.get("draft"):
        return False, "draft", True
    if ref in load_json(DATA / "paused.json", []):
        return False, "paused", True
    if cfg.reviewer.get("login") not in requested:
        return False, "reviewer not requested", True
    if cfg.ready_marker and cfg.ready_marker.lower() not in (p.get("body") or "").lower():
        return False, "ready marker missing", True
    pushed = pulls.push_time.get(ref)
    if not pushed:
        return False, "push time unknown", False
    if slack is not None and slack.get(ref, 0) > iso_ts(pushed):
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
            if time.time() - slack_at > (10 if shown else 120):
                slack, slack_at = slack_latest(cfg), time.time()
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
                _, pulls.threads[ref], broken = pr_health(pulls, repo, p["number"])
                (failing.add if broken else failing.discard)(ref)
            except Exception as e:
                print("checks:", ref, e, flush=True)
        failing &= set(pulls.open_now)
        publish(ci_fail=sorted(failing))
        time.sleep(30)


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
        threading.Thread(target=github_loop, args=(cfg, pulls), daemon=True).start()
        threading.Thread(target=ask_loop, args=(cfg, pulls), daemon=True).start()
        threading.Thread(target=threads_loop, args=(pulls,), daemon=True).start()
        threading.Thread(target=transcript_loop, args=(Transcript(cfg, pulls),), daemon=True).start()
        port = args.port or cfg.port
    print(f"PR Table on http://{args.host}:{port}", flush=True)
    ThreadingHTTPServer((args.host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
