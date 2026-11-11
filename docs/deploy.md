# Deploying to the NAS

Idea Machine and Lattice run on TrueNAS SCALE next to Hearsay (ROADMAP §1 stack assumptions):
- Postgres, the pipeline run and Lattice run in Docker. The run is a kind on [corral](https://github.com/junglestyle/corral)'s
  NAS worker: Hearsay enqueues it at the end of its pipeline, once the stream has new turns. Set up corral's server
  and NAS worker (in `/mnt/fast/corral-worker`, deployed from the infra repo) before `install/nas.sh`.
- The data lives in the `fast/ideamachine` dataset, on the NVMe pool, so the spinning disks in `storage` can idle.
  `fast` is snapshotted nightly at 4:00 and replicated to `storage/fast-replica`.
- The clones live in `/mnt/fast/repos` (dataset `fast/repos`), owned by `admin`. Clone and pull as admin, never
  as root; run the install script with sudo from there.
- Hearsay's stream is mounted read-only, from whichever pool Hearsay is on (below).
- Lattice is on the tailnet at `https://truenas.<tailnet>.ts.net:8444/` via `tailscale serve`, and the neutral view
  (`im web`, container `ideamachine-web`) at `:8445`, with Lattice's token.
- Logs go to the journal: `journalctl -t ideamachine-run`, `-t ideamachine-backup`, `-t ideamachine-db`,
  `-t lattice`, `-t lattice-tunnel`.

eeyore keeps the dev and test databases.

## First install, moving from eeyore

On the NAS, as admin:

```sh
git clone https://github.com/junglestyle/ideamachine.git /mnt/fast/repos/ideamachine
git clone https://github.com/junglestyle/lattice.git /mnt/fast/repos/lattice
sudo /mnt/fast/repos/ideamachine/install/nas.sh
```

It creates the datasets and secrets (asking once for the Anthropic API key), builds the images and starts
Postgres. Then it stops and asks for the database.

On eeyore, as your user:

```sh
install/move-to-nas.sh
```

This stops eeyore's hourly run, dumps the database and copies it to the NAS. eeyore's copy is left as a fallback.

On the NAS again: `sudo /mnt/fast/repos/ideamachine/install/nas.sh`. It does the rest:
- restores the database;
- checks that nothing already sent to Claude would be sent again (`im pending --expect-none-resent`; this catches
  a time zone that differs from eeyore's);
- starts Lattice and the neutral view and publishes both with `tailscale serve`;
- adds the run to corral's NAS worker and registers nightly backups;
- checks that it all works (below) and prints Lattice's address and login token.

On the phone, open that address in Safari, log in, then Share → Add to Home Screen. Finally, on eeyore:
`systemctl --user disable --now lattice.service`.

## Moving from `storage` to `fast` (2026-10-15)

Until 2026-10-15 everything lived in `storage/ideamachine`, with root-owned clones inside it. `install/nas.sh` moves
it once, when `fast/ideamachine` has no database yet and `storage/ideamachine` has one:
1. switches off both cron jobs and waits for a run in progress;
2. counts the rows of every table in the running database (`/mnt/fast/ideamachine/rowcounts-before-move.txt`);
3. stops Lattice and Postgres, snapshots `config`, `backups` and `pg` together (`@to-fast-<time>`), and copies
   each with `zfs send -p | zfs recv`, so their properties come along;
4. copies any file in `logs/` that isn't a `*.log` into `saved/` (there were two, kept by hand: an
   `im trial` report and a text export). The old logs stay on `storage`;
5. starts Postgres from `fast`, checks every table has the same row count before anything writes, then carries on
   as an update, switching the cron jobs back on.

If a step fails it stops with nothing scheduled; re-running picks up where it left off. Nothing on `storage` is
deleted. Once everything works from `fast`, the script's last lines are the commands that remove the old copy.

On the NAS, as admin:

```sh
git clone https://github.com/junglestyle/ideamachine.git /mnt/fast/repos/ideamachine   # if not there yet
git clone https://github.com/junglestyle/lattice.git /mnt/fast/repos/lattice
sudo /mnt/fast/repos/ideamachine/install/nas.sh
```

## Hearsay's stream

`install/nas.sh` reads the stream from `/mnt/fast/hearsay/stream` if Hearsay has moved there, else from
`/mnt/storage/hearsay/stream`, says which, and writes it to `install/.env` (which `compose.yaml` reads). After
Hearsay moves, re-run `install/nas.sh` to switch.

The importer reads a conversation missing from `index.json` as removed and drops its segments, so a stream on
`fast` that doesn't yet list every conversation the one on `storage` does is refused rather than read. If
conversations were forgotten on purpose in between, choose by hand:
`sudo HEARSAY_STREAM=/mnt/fast/hearsay/stream /mnt/fast/repos/ideamachine/install/nas.sh`.

## Updates

As admin, `git pull` in both clones, then `sudo /mnt/fast/repos/ideamachine/install/nas.sh`. It's idempotent:
existing secrets are kept, images are rebuilt, migrations applied, the worker's kind and the backup cron job
rewritten.

Every time, it ends by checking that it all works: Postgres is up; one run finishes; one backup, the same way, writes a readable dump to `backups/`; Lattice answers on 127.0.0.1:8790 and,
through `tailscale serve`, at the tailnet address on 8444. So each re-run also does a run and a backup.

## Lattice's MCP endpoint for claude.ai (optional)

Off unless `lattice.env` sets `LATTICE_PUBLIC_URL`. Then `install/nas.sh` asks once for the Cloudflare tunnel
token (kept in `config/cloudflared.env`) and starts the `cloudflared` container, which publishes only Lattice's MCP
listener (127.0.0.1:8791). Removing the line and re-running `install/nas.sh` stops it. The Cloudflare side (the
tunnel's hostname, two WAF rules) and how to add the connector are in Lattice's `docs/public-mcp.md`. Read its
"How it's protected" first: this is the one thing reachable from the internet.

## Policy C, retired (2026-11-11)

Names said go out as said (decision 0008, amended), so nothing looks for names any more. Each run of
`install/nas.sh` drops role `im_names` if it's still there, deletes `IM_NAME_KEY` and `IM_NAMES_PASSWORD` from the
config and the names job's connections in `gpu/`, and Postgres no longer listens on the NAS's loopback. `[egress]
names`, `public_names` and `my_names` are ignored, with a warning, until deleted from `im.toml`.
TrueNAS's SSH "Allow TCP Port Forwarding", turned on for the names job's tunnel, can be turned off if nothing else
uses it.

The dev box runs nothing for Idea Machine now. Remove the `ideamachine.names` kinds from its corral worker (delete
`~/.config/corral/kinds.d/ideamachine.toml`, then `systemctl --user restart corral-worker`), and the environment
and connections `install/gpu.sh` made: `~/.local/share/ideamachine/gpu-venv`, `~/.config/ideamachine/gpu.env` and
`gpu-rebuild.env`.

## The archived database, `im_v1`

Decision 0012 rebuilt the archive from Hearsay's stream into a second database, which replaced `im` at the cutover
(2026-11-11). The database from before the rebuild was renamed `im_v1`, in the same Postgres. It's archived: dumped
once at the cutover (`backups/im_v1-2026-11-11.dump`), never migrated again, and not in the nightly backup, which
covers only `im`. Nothing connects to it. To look at it, use the psql command below with `-d im_v1`.

To drop it later, once that dump is kept somewhere safe, run the same command with `-d postgres -c 'DROP DATABASE
im_v1'`. The rebuild's leftovers can go now: `config/im/rebuild.toml`, and the last `backups/im_rebuild-*.dump` (the
nightly job no longer prunes them).

## Where things are

| What | Where |
|---|---|
| Secrets | `/mnt/fast/ideamachine/config/` (`db.env`, `pipeline.env`, `lattice.env`, `cloudflared.env`; root only) |
| Config | `/mnt/fast/ideamachine/config/im/im.toml` (e.g. `extract.prompt`; read by the run container) |
| Database | `/mnt/fast/ideamachine/pg` |
| Nightly dumps (14 days) | `/mnt/fast/ideamachine/backups/` |
| Kept by hand (trial reports, exports) | `/mnt/fast/ideamachine/saved/` (root and apps only: they quote people) |
| Clones | `/mnt/fast/repos/ideamachine`, `/mnt/fast/repos/lattice` (admin) |
| Logs | `journalctl -t ideamachine-run` (and `-t ideamachine-backup`, `-t ideamachine-db`, `-t lattice`, `-t lattice-tunnel`) |
| Run now | `im` as root (`install/bin/im`; `install/nas.sh` puts it on root's PATH), or `docker compose -f /mnt/fast/repos/ideamachine/install/compose.yaml run --rm run` |
| Any im command | `im check`, `im lattice`, ...: the command runs in the pipeline image, with `saved/` at `/saved` |
| psql | `docker compose -f /mnt/fast/repos/ideamachine/install/compose.yaml exec db psql -U postgres -d im` |
