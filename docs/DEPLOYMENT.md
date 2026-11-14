# Deployment

The web app runs on **Vercel**. Everything else (API, worker, DevSim, the three MCP servers,
Postgres, Redis and Qdrant) runs on one **Azure VM** with the same `docker compose` stack as
`make dev`. The browser talks only to Vercel, which forwards `/api/*` to the VM over HTTPS, so the
session cookie stays first-party on the Vercel domain.

```
browser ──https──▶ Vercel (Next.js) ──/api/* over https──▶ Azure VM: Caddy ─▶ API ─▶ worker, MCP servers, DevSim
                                                                           └──▶ Postgres, Redis, Qdrant
```

The deployed app runs on DevSim, the simulated marketplace: it never contacts a real client.

## 1. The VM (Azure for Students)

1. Sign up for **Azure for Students** with your university email (no card; $100 credit).
2. **Create a virtual machine**:
   - Image: **Ubuntu Server 24.04 LTS**.
   - Size: **B2ms** (2 vCPU, 8 GB). If your subscription does not offer it, **B2s** (4 GB) works;
     the setup script adds swap.
   - Authentication: an SSH key or a password; user name `azureuser`.
   - Inbound ports: **SSH (22), HTTP (80), HTTPS (443)**. Nothing else.
3. When it is created: **Overview → DNS name → Configure**, set a label such as `gigbid-api`,
   and save. The VM is now `gigbid-api.<region>.cloudapp.azure.com`. This is `API_DOMAIN`.

## 2. The backend

```bash
ssh azureuser@gigbid-api.<region>.cloudapp.azure.com
git clone https://github.com/FatimaSohailll/GigBid.git
cd GigBid
sh deploy/vm/setup.sh        # installs Docker, then creates .env and stops
nano .env                    # fill in the values below
sh deploy/vm/setup.sh        # builds and starts the backend
```

Values to set in the VM's `.env` (never commit it):

| Name | Value |
|---|---|
| `GROQ_API_KEY` | Your Groq key |
| `TOKEN_ENCRYPTION_KEY` | `python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"` |
| `MCP_TENANT_SIGNING_KEY` | `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `DEMO_EMAIL`, `DEMO_PASSWORD` | The reviewer login, created with a persona on first start |
| `API_DOMAIN` | The VM's DNS name from step 1.3 |
| `WEB_APP_URL` | The Vercel address, once you have it (step 3) |
| `LANGSMITH_API_KEY` | Optional, for traces |

Check it: `curl https://<API_DOMAIN>/health` should answer `{"status":"ok",...}`. The first start
generates DevSim data and indexes the portfolios, which takes a few minutes.

## 3. The web app (Vercel)

1. On vercel.com, **Add New → Project**, import the `GigBid` GitHub repository.
2. **Root Directory**: `apps/web`. Framework preset: Next.js (detected).
3. **Environment Variables**: `API_URL` = `https://<API_DOMAIN>`. Next.js fixes this address at
   build time, so change it only with a redeploy.
4. **Deploy.** Then set `WEB_APP_URL` on the VM to the Vercel address and run
   `sh deploy/vm/setup.sh` again.

Sign in at the Vercel address with `DEMO_EMAIL` and `DEMO_PASSWORD`.

## Updating

- Web: every push to `main` redeploys on Vercel.
- Backend: on the VM, `git pull && sh deploy/vm/setup.sh`.

## Limits and notes

- The deployed app shares the Groq key's daily free limit with any evaluation run on the same key.
- Live progress is streamed with Server-Sent Events through Vercel's proxy; if a proxy buffers
  it, the run page still updates, because it also polls the run's status.
- Hugging Face Spaces: `deploy/space/` holds a single-container image for a Space. Docker Spaces
  need a paid plan, so it is kept as an alternative, not used.
