"""Publish weekly_video/kuoala_weekly.mp4 as an Instagram Reel via the official Instagram API (Instagram Login).

    python3 instagram_upload.py --dry-run --video-url <https url>    # shows what it would send
    python3 instagram_upload.py --video-url <https url>               # container -> wait FINISHED -> media_publish
    python3 instagram_upload.py --refresh                             # new 60-day token (put it in IG_ACCESS_TOKEN)

Env: IG_USER_ID (the professional account's Instagram user id), IG_ACCESS_TOKEN (long-lived, 60 days, scope
instagram_business_content_publish). Meta fetches the video from a public HTTPS URL; the workflow uses a GitHub
release asset of this public repo. Official API = allowed automation; browser bots on Instagram are not.
Long-lived tokens expire after 60 days: run --refresh about monthly and update the secret (the job warns).
"""
import json
import os
import sys
import time

import requests

API = "https://graph.instagram.com/" + os.environ.get("IG_API_VERSION", "v26.0")
META = "weekly_video/meta.json"


def tok():
    return os.environ["IG_ACCESS_TOKEN"]


def refresh():
    r = requests.get("https://graph.instagram.com/refresh_access_token",
                     params={"grant_type": "ig_refresh_token", "access_token": tok()}, timeout=20).json()
    if "access_token" not in r:
        sys.exit(f"refresh failed: {r}")
    print(f"new token valid {r['expires_in'] // 86400} days. Update the GitHub secret IG_ACCESS_TOKEN with it:\n{r['access_token']}")


def publish(video_url, dry):
    caption = json.load(open(META))["instagram"]["caption"]
    assert "not financial advice" in caption.lower() and video_url.startswith("https://")
    body = {"media_type": "REELS", "video_url": video_url, "caption": caption, "share_to_feed": "true"}
    if dry:
        print(json.dumps(body, indent=1, ensure_ascii=False))
        return
    uid = os.environ["IG_USER_ID"]
    c = requests.post(f"{API}/{uid}/media", data={**body, "access_token": tok()}, timeout=60).json()
    if "id" not in c:
        sys.exit(f"container failed: {c}")
    for _ in range(40):                                   # ~10 min max while Instagram processes the video
        st = requests.get(f"{API}/{c['id']}", params={"fields": "status_code,status", "access_token": tok()}, timeout=20).json()
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") in ("ERROR", "EXPIRED"):
            sys.exit(f"processing failed: {st}")
        time.sleep(15)
    else:
        sys.exit("processing timed out")
    p = requests.post(f"{API}/{uid}/media_publish", data={"creation_id": c["id"], "access_token": tok()}, timeout=60).json()
    if "id" not in p:
        sys.exit(f"publish failed: {p}")
    link = requests.get(f"{API}/{p['id']}", params={"fields": "permalink", "access_token": tok()}, timeout=20).json().get("permalink")
    print(f"published: {link or p['id']}")


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--refresh" in a:
        refresh()
    elif "--video-url" not in a:
        sys.exit(__doc__)
    elif "--dry-run" not in a and not (os.environ.get("IG_USER_ID") and os.environ.get("IG_ACCESS_TOKEN")):
        print("Instagram secrets not set: skipping (MP4 is in the workflow artifact for manual upload)")
    else:
        publish(a[a.index("--video-url") + 1], "--dry-run" in a)
