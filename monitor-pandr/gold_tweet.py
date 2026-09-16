#!/usr/bin/env /Users/proofsandreasons/monitor/venv/bin/python3
"""gold_tweet.py — post the finished golden-hour peak frame to X/Twitter (@LaboratoryMinds).

Called by goldtick.sh's window-close finalizer (so the FINAL pick is posted, not a running
peak that a redder frame later supersedes). Fail-soft by design: any error is logged and the
exit code is 0 so the finalizer's upload path is never disturbed.

  * Windows posted: TWEET_WINDOWS in the env file (default "morning").
  * Credentials: ~/monitor/.twitter_env (KEY=VALUE, chmod 600, NOT in git — see
    .twitter_env.example). OAuth 1.0a user context for the LaboratoryMinds account:
    X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET (app must have Read+Write).
  * Idempotent: one post per window, marker in .tweeted/<date>_<which>.
  * Image: .goldpeak_frame.jpg downscaled to TWEET_MAX_W (default 2048) px wide, JPEG q92
    (X recompresses anyway; keeps the upload well under the 5 MB image limit).
  * Upload tries the v2 media endpoint first, falls back to v1.1 media/upload; the post
    itself is v2 POST /2/tweets. Alt text is best-effort.
  * Log: monitor.log ([goldtweet] lines) + tweet_log.csv (one row per post).

Usage:  gold_tweet.py [--dry-run] [--force] [--which morning|evening] [frame.jpg] [peak.json]
--dry-run builds the caption and image and checks credentials but sends nothing.
"""
import os, sys, json, csv, datetime, cv2

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
ENV_FILE = os.path.join(HERE, ".twitter_env")
LOG = os.path.join(HERE, "monitor.log")
TWEET_LOG = os.path.join(HERE, "tweet_log.csv")
MARK_DIR = os.path.join(HERE, ".tweeted")
PAGE_URL = "https://proofsandreasons.io"
V2_MEDIA = "https://api.x.com/2/media/upload"
V1_MEDIA = "https://upload.twitter.com/1.1/media/upload.json"
V1_ALT = "https://upload.twitter.com/1.1/media/metadata/create.json"
V2_TWEET = "https://api.x.com/2/tweets"


def log(msg):
    line = f"{datetime.datetime.now(datetime.timezone.utc):%Y-%m-%dT%H:%M:%SZ} [goldtweet] {msg}"
    print(line)
    try:
        open(LOG, "a").write(line + "\n")
    except Exception:
        pass


def load_env():
    env = {}
    try:
        for ln in open(ENV_FILE):
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    # Accept the names the X developer portal uses as well as the X_* names.
    for alias, canon in (("consumer_key", "X_API_KEY"), ("secret_key", "X_API_SECRET"),
                         ("consumer_secret", "X_API_SECRET"), ("api_key", "X_API_KEY"),
                         ("api_secret", "X_API_SECRET"), ("access_token", "X_ACCESS_TOKEN"),
                         ("access_token_secret", "X_ACCESS_SECRET")):
        for k in (alias, alias.upper()):
            if k in env and not env.get(canon):
                env[canon] = env[k]
    return env


def caption(s):
    """Caption in Simon's template (2026-09-16):
       It's sunrise at Proofs and Reasons on the @CarnegieMellon campus. Sun XX° below horizon.
       Red across XX% of the Eastern sky. https://proofsandreasons.io
    Evening swaps sunrise->sunset. red_pct is the winning sky region's red fraction; when
    it is under 1% the sentence says so rather than quoting 0%."""
    which = s.get("which", "morning")
    what = "sunrise" if which == "morning" else "sunset"
    el = s.get("sun_elev")
    if el is None:
        sun = ""
    elif abs(el) < 0.25:
        sun = "Sun on the horizon."
    else:
        sun = f"Sun {abs(el):.1f}° {'below' if el < 0 else 'above'} horizon."
    red = float(s.get("red_pct") or 0.0)
    where = "the Eastern sky" if which == "morning" else "the sky"
    sky = f"Red across {red:.0f}% of {where}." if red >= 1.0 else f"No red in {where} today."
    text = (f"It's {what} at Proofs and Reasons on the @CarnegieMellon campus. {sun} {sky} "
            f"{PAGE_URL}").replace("  ", " ")
    d = datetime.date.fromisoformat(s["date"])
    alt = (f"Automated photograph of the Carnegie Mellon campus skyline at {what}, "
           f"{d:%A %-d %B %Y}, {s.get('time', '')}. {sun} {sky}").replace("  ", " ")
    return text, alt[:1000]


def prepare_image(src, max_w):
    img = cv2.imread(src)
    if img is None:
        raise RuntimeError(f"cannot read {src}")
    h, w = img.shape[:2]
    if w > max_w:
        img = cv2.resize(img, (max_w, int(round(h * max_w / w))), interpolation=cv2.INTER_AREA)
    out = os.path.join(HERE, ".tweet_frame.jpg")
    cv2.imwrite(out, img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return out, os.path.getsize(out)


def upload_media(sess, path):
    """Return a media id string. v2 first, then the v1.1 endpoint."""
    with open(path, "rb") as fh:
        r = sess.post(V2_MEDIA, files={"media": ("sky.jpg", fh, "image/jpeg")},
                      data={"media_category": "tweet_image", "media_type": "image/jpeg"}, timeout=60)
    if r.status_code in (200, 201):
        j = r.json()
        mid = (j.get("data") or {}).get("id") or j.get("media_id_string") or j.get("id")
        if mid:
            return str(mid), "v2"
    log(f"v2 media upload -> HTTP {r.status_code}: {r.text[:200]}; trying v1.1")
    with open(path, "rb") as fh:
        r = sess.post(V1_MEDIA, files={"media": ("sky.jpg", fh, "image/jpeg")},
                      data={"media_category": "tweet_image"}, timeout=60)
    if r.status_code in (200, 201):
        return str(r.json()["media_id_string"]), "v1.1"
    raise RuntimeError(f"media upload failed: HTTP {r.status_code} {r.text[:300]}")


def set_alt_text(sess, media_id, alt):
    try:
        r = sess.post(V1_ALT, json={"media_id": media_id, "alt_text": {"text": alt}}, timeout=30)
        if r.status_code not in (200, 201, 204):
            log(f"alt text not set (HTTP {r.status_code}); continuing")
    except Exception as e:
        log(f"alt text error {e!r}; continuing")


def main():
    args = sys.argv[1:]
    dry = "--dry-run" in args
    force = "--force" in args
    which_arg = None
    if "--which" in args:
        which_arg = args[args.index("--which") + 1]
        args = [a for i, a in enumerate(args) if i not in (args.index("--which"), args.index("--which") + 1)]
    pos = [a for a in args if not a.startswith("--")]
    frame = pos[0] if pos else os.path.join(HERE, ".goldpeak_frame.jpg")
    peak = pos[1] if len(pos) > 1 else os.path.join(HERE, ".golden_peak.json")

    env = load_env()
    windows = {w.strip() for w in env.get("TWEET_WINDOWS", "morning").split(",") if w.strip()}
    max_w = int(env.get("TWEET_MAX_W", "2048"))

    try:
        s = json.load(open(peak))
    except Exception as e:
        log(f"no peak record ({e!r}); nothing to post"); return 0
    which = which_arg or s.get("which")
    if which not in windows:
        log(f"window {which!r} not in TWEET_WINDOWS={sorted(windows)}; skipping"); return 0
    if not os.path.isfile(frame) or os.path.getsize(frame) == 0:
        log(f"peak frame {frame} missing; skipping"); return 0

    os.makedirs(MARK_DIR, exist_ok=True)
    mark = os.path.join(MARK_DIR, f"{s.get('date')}_{which}")
    if os.path.exists(mark) and not force:
        log(f"already posted {s.get('date')} {which}; skipping"); return 0

    text, alt = caption(s)
    img, nbytes = prepare_image(frame, max_w)
    keys = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")
    have_creds = all(env.get(k) for k in keys)

    if dry:
        log(f"DRY RUN {which} {s.get('date')}: image {img} ({nbytes/1e6:.2f} MB), "
            f"credentials {'present' if have_creds else 'MISSING (' + ENV_FILE + ')'}")
        print("---- caption (%d chars) ----\n%s\n---- alt ----\n%s" % (len(text), text, alt))
        return 0
    if not have_creds:
        log(f"no credentials in {ENV_FILE}; not posting"); return 0

    try:
        import requests
        from requests_oauthlib import OAuth1
        sess = requests.Session()
        sess.auth = OAuth1(env["X_API_KEY"], env["X_API_SECRET"],
                           env["X_ACCESS_TOKEN"], env["X_ACCESS_SECRET"])
        media_id, via = upload_media(sess, img)
        set_alt_text(sess, media_id, alt)
        r = sess.post(V2_TWEET, json={"text": text, "media": {"media_ids": [media_id]}}, timeout=60)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"tweet failed: HTTP {r.status_code} {r.text[:300]}")
        tid = r.json().get("data", {}).get("id", "")
        open(mark, "w").write(tid + "\n")
        new = not os.path.exists(TWEET_LOG)
        with open(TWEET_LOG, "a", newline="") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["posted_utc", "date", "which", "time", "sun_elev", "gold_score",
                            "red_pct", "media_via", "tweet_id"])
            w.writerow([datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                        s.get("date"), which, s.get("time"), s.get("sun_elev"),
                        s.get("gold_score"), s.get("red_pct"), via, tid])
        log(f"posted {which} {s.get('date')} {s.get('time')} -> tweet {tid} (media via {via})")
    except Exception as e:
        log(f"post FAILED: {e!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
