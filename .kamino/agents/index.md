# Register of available agents

Each `## section` is a category. Agent paths are **tiered**, and the factory must search them in this order:

1. `library/…` — curated, tested agents. **Search here first.**
2. `ad-hoc/…` — agents the factory created on the fly, kept for reuse. **Fall back here** only if no library agent fits.
3. If no agent fits in either tier, create a new **ad-hoc** agent from `agent-blueprint.template.md` (see `createblueprint`). New agents are always written to `ad-hoc/`; promote them to `library/` after they are validated.

## Writing

library/writing/article-writing-agent.md - An agent to write an article based on a provided goal, styleguide, and context
library/writing/article-review-agent.md - An agent to review an article based on a provided styleguide
library/writing/article-fact-checking-agent.md - An agent to critically fact-check an article against the provided source material

## Coding

library/coding/python-coding-agent.md - Solves a specified Python coding problem by writing a complete solution.py that satisfies the problem's function signature, self-checking against the provided test file when possible and reporting the self-check result honestly (promoted after 151 verified successful runs)
library/coding/python-coding-agent-single-shot.md - Single-shot no-oracle variant: writes solution.py once from the problem statement alone, with no test access and no revision loop; used to measure a model's unaided capability ceiling (promoted after 21 verified successful runs)
