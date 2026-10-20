"""Tiny local watcher: polls the source channel's public page and triggers the copier workflow
only when a new signal/update post appears. Costs zero Actions minutes while idle.
Run by launchd (see com.kuoala.erfann-watch.plist). Stdlib only."""
import json, os, re, subprocess, time, urllib.request

CH, REPO, WF = "erfann_finance", "kuoala/crypto-signal", "copy_signals.yml"
STATE = os.path.expanduser("~/.erfann_watch_last")
MARK = re.compile(r"نام ارز|Take-Profit|\b(LONG|SHORT)\b")   # signal or update, not chatter

def newest():
    html = urllib.request.urlopen(f"https://t.me/s/{CH}", timeout=20).read().decode()
    out = []
    for b in html.split("tgme_widget_message_wrap")[1:]:
        i = re.search(rf'data-post="{CH}/(\d+)"', b)
        if i: out.append((int(i.group(1)), bool(MARK.search(b))))
    return out

def dispatch():
    tok = re.search(r"password=(.*)", subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                    capture_output=True, text=True).stdout).group(1)
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/actions/workflows/{WF}/dispatches",
        data=json.dumps({"ref": "main"}).encode(), method="POST",
        headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=20).status

def main():
    last = int(open(STATE).read()) if os.path.exists(STATE) else None
    while True:
        try:
            posts = newest()
            top = max(i for i, _ in posts)
            if last is None: last = top
            if top > last:
                if any(m for i, m in posts if i > last):
                    print(time.strftime("%H:%M:%S"), "new post(s) -> dispatch", dispatch(), flush=True)
                last = top
                open(STATE, "w").write(str(last))
        except Exception as e:
            print("poll error:", e, flush=True)
        time.sleep(20)

if __name__ == "__main__":
    main()
