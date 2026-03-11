# Deploying ChaosWing to chaos-wing.com

## Architecture

```
User → Cloudflare (DDoS shield + SSL + CDN) → Render (Django + Gunicorn + PostgreSQL)
```

- **Cloudflare** (free): Hides your real server IP, blocks DDoS attacks, bots, and scanners. Provides free SSL and caching.
- **Render** (free tier): Hosts the Django app with PostgreSQL. Auto-deploys from GitHub on every push.

---

## Step 1: Push Code to GitHub

Make sure all changes are committed and pushed:

```powershell
cd D:\Chaoswing
git add -A
git commit -m "Add production deployment config"
git push origin main
```

**IMPORTANT**: Verify `.env` is NOT in the commit. Run `git show --stat HEAD` and confirm `.env` does not appear.

---

## Step 2: Deploy on Render

1. Go to [https://render.com](https://render.com) and sign up (use your GitHub account).
2. Click **New** → **Blueprint**.
3. Connect your GitHub repo `Zwc-11/Chaoswing`.
4. Render detects `render.yaml` and shows the plan:
   - **Web Service**: `chaoswing` (Python)
   - **Database**: `chaoswing-db` (PostgreSQL)
5. Click **Apply**. Render will build and deploy automatically.
6. **Add your Anthropic API key**: Go to **Dashboard** → **chaoswing** service → **Environment** → find `ANTHROPIC_API_KEY` → paste your key → **Save**.
7. Your app is now live at `https://chaoswing.onrender.com`.

---

## Step 3: Set Up Cloudflare (DDoS Protection + SSL)

1. Go to [https://cloudflare.com](https://cloudflare.com) and create a free account.
2. Click **Add a site** → enter `chaos-wing.com` → select the **Free** plan.
3. Cloudflare scans your DNS. If existing records appear, keep them.
4. **Add a CNAME record** pointing your domain to Render:

   | Type  | Name | Target                        | Proxy |
   |-------|------|-------------------------------|-------|
   | CNAME | @    | chaoswing.onrender.com        | ON    |
   | CNAME | www  | chaoswing.onrender.com        | ON    |

5. Cloudflare gives you **two nameservers** (e.g., `ada.ns.cloudflare.com` and `bob.ns.cloudflare.com`).
6. Go to your **domain registrar** (where you bought `chaos-wing.com`) and **replace the nameservers** with the Cloudflare ones.
7. Wait 10-60 minutes for DNS to propagate.

---

## Step 4: Configure Cloudflare Security

Once the domain is active on Cloudflare:

### SSL/TLS
- Go to **SSL/TLS** → set mode to **Full (strict)**.

### Security Settings
- **Security** → **Settings** → set Security Level to **Medium** or **High**.
- **Security** → **Bots** → enable **Bot Fight Mode** (free).

### Page Rules (optional)
- Add a rule: `http://chaos-wing.com/*` → **Always Use HTTPS**.

### Rate Limiting (optional, paid)
- Cloudflare's free tier already blocks most attacks. If you need custom rate limiting, it's available on the paid plan, but your Django middleware already handles per-IP throttling.

---

## Step 5: Add Custom Domain on Render

1. Go to your Render dashboard → **chaoswing** service → **Settings** → **Custom Domains**.
2. Add `chaos-wing.com` and `www.chaos-wing.com`.
3. Render will verify the domain (it should work since DNS already points to Render through Cloudflare).

---

## Step 6: Verify Everything Works

1. Visit `https://chaos-wing.com` — you should see the ChaosWing landing page.
2. Click **Launch App** → try loading a market → confirm the butterfly graph renders.
3. Check the padlock icon in your browser — SSL should be active.

---

## Security Layers (What Protects You)

| Layer | Protection |
|-------|-----------|
| **Cloudflare** | DDoS mitigation, bot blocking, scanner blocking, WAF rules, IP hiding |
| **Django Rate Limiter** | Per-IP sliding window throttle, burst detection, auto-ban suspicious paths |
| **Security Headers** | CSP, HSTS, Permissions-Policy, COOP, COEP, CORP |
| **Django Security** | CSRF protection, secure cookies, SSL redirect, X-Frame-Options DENY |
| **Render** | Automatic SSL certificates, isolated containers, no SSH access |

Your **real server IP is hidden** behind Cloudflare. Attackers can only see Cloudflare's IPs, not your Render server.

---

## Environment Variables on Render

These are set automatically by `render.yaml`. You only need to manually add `ANTHROPIC_API_KEY`:

| Variable | Value | Source |
|----------|-------|--------|
| `DJANGO_SECRET_KEY` | Auto-generated | render.yaml |
| `DATABASE_URL` | Auto from PostgreSQL | render.yaml |
| `DJANGO_DEBUG` | `0` | render.yaml |
| `DJANGO_ALLOWED_HOSTS` | `chaos-wing.com,...` | render.yaml |
| `ANTHROPIC_API_KEY` | Your key | **Manual** |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | render.yaml |

---

## Updating the Site

Every time you push to `main`, Render auto-deploys:

```powershell
git add -A
git commit -m "your changes"
git push origin main
```

Render will rebuild, run migrations, collect static files, and restart the server automatically.
