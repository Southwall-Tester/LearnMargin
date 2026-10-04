"""Explicit, opt-in live generation check against a running local LearnMargin app.

This sends examples/probability.md to the configured model and may incur API charges.
No key is read or printed by this script; the application resolves its local config.
"""
import argparse
import json
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--mode", choices=["topics", "pages"], default="topics")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = root / "artifacts" / ("live-api-" + args.mode)
    output.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=args.url, timeout=60) as client:
        settings = client.get("/api/settings").json()
        api = {key: value for key, value in settings["api"].items() if key != "has_api_key"}
        api["vision"] = False
        documents = []
        for name in ("probability.md", "probability-reference.md"):
            with (root / "examples" / name).open("rb") as material:
                response = client.post("/api/documents", files={"file": (name, material, "text/markdown")})
            response.raise_for_status()
            documents.append(response.json())
        scope = {"mode": "topics", "topics": "条件概率与独立性"} if args.mode == "topics" else {
            "mode": "pages", "ranges": {documents[0]["id"]: "1"}}
        response = client.post("/api/jobs", json={"document_ids": [doc["id"] for doc in documents], "api": api,
            "scope": scope,
            "section_count": 2, "layout": "a4", "learner_notes": "基础较弱，解释分母与公式条件。侧栏提示少而具体。"})
        response.raise_for_status()
        job = response.json()
        print(json.dumps({"job_id": job["id"], "model": api["model"]}, ensure_ascii=False), flush=True)
        deadline = time.monotonic() + 900
        stage = None
        while time.monotonic() < deadline:
            job = client.get(f"/api/jobs/{job['id']}").json()
            if job["stage"] != stage:
                print(json.dumps({"stage": job["stage"], "progress": job["progress"]}, ensure_ascii=False), flush=True)
                stage = job["stage"]
            if job["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(2)
        else:
            client.post(f"/api/jobs/{job['id']}/cancel")
            raise SystemExit("Live verification timed out and was cancelled.")
        (output / "result.json").write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        if job["status"] != "completed":
            raise SystemExit(job.get("error") or job["status"])
        for key, filename in (("pdf", "lesson.pdf"), ("json", "lesson.json"), ("source_zip", "sources.zip")):
            response = client.get(job["artifacts"][key])
            response.raise_for_status()
            (output / filename).write_bytes(response.content)
        print(json.dumps({"status": job["status"], "pages": job["page_count"], "output": str(output)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
