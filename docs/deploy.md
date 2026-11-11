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
4. copies `vocab/`, and any file in `logs/` that isn't a `*.log` into `saved/` (there were two, kept by hand: an
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

Hearsay mounts `/mnt/fast/ideamachine/vocab` read-only for my vocabulary (Hearsay's `IDEAMACHINE_VOCAB_DIR`).

## Updates

As admin, `git pull` in both clones, then `sudo /mnt/fast/repos/ideamachine/install/nas.sh`. It's idempotent:
existing secrets are kept, images are rebuilt, migrations applied, the worker's kind and the backup cron job
rewritten. While there's a rebuild database (below), it then re-runs `install/rebuild.sh`, so that one gets the same
migrations.

Every time, it ends by checking that it all works: Postgres is up; one run finishes; one backup, the same way, writes a readable dump to `backups/`; Lattice answers on 127.0.0.1:8790 and,
through `tailscale serve`, at the tailnet address on 8444. So each re-run also does a run and a backup.

## Lattice's MCP endpoint for claude.ai (optional)

Off unless `lattice.env` sets `LATTICE_PUBLIC_URL`. Then `install/nas.sh` asks once for the Cloudflare tunnel
token (kept in `config/cloudflared.env`) and starts the `cloudflared` container, which publishes only Lattice's MCP
listener (127.0.0.1:8791). Removing the line and re-running `install/nas.sh` stops it. The Cloudflare side (the
tunnel's hostname, two WAF rules) and how to add the connector are in Lattice's `docs/public-mcp.md`. Read its
"How it's protected" first: this is the one thing reachable from the internet.

## Names said, on the dev box's GPU

Policy C (decision 0008) finds names said with GLiNER and keeps them per segment (`im.segment_names`). The NAS
reads any segment not looked at yet on its own CPU, which keeps up with live volume. For a backlog (the trial
sample, the whole history before the live switch, a day of all-day recording), the dev box's GPU does it as a
corral job:

- On the NAS, `install/nas.sh` puts Postgres on the NAS's loopback (`127.0.0.1:55433`, nothing on the network),
  creates role `im_names` (reads segments and episodes, writes names, nothing else) and writes its connection to
  `/mnt/fast/ideamachine/gpu/names.env`, readable only by the clones' owner.
- On the dev box, as your user: `install/gpu.sh`. Like Hearsay's, it checks the GPU and `ssh nas`, builds an
  environment with CUDA torch and the pinned GLiNER, copies the connection, and adds `ideamachine.names` to the
  corral worker. Each job opens an ssh tunnel to the NAS, reads one chunk (corral's suggested size), and requeues
  itself until nothing is left.
- Start a backlog: `corral enqueue ideamachine.names --needs gpu --key ideamachine.names`. `corral pause` holds it
  between chunks; the NAS never waits for it.

## The rebuild database

Decision 0012 rebuilds the archive from Hearsay's stream, with extraction v5, into a second database, `im_rebuild`,
in the same Postgres as `im`, and switches to it once its checks pass. Both use the same image: only the connection
(`IM_DATABASE_URL`) and the config (`IM_CONFIG`) differ. Lattice and the neutral view read `im` until the cutover.

Create it on the NAS, after `install/nas.sh`:

```sh
sudo /mnt/fast/repos/ideamachine/install/rebuild.sh
```

It creates `im_rebuild` if it's missing and gives it `im`'s per-database setup (`dev/init/bootstrap-db.sql.in`) and
the names role's grants. It writes the rebuild's connections, `config/rebuild.env` and `gpu/names-rebuild.env`,
from the live ones with the database name swapped, so no secret is entered twice. It writes `config/im/rebuild.toml`
if it's missing, then migrates `im_rebuild`. It never touches `im`, and it's safe to re-run.

Run it as root with `im-rebuild` (`install/bin/im-rebuild`, on root's PATH next to `im`). It takes the same
arguments as `im`:

```sh
im-rebuild pending   # what the next run would send to Claude, sending nothing
im-rebuild run       # one pipeline run against im_rebuild (no arguments does the same)
```

It's `im`'s `run` service with `IM_DATABASE_URL` from `rebuild.env`, `IM_CONFIG=/config/rebuild.toml`, and
`IM_VOCAB_PATH` empty, so the rebuild never writes Hearsay's `vocab.json`. Corral doesn't schedule it: every run is
started by hand.

- **Its own spending cap.** `extract.monthly_cap_usd` is checked against `egress.spent_this_month`, the month's sum
  of `im.egress_log` in the database the run is connected to. So `im_rebuild` has its own cap, $100 in
  `rebuild.toml`, apart from `im`'s. Both bill the same Anthropic key: in a month both run, the account can be
  charged up to the two caps together.
- **Matching is off** (`[lattice] enabled = false` in `rebuild.toml`), so the bulk extraction goes first. Set it to
  `true` there to switch matching on; each run reads the file, so nothing needs reinstalling.
- **Names said** (policy C) are kept per database, and `im_rebuild` starts with none: the NAS's CPU finds them as
  payloads need them. For the backlog, re-run `install/gpu.sh` on the dev box after `install/rebuild.sh` (it adds
  the kind `ideamachine.names-rebuild`), then, once the first `im-rebuild run` has imported the stream:
  `corral enqueue ideamachine.names-rebuild --needs gpu --key ideamachine.names-rebuild`.
- **Backups.** The nightly backup also dumps `im_rebuild` while it's there, to `backups/im_rebuild-<date>.dump`,
  kept 14 days like `im`'s.

**Cutover** renames the databases; it isn't scripted. With nothing connected to either (corral's run kind paused,
Lattice, the neutral view and any `im-rebuild` stopped), as `postgres` in the `postgres` database: rename `im` to an
archive name (for example `ALTER DATABASE im RENAME TO im_v1`), then `ALTER DATABASE im_rebuild RENAME TO im`. The
old database is archived, not deleted; dump it once, since the nightly backup covers only `im` and `im_rebuild`.
Then carry `rebuild.toml`'s settings into `im.toml`, delete `config/rebuild.env` and `gpu/names-rebuild.env`, and
re-run `install/nas.sh`, and `install/gpu.sh` on the dev box.

## Where things are

| What | Where |
|---|---|
| Secrets | `/mnt/fast/ideamachine/config/` (`db.env`, `pipeline.env`, `lattice.env`, `cloudflared.env`, and `rebuild.env` while there's a rebuild; root only) |
| Config | `/mnt/fast/ideamachine/config/im/im.toml` (e.g. `extract.interests`; read by the run container), and `rebuild.toml` for `im-rebuild` |
| Database | `/mnt/fast/ideamachine/pg` |
| Nightly dumps (14 days) | `/mnt/fast/ideamachine/backups/` |
| My vocabulary (for Hearsay) | `/mnt/fast/ideamachine/vocab/vocab.json` |
| Kept by hand (trial reports, exports) | `/mnt/fast/ideamachine/saved/` (root and apps only: they quote people) |
| Clones | `/mnt/fast/repos/ideamachine`, `/mnt/fast/repos/lattice` (admin) |
| Logs | `journalctl -t ideamachine-run` (and `-t ideamachine-backup`, `-t ideamachine-db`, `-t lattice`, `-t lattice-tunnel`) |
| Run now | `im` as root (`install/bin/im`; `install/nas.sh` puts it on root's PATH), or `docker compose -f /mnt/fast/repos/ideamachine/install/compose.yaml run --rm run` |
| Any im command | `im check`, `im lattice`, ...: the command runs in the pipeline image, with `saved/` at `/saved` |
| The rebuild database | `im-rebuild` as root, with `im`'s arguments (`install/bin/im-rebuild`); psql: `... exec db psql -U postgres -d im_rebuild` |
| psql | `docker compose -f /mnt/fast/repos/ideamachine/install/compose.yaml exec db psql -U postgres -d im` |
