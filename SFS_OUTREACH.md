# Telegram cross-promotion (SFS)

Equal shout-out swaps with crypto **news/education** channels about our size (0.5×–3×). Never with signal competitors, never paid, never member-adder tools.

## 1. Find prospects (read-only, your own Telegram session)

```bash
pip install telethon==1.45.0
export TG_API_ID=... TG_API_HASH=...       # my.telegram.org > API development tools; keep them out of the repo
python3 sfs_prospects.py --selftest
python3 sfs_prospects.py --dry-run <seed1> <seed2>        # first run asks for your phone + code, writes kuoala_user.session
python3 sfs_prospects.py <seed1> ... <seed10>             # -> sfs_prospects.csv (gitignored), passing channels first
```

Open each passing channel yourself before writing: check the last week of posts, that views look organic, and that it doesn't sell signals or "VIP".

## 2. Make a tracked link per partner

```bash
python3 make_invite_link.py sfs-<partner> --dry-run
python3 make_invite_link.py sfs-<partner>       # saved to invite_links.json (gitignored)
python3 make_invite_link.py --stats             # joins per link (or: Manage channel > Invite links in the app)
```

Give the partner **their** link, so we can see which swaps bring people who stay. Check 7 and 30 days later whether those joiners are still there (channel stats > Followers graph after each swap date).

## 3. Messages (you send these by hand, one at a time)

Fill the `{…}` from `sfs_prospects.py`'s "our subscribers" line, the channel's Statistics page, and the latest weekly report. Only real numbers.

**Variant A, short**

> Hi {name}, I run t.me/KuoalaOfficial ({subs} subs, ~{avg_views} views a post). I read your {specific post or series}, and our audiences overlap but we don't compete: you cover {news/education}, we post signals with every result public, losses included.
> Would you do an equal swap? One post each, same day, pinned 24 h, with a tracked link both ways so we can both see the result. Happy to send our stats screenshot first.

**Variant B, value first**

> Hey {name}, {one honest sentence about something useful on their channel}.
> I'm behind Kuoala (t.me/KuoalaOfficial, {subs} subs, {view_rate}% view rate). We post crypto signals, some from our own rules and some relayed from other channels, and every one is scored on real exchange prices in a weekly report, wins and losses.
> Would you swap shout-outs, one post each, same size and timing? I can write ours about your channel so it's zero work for you. No paid promo or anything like that, just a fair swap.

Rules: send to the admin listed in the channel description only, at most ~5 a day, never repeat after a no or no answer, never mass-paste.

## 4. Seed channels to confirm

Confirm or replace these before the first run (news/education, English, not signal sellers). Telegram's recommendations do the rest:
`WatcherGuru`, `cointelegraph`, `coindesk`, `wublockchainenglish`, `thecryptonewsroom`, `bitcoinmagazinetelegram`, `glassnodeofficial`, `cryptoquant_official`, `the_block_crypto`, `messaricrypto`.
These handles are my suggestions and haven't been checked yet. `--dry-run` skips any that don't resolve.
