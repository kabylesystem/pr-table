# Product Hunt launch kit

## Name

PR Table

## Tagline (60 characters max)

**Mission control for teams that ship with AI** (43)

## Description (500 characters max)

> A dedicated screen for your team's pull requests. Plug in GitHub, Slack and Claude Code: giant Next, Review, Merged numbers, live agent activity, the one PR that needs you in red, and a 20 second party on every merge. Free and open source, runs on your own machine, and the demo needs zero setup.

## Launch tags (3 max)

Developer Tools · GitHub · Artificial Intelligence

## Gallery (all 1270×760, in this order)

Everything is in `final/`: `0-thumbnail.png`, then `1-team-wall.jpg`, `2-tour.gif`, `3-merge-party.jpg`, `4-one-red-line.jpg`, `5-plug-in.jpg`, `6-reviewer-alert.jpg`, `7-crew-party.jpg`. Video: upload `final/video-tour.mp4` to YouTube and paste the link.

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
> 🎉 **A merge party**: 20 seconds of 3D "MERGED", confetti and the crew jumping
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
