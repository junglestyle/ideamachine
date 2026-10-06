# Deploying to the NAS

Idea Machine and Lattice run on TrueNAS SCALE next to Hearsay and Teem (ROADMAP §1 stack assumptions):
- Postgres, the hourly pipeline run (cron, :45) and Lattice run in Docker.
- The data lives in the `storage/ideamachine` dataset.
- Hearsay's stream is mounted read-only.
- Lattice is on the tailnet at `https://truenas.<tailnet>.ts.net:8444/` via `tailscale serve`.

eeyore keeps the dev and test databases.

## First install, moving from eeyore

On the NAS, as root:

```sh
midclt call pool.dataset.create '{"name": "storage/ideamachine"}'     # before cloning into it
git clone https://github.com/junglestyle/ideamachine.git /mnt/storage/ideamachine/repo
git clone https://github.com/junglestyle/lattice.git /mnt/storage/ideamachine/lattice
/mnt/storage/ideamachine/repo/install/nas.sh
```

It creates the datasets and secrets (asking once for the Anthropic API key), builds the images and starts
Postgres. Then it stops and asks for the database.

On eeyore, as your user:

```sh
install/move-to-nas.sh
```

This stops eeyore's hourly run, dumps the database and copies it to the NAS. eeyore's copy is left as a fallback.

On the NAS again: `install/nas.sh`. It does the rest:
- restores the database;
- checks that nothing already sent to Claude would be sent again (`im pending --expect-none-resent`; this catches
  a time zone that differs from eeyore's);
- starts Lattice and publishes it with `tailscale serve`;
- registers the hourly run and nightly backups;
- prints Lattice's address and login token.

On the phone, open that address in Safari, log in, then Share → Add to Home Screen. Finally, on eeyore:
`systemctl --user disable --now lattice.service`.

## Updates

`git pull` in both clones, then re-run `install/nas.sh`. It's idempotent: existing secrets are kept, images are
rebuilt, migrations applied.

## Where things are

| What | Where |
|---|---|
| Secrets | `/mnt/storage/ideamachine/config/` (`db.env`, `pipeline.env`, `lattice.env`; root only) |
| Database | `/mnt/storage/ideamachine/pg` |
| Nightly dumps (14 days) | `/mnt/storage/ideamachine/backups/` |
| Logs | `/mnt/storage/ideamachine/logs/run.log`, `backup.log` |
| Run now | `docker compose -f /mnt/storage/ideamachine/repo/install/compose.yaml run --rm run` |
| psql | `docker compose -f /mnt/storage/ideamachine/repo/install/compose.yaml exec db psql -U postgres -d im` |
