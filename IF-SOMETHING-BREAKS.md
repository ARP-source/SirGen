# If something breaks

First, paste this into the Cursor agent. It usually fixes things on its own:
```
Read AGENTS.md and HANDOFF.md. Something is broken: <say what you see>. Check curl -s localhost:7000/health, docker ps -a, docker logs --tail 30 lightning, the last 40 lines of ~/sentinel-dev.log (or the dev.sh tmux window), and free -g. Tell me the cause in one line, then fix it. Don't delete containers, files in ~/assets or audit/, and don't restart Docker.
```

If the agent itself isn't working, the common cases by hand:

| What you see | Do this |
|---|---|
| Screens won't load at `:5173` | The app stopped. New tmux window (`Ctrl-b` then `c`): `cd ~/sentinel-or-app-m3 && SENTINEL_VIDEO=$HOME/assets/data/holoscan_sample/surgical_video.264 bash scripts/dev.sh 2>&1 \| tee -a ~/sentinel-dev.log` |
| "address already in use" on 7000 or 5173 | An old copy is still running. `pkill -f "uvicorn backend.app"; pkill -f vite`, then start again. |
| `/surgical` is black | Press **Play** on `/control`. Still black: check `curl -s --max-time 2 localhost:7000/video.mjpg \| head -c 300` shows `X-Frame-Id`. |
| Agents say "unavailable" | Lightning is down or still booting: `curl -s localhost:8000/v1/models`. If empty, `docker ps -a`; if `lightning` is "Exited", `docker start lightning` and wait for "Application startup complete" in `docker logs -f lightning` (Ctrl-C stops watching, not the model). |
| Lightning crashes with out of memory | `docker stop omni` if it is running, `sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'`, then `docker start lightning`. |
| Lightning stuck compiling over 45 min | `docker rm -f lightning`, then re-run the start command in HANDOFF.md section 4 with `--enforce-eager` added at the end. |
| `permission denied` on docker | `sudo usermod -aG docker $USER`, then log out and SSH in again. |
| Tests fail after a change | `git diff` to see what changed; `git stash` puts the last working version back (`git stash pop` brings the change back). |
| Audit "Verify" fails | Don't touch the file; it is evidence. Restarting the backend starts a new log. Tell the agent to find out what wrote to the file. |
| Report says "Narrative unavailable" | The tables are still right. Lightning was down or its summary failed checks; it's allowed to show this. Fix later via Step 1 in MILESTONES.md. |
| SSH from laptop won't connect | Check the GX10 and laptop are on the same wifi; on the GX10 screen run `hostname -I` to see if the address changed from 169.233.72.133. |
| Everything is a mess, demo in under an hour | Stop building. `git log --oneline`, `git checkout <last good commit>`, restart the app, rehearse the three scenarios, use the backup video if needed. |
