# Frontier AI discourse, live

A continuously updated [Latent Lab](https://latentlabdev.media.mit.edu) map of how AI is talked about. It covers three layers:

- **company**: blogs and newsrooms of the six Frontier Model Forum members (Amazon, Anthropic, Google, Meta, Microsoft, OpenAI)
- **news**: The Verge, TechCrunch, Wired, Ars Technica, VentureBeat, MIT Technology Review, BBC and The Guardian
- **policy**: US federal agencies (Federal Register, White House, NIST, FTC), US Congress, the California governor, and the EU (Commission, Parliament, Council)

Each item is a short topic description with names masked, written from the source document. Branch `claude/live-data` holds the items, and Latent Lab syncs from that branch. Branch `main` holds the collector and the update instructions (`CLAUDE.md`). A scheduled Claude Code routine runs the update daily.

Part of a MIT Media Lab research project. Source documents belong to their publishers; only links and short derived descriptions are stored here.
