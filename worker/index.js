// Cloudflare Worker (free tier): checks the source channel's public page every minute and
// starts the GitHub copier workflow ONLY when a new signal/update post appeared.
// Idle minutes cost nothing; no computer of yours needs to be on.
const CH = "erfann_finance";
const MARK = /نام ارز|Take-Profit|\b(LONG|SHORT)\b/;   // signal or update, not chatter

export default {
  async scheduled(_event, env) {
    const html = await (await fetch(`https://t.me/s/${CH}`)).text();
    const posts = html.split("tgme_widget_message_wrap").slice(1).map(b => {
      const m = b.match(new RegExp(`data-post="${CH}/(\\d+)"`));
      return m ? { id: +m[1], hit: MARK.test(b) } : null;
    }).filter(Boolean);
    if (!posts.length) return;
    const top = Math.max(...posts.map(p => p.id));
    const last = +(await env.KV.get("last")) || top;          // first run: start from now
    if (top > last && posts.some(p => p.id > last && p.hit)) {
      const r = await fetch(`https://api.github.com/repos/${env.REPO}/actions/workflows/copy_signals.yml/dispatches`, {
        method: "POST",
        headers: { Authorization: `Bearer ${env.GH_TOKEN}`, Accept: "application/vnd.github+json", "User-Agent": "erfann-watch" },
        body: JSON.stringify({ ref: "main" }),
      });
      if (!r.ok) return;                                       // retry next minute
    }
    await env.KV.put("last", String(top));
  },
};
