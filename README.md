# Minu

`Minu` is an Arabic football-only guessing game where each player gets a hidden player card and the opponent has to guess it through the allowed question rules.

## What is included

- Arabic interface with the `Minu / منو` brand
- 500+ male football players only
- 4 popularity-based difficulty levels
- Match modes, optional twists, QR player-card flow, and notes
- Login-protected host experience
- Public player-card pages that stay mobile-friendly
- Signed QR card links with expiry support
- FastAPI backend + React frontend

## Local run

Use:

```powershell
./scripts/start-local.ps1
```

Local admin credentials are auto-generated on first run and saved here:

```text
%LOCALAPPDATA%\WhoIsThePlayerFootball\secrets\minu_admin_credentials.txt
```

## Production deploy

This repo is now prepared for a permanent Vercel deploy:

- [vercel.json](./vercel.json)
- [api/index.py](./api/index.py)
- [requirements.txt](./requirements.txt)

How this deploy works:

- Vercel runs one Python FastAPI function
- The frontend is built first from `frontend/`
- The same FastAPI app serves `/minu`, `/minu/card/...`, and `/api/...`
- Your permanent public URL will be on `vercel.app`

For database storage on Vercel:

- Use a Postgres integration from the Vercel Marketplace
- Neon is the normal free option for this setup

Secrets to set in Vercel:

- `DATABASE_URL`
- `MINU_ADMIN_USERNAME`
- `MINU_ADMIN_PASSWORD`
- `MINU_SESSION_SECRET`

Optional:


## GitHub + Vercel workflow

Use this one-command publish flow:

```powershell
./scripts/publish-github-vercel.ps1 -CommitMessage "update Minu"
```

First time only, if this repo still has no GitHub `origin`, run it with your repo URL:

```powershell
./scripts/publish-github-vercel.ps1 -RemoteUrl https://github.com/<you>/<repo>.git -CommitMessage "initial publish"
```

What this script does:

- commits local changes if you pass `-CommitMessage`
- pushes the current branch to GitHub
- deploys the project to Vercel
- verifies the live site response

Useful flags:

- `-SkipPush`
- `-SkipDeploy`
- `-SkipVerify`
- `-Branch your-branch`

Render files are still kept in the repo if you ever want that path too:

- [Dockerfile](./Dockerfile)
- [render.yaml](./render.yaml)

## Important env vars

- `DATABASE_URL`: optional override, used automatically in production
- `MINU_ADMIN_USERNAME`: fixed production login name
- `MINU_ADMIN_PASSWORD`: fixed production login password
- `MINU_ADMIN_PASSWORD_HASH`: optional hashed alternative if you prefer not to store plaintext in Render
- `MINU_SESSION_SECRET`: required for stable signed login sessions across restarts
- Signed player-card links have a fixed 15-minute lifetime.
- `FRONTEND_DIST_DIR`: optional frontend build path override

## Stack

- Backend: FastAPI + SQLAlchemy
- Frontend: React + Vite + TypeScript
- QR cards: same-origin SVG generation
- Auth: signed HTTP-only cookie session
