# Free server on Google Cloud (about 30 minutes, once)

The logger has to run around the clock, so the app lives on a small server that's always on.
Google Cloud's **e2-micro** is free indefinitely (always-free tier) when set up exactly as below.

```
 Tesla + Energi Data Service  <──  your free server (logger + dashboard)  ──Tailscale──>  your phone / PC
```

## A · Google Cloud account
1. Go to **console.cloud.google.com**, sign in with a Google account and turn on **2-step verification** for it.
2. Sign up (*Try for free*): account type **Individual**, your own country and address, a payment card for verification.
   The trial credit is separate from the always-free server. When the 90-day trial ends, click
   **Activate full account** – otherwise Google pauses the server.
3. **Safety net:** ☰ → Billing → **Budgets & alerts** → Create budget → amount **10** (DKK) → alerts at 50/90/100 %.
   Untick *Promotions and other credits*. You get an email if anything ever starts costing money.

## B · Create the server
☰ → **Compute Engine → VM instances → Create instance**:

| Setting | Value |
|---|---|
| Name | `ev-server` |
| Region | **us-central1**, us-west1 or us-east1 (only these are free) |
| Machine | series **E2** → machine type **e2-micro** (under *Shared core*) |
| Provisioning model | **Standard** (not Spot) |
| OS and storage → Change | **Debian**, disk type **Standard persistent disk**, **30 GB** |
| Data protection / backups | **No backups** (snapshots can cost money) |
| Networking → Firewall | HTTP and HTTPS **unticked** |

⚠ The default disk type ("Balanced") is **not** free – pick **Standard**.
The cost estimate still shows a few dollars; the free tier is subtracted on the bill.
Click **Create**.

## C · Install the app
Click **SSH** next to `ev-server` (a terminal opens in the browser).
If it closes immediately: allow pop-ups for console.cloud.google.com, wait a minute and try again –
or use **Cloud Shell** (`>_` at the top right) and run `gcloud compute ssh ev-server --zone=<zone>`.
Company networks sometimes block both; use a private computer or your phone on mobile data then.

```bash
sudo apt-get install -y git
git clone https://github.com/santhom24/ev-charging-tracker.git ev_cost
cd ev_cost
bash deploy/install.sh
```
Takes 5–10 minutes. It installs everything and starts two services, **ev-logger** and **ev-dashboard**,
which restart by themselves after crashes and reboots.

## D · Reach the app from your phone (Tailscale)
Tailscale makes a private network between your own devices – the app is never visible on the internet.
1. On the server:
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up
   ```
   Open the printed link and sign in (Google, Microsoft or Apple account).
2. Install the **Tailscale** app on your phone, sign in with the **same** account, switch it on.
3. Open **`http://ev-server:8501`** (or `http://100.x.x.x:8501` with the address shown in the Tailscale app).
4. At **login.tailscale.com/admin/machines** → ev-server → ⋯ → **Disable key expiry**, so access doesn't stop after a few months.

Now continue with the **setup page** in the app.

## Useful commands (in the SSH window, inside `ev_cost`)
| What | Command |
|---|---|
| Logger output, live | `journalctl -u ev-logger -f` (Ctrl+C to leave) |
| Status of both services | `systemctl status ev-logger ev-dashboard` |
| Restart | `sudo systemctl restart ev-logger ev-dashboard` |
| Update to the latest version | `bash deploy/update.sh` |
| Sessions in the terminal | `.venv/bin/python logger.py report` |
| Check Supercharger access | `.venv/bin/python tesla_auth.py history` |

After a few days, check ☰ → Billing → **Reports** shows 0 kr.
