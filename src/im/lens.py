"""Lenses (decision 0005, amended 2026-11-02): a lens says how relevant every cluster is to its seed. It never says
where anything goes: positions are the pipeline's, one map for every view.

Relevance is personalized PageRank from the seed over every edge (pub.card_links), with the seed's nearest cards by
meaning (pub.card_neighbours) added as edges from the seed, weighted by similarity. The seed is 1; the rest are
scaled so the most relevant other cluster is 1 too, so a lens always has something lit around its seed. With no
seed there's nothing to compute: every cluster is equally relevant, and the page's encodings light the sky. A global
PageRank would be a score of what matters, which decision 0007 rules out.

It reads only pub, as the web app's role (lattice_app).
"""

from collections import defaultdict

RESTART = 0.15     # the chance each step of jumping back to the seed
NEAR = 20          # nearest by meaning, joined to the seed
STEPS = 60


def relevance(conn, seed: str) -> dict[str, float]:
    """Cluster id -> relevance to the seed, in [0, 1]. Clusters it doesn't reach are left out (0)."""
    weights: dict = defaultdict(lambda: defaultdict(float))
    for a, b in conn.execute("SELECT a::text, b::text FROM pub.card_links"):
        if a != b:
            weights[a][b] += 1.0
            weights[b][a] += 1.0
    for other, similarity in conn.execute("SELECT idea_id::text, similarity FROM pub.card_neighbours(%s, %s)",
                                          (seed, NEAR)):
        weights[seed][other] += max(float(similarity), 0.0)
        weights[other][seed] += max(float(similarity), 0.0)
    if seed not in weights:
        return {seed: 1.0}
    out_sum = {n: sum(w.values()) for n, w in weights.items()}
    r = {seed: 1.0}
    for _ in range(STEPS):
        nxt = defaultdict(float, {seed: RESTART})
        for n, mass in r.items():
            for m, w in weights[n].items():
                nxt[m] += (1 - RESTART) * mass * w / out_sum[n]
        r = nxt
    top = max((v for n, v in r.items() if n != seed), default=0.0)
    return {n: (1.0 if n == seed else round(min(v / top, 1.0), 4)) for n, v in r.items() if v > 0 and top > 0}
