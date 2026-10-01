"""The projects registry: what the "which project" triage question chooses from."""


def add(conn, slug: str, description: str | None, aliases: list[str]) -> str:
    """Create a project, or update an existing one's description and add aliases."""
    with conn.transaction():
        row = conn.execute("SELECT 1 FROM im.projects WHERE slug = %s", (slug,)).fetchone()
        if row is None:
            if not description:
                raise ValueError("a new project needs a description")
            conn.execute("INSERT INTO im.projects (slug, aliases, description) VALUES (%s, %s, %s)",
                         (slug, sorted(set(aliases)), description))
            return "added"
        conn.execute(
            """UPDATE im.projects SET description = coalesce(%s, description), retired_at = NULL,
                 aliases = ARRAY(SELECT DISTINCT unnest(aliases || %s::text[]) ORDER BY 1)
               WHERE slug = %s""", (description, aliases, slug))
        return "updated"


def retire(conn, slug: str) -> None:
    if conn.execute("UPDATE im.projects SET retired_at = now() WHERE slug = %s AND retired_at IS NULL",
                    (slug,)).rowcount == 0:
        raise ValueError(f"no active project {slug!r}")


def active(conn) -> list[tuple[str, list[str], str]]:
    return conn.execute("SELECT slug, aliases, description FROM im.projects WHERE retired_at IS NULL "
                        "ORDER BY slug").fetchall()


def listing(conn) -> str:
    rows = conn.execute("SELECT slug, aliases, description, retired_at FROM im.projects ORDER BY slug").fetchall()
    if not rows:
        return "no projects yet: im project add <slug> --description '...'"
    return "\n".join(f"{slug:<20} {description}" + (f"  (aka {', '.join(aliases)})" if aliases else "")
                     + ("  [retired]" if retired else "") for slug, aliases, description, retired in rows)
