import base64
import json
import os
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = json.loads((ROOT / "scripts" / "prompts.json").read_text())
MODEL = os.environ.get("IMAGE_MODEL", "gpt-image-2.6-sunburst")


def generate(name, prompt, size, transparent, out_dir):
    body = {"model": MODEL, "prompt": prompt, "size": size, "quality": "high", "n": 1}
    if transparent:
        body["background"] = "transparent"
        body["output_format"] = "png"
    req = urllib.request.Request("https://api.openai.com/v1/images/generations", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        data = json.loads(r.read())["data"][0]["b64_json"]
    path = out_dir / f"{name}.png"
    path.write_bytes(base64.b64decode(data))
    return path


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    jobs = []
    if which in ("all", "crew"):
        out = ROOT / "web" / "assets" / "crew"
        out.mkdir(parents=True, exist_ok=True)
        for name, desc in PROMPTS["crew"].items():
            jobs.append((name, PROMPTS["crew_base"].format(desc=desc), "1024x1536", True, out))
    if which in ("all", "gallery"):
        out = ROOT / "launch" / "gallery"
        out.mkdir(parents=True, exist_ok=True)
        for name, prompt in PROMPTS["gallery"].items():
            jobs.append((name, prompt, "1536x1024", False, out))
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        for path in pool.map(lambda j: generate(*j), jobs):
            print(path)


if __name__ == "__main__":
    main()
