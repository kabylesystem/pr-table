# Product Hunt launch kit

## Name

PR Table

## Tagline (60 characters max, pick one)

1. **Your PRs on the wall. Confetti when they merge.** (47) ← recommended
2. A stadium scoreboard for your pull requests (43)
3. Know what to do next without opening a tab (42)
4. Your AI agents ship. Your second screen cheers. (47)
5. The only dashboard that throws a party on merge (47)

## Description (260 characters max)

> PR Table turns a spare monitor into a live scoreboard for your pull requests. Huge numbers you can read from the couch, the one PR that needs you in red, your Claude Code agents working in real time, and a full confetti party every time something merges.

(259 characters)

## Topics

Developer Tools · GitHub · Productivity · Artificial Intelligence · Open Source

## Gallery order

1. `gallery/cover.png` (the hook: minifigs partying, title, one line)
2. `gallery/merge.gif` (the merge party, the thing people will share)
3. `gallery/board.png` (the real board: Next · Review · Merged, one red line)
4. `gallery/reviewer-alert.png` (your reviewer commented, from across the room)
5. `gallery/hero-lever.png` (pure fun, the "merge lever")
6. `gallery/demo.mp4` as the video, or upload it to YouTube and paste the link

Thumbnail: `logo-240.png`.

## First comment (post it the second the launch goes live)

> Hey Product Hunt 👋
>
> I ship most of my code with AI agents now. On a normal day I have five PRs open, two agents fixing review comments, and a reviewer in another timezone. I kept alt-tabbing to GitHub just to answer one question: *what do I have to do right now?*
>
> So I put the answer on my second screen, in letters big enough to read from the couch:
>
> 🟢 **Next · Review · Merged**, nothing else
> 🔴 **One red line** when a PR actually needs me (reviewer requested, checks green, no open threads, not posted on Slack since the last push). Everything else says `nothing`.
> 🤖 **Live from Claude Code**: the PR my agent is touching glows, and every sub-agent it spawns gets its own spinning line
> 🎉 **A merge party**: 8 seconds of 3D "MERGED", confetti and the crew jumping
>
> It's free and open source, runs 100% on your machine, and the demo needs zero setup: `python3 server.py --demo`.
>
> I'd love to know: what would *your* team want on the wall? And what should happen on a merge 🎉?

## Follow-up comments (drop one every few hours to keep the thread alive)

- **The rule behind the red line:** "The red line only shows when all five conditions are true. If any check flakes once, it stays. If someone posts on Slack after your last push, it disappears within 15 seconds. Every change is logged with its reason, because a dashboard that lies once gets ignored forever."
- **The crew:** "The minifigs are generated. The prompts are in the repo, and PROMPTS.md shows how to turn your teammates into figures from a photo (ask them first 😅)."
- **For agent users:** "PR Table tails your Claude Code session file locally. It shows the PR your agent is on and every running sub-agent, and nothing leaves your machine."
- **Hidden feature:** "`bin/pr-table party 1234` replays the merge animation on demand. Perfect for showing off to whoever walks by your desk."

## Launch day checklist

- [ ] Repo public, README GIF loads, `python3 server.py --demo` works on a fresh clone
- [ ] Schedule for 00:01 PT (Product Hunt days start at midnight Pacific)
- [ ] Post the first comment immediately
- [ ] Share the merge GIF on X / LinkedIn / Bluesky with the Product Hunt link, in that order, during the first hour
- [ ] Reply to every comment within the hour, all day
