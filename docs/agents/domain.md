# Domain docs

This repository uses a single-context layout:

- `GLOSSARY.md`: canonical domain vocabulary.
- `docs/architecture.md`: current implementation and boundary flows.
- `docs/plans/`: planning material; document status determines whether it is a
  proposal, historical brief, or current summary, not acceptance by location.
- `docs/adr/`: ADR location if decisions are recorded; currently absent.

Before exploring domain concepts, read the glossary and current architecture,
then relevant ADRs if present. Create glossary entries or ADRs only when terms
or decisions are resolved; documenting a proposal does not approve it.

Use glossary terms consistently in code, tests, tickets, and proposals.
Flag conflicts with existing ADRs rather than silently overriding them.
