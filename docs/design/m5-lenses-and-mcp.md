# M5: the MCP server here, and lenses

_2026-11-14. Status: plan, for discussion; nothing built. The design ROADMAP-v2's M5 row asks for. It settles how
[decision 0005](../decisions/0005-presentation-inside-idea-machine.md)'s MCP server moves in under
[SPEC §2](../SPEC.md#2-contracts)'s contract, and the storage [decision 0013](../decisions/0013-lenses-and-boards.md)
leaves to M5. The questions only I can answer are at the end._

**Done when** (ROADMAP-v2): the claude.ai connector works unchanged against the new endpoint, and 5 canonical lenses
are judged useful.

## Today

- No MCP server runs. Lattice served it until 2026-11-11 ([deploy.md](../deploy.md), "Lattice, removed"): 19 tools,
  10 that read and 9 that write (lattice `src/lattice/mcp.py:441-520`), on a tailnet listener with a bearer token and
  a public one behind OAuth, a Cloudflare tunnel and two WAF rules (lattice `docs/public-mcp.md`). The tunnel, the
  WAF rules and the claude.ai connector were removed by hand.
- `im web` (`src/im/web.py`) is one `ThreadingHTTPServer` as role `lattice_app`, on 127.0.0.1:8792 and, through
  `tailscale serve`, the tailnet on 8445. Its container runs the pipeline's image, so the `models` extra
  (sentence-transformers) is there.
- A lens is computed, never stored: `lens.relevance(seed)` lights the sky from one cluster. Themes are their own
  tables (`im.themes`, `im.idea_themes`), filed by Claude (`themes.file_stage`) and edited by my events (024). The
  Tree (`/tree`) and Grid (`/grid`) draw graph neighborhoods as stand-ins for lenses.

## Part 1: the MCP server

### Where it runs

In `im web`, as Lattice's did in `lattice serve`: the same process, role and container, two listeners.

- **Tailnet:** `/mcp` and `/demo/mcp` on the existing listener, with `Authorization: Bearer $IM_WEB_TOKEN`. It's
  Lattice's `LATTICE_TOKEN` renamed (deploy.md), so the value is the one the old connector config used.
- **Public:** a second listener on 127.0.0.1:8791 that serves MCP and OAuth and nothing else, opened only when
  `IM_PUBLIC_URL` is set, behind a Cloudflare tunnel (a `cloudflared` container again in `install/compose.yaml`)
  and the two WAF rules, as lattice `docs/public-mcp.md` describes. `IM_MCP_ALLOW` (default `160.79.104.0/21`)
  replaces `LATTICE_MCP_ALLOW`.

The code moves in from Lattice nearly as it is: `mcp.py` (JSON-RPC over streamable HTTP, plain JSON responses, no
sessions; protocol versions 2025-11-25, 2025-06-18, 2025-03-26), `oauth.py` (its own OAuth 2.1 server: dynamic
registration for Claude's two callbacks only, PKCE S256, one-hour access tokens, 30-day rotated refresh tokens,
everything signed with a key derived from the token, nothing stored), and the listener and allowlist parts of
`server.py` and `auth.py`. As `src/im/mcp.py` and `src/im/oauth.py`, with `web.py` routing to them. The tools read
`pub` through `lattice_app`, never `im`, and append `pub.feedback_events` with `source = 'mcp'`.

### The 19 tools against today's `pub`

The contract is the tools' names, argument schemas and result shapes, not the views Lattice read. Each tool is
rewritten here against what `pub` has now, and keeps its shapes.

| Tool | Reads | Missing today |
|---|---|---|
| `search_ideas` | `pub.ideas`, matching title, statement, notes, my note, themes | `pub.ideas.notes`, `my_note` |
| `get_idea` | `pub.ideas`, `pub.evidence`, `pub.connections`, the rubric score | `notes`, `archive_ref`, `claude_title`, `claude_statement`, `my_note`, `corrected`; `pub.idea_scores` (053) |
| `best_ideas` | `pub.idea_scores`, `pub.ideas` | `pub.idea_scores` (053) |
| `list_themes`, `open_theme` | `pub.themes`, `pub.ideas` | nothing |
| `whats_new` | `pub.ideas`, `pub.proposals`, `pub.themes` | nothing |
| `weekly_digest` | `pub.digests`, `pub.ideas` | nothing |
| `draft_material` | as `get_idea`, plus a theme or a digest point | as `get_idea` |
| `similar_ideas` | `pub.idea_neighbours` by id; `pub.ideas_near(vector, int)` by text | both (058) |
| `rename_theme`, `merge_themes`, `split_theme`, `file_idea` | `pub.themes`, `pub.ideas`; theme events | nothing |
| `add_idea`, `proposals`, `keep_idea`, `star_idea`, `link_ideas`, `note_idea` | `pub.proposals`; feedback events | nothing |

(Lattice: `src/lattice/mcp.py:75-436`, `data.py:46-253`, `meaning.py:46-67`.) What `pub` gains:

- **`pub.ideas` gets its columns back,** appended so the view is replaced, not rebuilt: `my_note`, `claude_title`,
  `claude_statement` and `corrected` from `im.idea_text`; `notes` (the old archive elaboration) and `archive_ref`
  as NULL, since nothing has either since 057 and 058. `get_idea` returns `ref: null` and `notes: null`, which its
  shape allows.
- **`pub.ideas_near(vector, int)`** again, over `im.idea_embeddings` in `pub.embedding_model()`, for search by text.
  The query is embedded in the web process with `lattice.SentenceEmbedder`, the code that embedded the ideas, so
  the vectors match by construction (Lattice needed FastEmbed and a parity test). Loaded on the first text search:
  torch costs the web container a few hundred MB from then on. [UNMEASURED]
- **No `pub.idea_neighbours`:** by id, `similar_ideas` asks `pub.card_neighbours(seed, k)`, which already exists.

**The rubric's three tools.** Nothing has been scored since [0007](../decisions/0007-neutral-proposals.md), and 053
dropped the scores. The contract already has a shape for "nothing scored", which Lattice's tests pin (lattice
`tests/test_mcp.py:102-121`): `best_ideas` answers with a tool error, `get_idea` leaves out `ranking`, and
`draft_material` gives every idea `score: null`. So that's what they do, and `best_ideas`'s error says why ("ideas
aren't rated: decision 0007"). Question 1 below.

**Pseudonymization** stays as Lattice did it (`mcp.py:50-57`): in `get_idea`'s evidence and in each
`draft_material` answer, every speaker but `me` and `unknown` becomes `S1`, `S2`... in order of appearance, one
numbering per answer. It covers speaker labels only: quotes, titles and digest text go as they are, as they did.

**Demo mode.** `/demo/mcp` serves the nine read tools Lattice's did (not `proposals`, no writes). What it hides
comes from `pub.demo_hidden_ideas` and `pub.sensitivity`, the web view's own demo mode, instead of Lattice's
`LATTICE_DEMO_HIDE` category list (gone with `lattice.env`), so the two can't disagree. Swear words are blacked out
in every string, as Lattice did (`mcp.py:533-582`). Question 4.

**Verified by** Lattice's tests, ported (lattice `tests/test_mcp.py`, `test_audit.py`, `test_public_mcp.py`): the
handshake and errors, no raw speaker label in any answer, the exact feedback each write appends, the theme edits,
drafting, the OAuth flow and token binding, and demo mode. Then the done-when check: the claude.ai connector, added
again, calls a read and a write tool.

### `open_lens`

The one new tool: `open_lens(lens, limit)`, a lens by name or id. It answers with the lens's name, description,
how much of the map it has judged ("judged 300 of 1,065"), and its members as brief ideas, each with why it's in (a
pin, or the query's score). In demo mode too, with the same hiding.

## Part 2: lenses

As [0013](../decisions/0013-lenses-and-boards.md) decided: a lens is a query, filters and pins; a cluster is in it
if it's pinned in, or it passes the query and the filters and isn't pinned out. Viewing from a cluster
(`lens.relevance`) stays as it is.

### Storage

My input is feedback events, as for themes (024): a lens is made, edited, pinned, archived and placed on a board by
events (`lens_add`, `lens_edit`, `lens_pin`, `lens_unpin`, `lens_archive`, `board_place`...), applied on insert by
a trigger, so the web view and MCP can do it as `lattice_app`. The tables they write:

- **`im.lenses`:** id, name, description, `query` (jsonb: its kind and that kind's parameters), `query_version`
  (the hash of `query`), `filters` (jsonb), `state` (`active`, `pinned` or `archived`), created.
- **`im.lens_pins`:** lens, in or out, and what it's anchored to: the pinned capture's `segment_ids`, or an
  archive or typed observation's `source_ref`. A view resolves each pin to the cluster that holds that speech now.
  Forgetting a pin's segments deletes the pin.
- **`im.lens_answers`:** lens, cluster, `query_version`, the cluster's text hash, in or out, score, reason, and the
  egress row it came from. Derived: it cascades with the cluster, and a changed text or version is asked again.
- **`im.boards`, `im.board_lenses`:** a board's name, and each lens on it with its position.
- **`pub.lenses`, `pub.lens_members`** (lens, cluster, why: `pin`, `query`; score), and demo versions that leave out
  `pub.demo_hidden_ideas`.

### Query kinds

`all` and `none` first, then `jev`: a set of Nouls, a rule combining them (`any k at ≥ cut`, `mean ≥ cut`) and the
cut. The state Jev sees is the cluster's title and statement, as the sensitivity tagger's is. A `jev` lens's run
asks, by default, only the clusters nearest its description by embedding, up to a maximum; `im lens run ID --all`
asks the rest. Every request goes through `egress.send` and the monthly cap. At Jev's price a lens over the archive
costs about a cent; the cost is time, a request per cluster. [UNMEASURED]

Filters, first: maturity, kept as, said by me, said since or until, sensitive. Each is a condition on `pub.cards`
and `pub.sensitivity`, checked in SQL.

**Editing a query is my request to ask again,** so it isn't held by the re-read gate: the gate holds what a version
change would send unasked. A new Jev model or question wording that I didn't make is held, as every other stage's
is.

### Where they show

- `/lens/<id>`: the members, and why each is in; pin in or out from a card.
- The sky lit by a lens, as from a cluster now, through `/sky/lens`.
- The Tree and the Grid take their lenses from a board instead of neighborhoods, once lenses exist. Laying out a
  board by hand is M6's (Trellis).
- `im lens list | show | run | import FILE`, the last for a long query built offline (0013).

### Themes

0013 makes themes lenses: a theme's name and description its query, my filing corrections its pins. That changes
who files: today Claude files every new idea against all the themes in one request (`themes.file_stage`); as
lenses, Jev would ask each idea about each theme. The theme tools keep their shapes either way. Question 3.

## Slices

One PR each, in order:

1. `pub` additions and the MCP server on the tailnet (`/mcp`, `/demo/mcp`), with Lattice's tests ported.
2. The public listener, OAuth, and the tunnel in `install/`. Done when the connector works.
3. Lens storage and events, kinds `all` and `none`, pins, filters, `pub.lens_members`, `/lens/<id>`, `open_lens`.
4. The `jev` kind and `im lens run`.
5. The 5 canonical lenses, judged under a bar registered in im-evals first.
6. Themes into lenses, if question 3 says so.

## Questions for me

1. **The rubric's tools.** The "nothing scored" shapes above (`best_ideas` an error), or should `best_ideas` answer
   something else that keeps its shape, such as the most recently said kept ideas?
2. **The public endpoint.** The Cloudflare tunnel again, as Lattice had it (proven; two WAF rules to switch by hand
   when adding a connector), or Tailscale Funnel (no Cloudflare account; no `CF-Connecting-IP`, so the address
   check moves to whatever Funnel passes on)?
3. **Themes.** Migrate them into lenses in M5, with Jev filing in Claude's place once it agrees with my filing
   corrections on a marked sheet, or keep themes as they are and leave the migration to after M5?
4. **Demo over MCP.** One definition of hidden (the web view's), or keep Lattice's category list for MCP?
5. **The 5 canonical lenses.** Which five, and what's "judged useful"? A proposal: for each, a blind sheet of 20
   members and 20 near non-members, which I mark in or out; it passes at precision ≥ 0.8 among the members, and I
   say whether I'd open it again. Registered in im-evals before any run.
6. **Boards in M5.** Storage only (the Tree and Grid read a board), with laying out by hand in M6?
