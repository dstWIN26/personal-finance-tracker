# GitHub — Connected ✓

GitHub CLI is installed and authenticated as **dstWIN26**.

The repo for this project lives at:
**https://github.com/dstWIN26/personal-finance-tracker**

## Everyday workflow

```bash
# Check what changed
git status

# Stage and commit
git add .
git commit -m "describe your change"

# Push (triggers Render auto-deploy if connected)
git push
```

## Connect to Render for auto-deploy

1. Go to https://render.com → New → Web Service
2. Connect to GitHub → select `dstWIN26/personal-finance-tracker`
3. Runtime: **Docker**, Branch: **main**, Region: **Frankfurt**
4. Add Disk: mount path `/app/data`, size 1 GB
5. Add environment variables from `.env.example`
6. Deploy — your URL will be `https://personal-finance-tracker.onrender.com`

After that, every `git push` to `main` automatically redeploys.
