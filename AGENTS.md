# Working on Idea Machine

Turns transcripts of recorded conversations into a searchable map of the ideas in them.

Read before changing behavior:

- [V2 spec](docs/SPEC.md): what V2 changes, its contracts and data model
- [V2 roadmap](docs/ROADMAP-v2.md): V2's milestones and benchmarks
- [V1 roadmap](docs/ROADMAP.md): V1's scope, how it imports Hearsay's stream, and phases (frozen; code comments cite it)
- [Hearsay's stream contract](https://github.com/junglestyle/hearsay/blob/main/docs/stream.md): what Idea Machine reads from Hearsay, and the vocabulary it writes back
- [Coding standards](docs/standards.md): how the code should read, and the invariants it keeps, on top of the org's [principles](https://github.com/junglestyle/.github/blob/main/PRINCIPLES.md) and standards (`docs/org-standards*.md`)
- [Decisions](docs/decisions/): what was measured and decided, and why
- [Installing and running](docs/install.md): setup, commands, configuration and how `im run` works
- [Deploying to the NAS](docs/deploy.md)

Run `make check` before committing. It runs everything CI runs, except the org standards check: `make standards` (needs the network).
