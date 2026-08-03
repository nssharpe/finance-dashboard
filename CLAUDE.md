See [AGENTS.md](AGENTS.md) — one briefing, shared by every agent tool.

Two things not to skip:

- **The financial data never leaves the machine.** Reading `finance.db` locally
  to answer questions about spending is the whole point of this tool. But nothing
  derived from real data may reach the repo, a commit, an issue, a doc, or a test
  fixture — invent example data for those. `config.json` holds a bearer
  credential and shouldn't be displayed at all.
- **The dangerous bugs here are silent, not loud.** Sign conventions,
  de-duplication, and categorization precedence all fail by producing a wrong
  number rather than an error. See "Silent failures" in AGENTS.md.
