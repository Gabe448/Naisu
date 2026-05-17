# Claude Skills Reference Guide

A synthesized reference of skills learned from four repositories:
- [JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman)
- [nextlevelbuilder/ui-ux-pro-max-skill](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill)
- [ComposioHQ/awesome-claude-skills](https://github.com/ComposioHQ/awesome-claude-skills)
- [obra/superpowers](https://github.com/obra/superpowers)

---

## Table of Contents
1. [Caveman Mode — Token Compression](#1-caveman-mode--token-compression)
2. [UI/UX Pro Max — Design Intelligence](#2-uiux-pro-max--design-intelligence)
3. [Awesome Claude Skills — Curated Skill Catalog](#3-awesome-claude-skills--curated-skill-catalog)
4. [Superpowers — Software Development Workflow](#4-superpowers--software-development-workflow)

---

## 1. Caveman Mode — Token Compression

**Source:** JuliusBrussee/caveman
**Trigger:** `/caveman`, "talk like caveman", "less tokens please"
**Purpose:** Compress AI responses ~75% by speaking like a caveman while keeping full technical accuracy.

### Intensity Levels

| Level | Behavior |
|-------|----------|
| `lite` | Remove filler/hedging words; keep grammar intact |
| `full` *(default)* | Drop articles, allow sentence fragments, use shorter synonyms |
| `ultra` | Abbreviate terms, eliminate conjunctions, use symbols (→, ∴, ∵) for logic flow |

### Rules
- Code blocks, technical terms, and error messages are **never** altered
- Auto-revert to normal English for:
  - Security warnings
  - Irreversible action confirmations
  - Complex multi-step sequences where brevity could cause misunderstanding
- Never applies to: commit messages, pull request descriptions, or code itself

### Benchmarked Savings
| Scenario | Token Reduction |
|----------|----------------|
| React re-render bug | 87% |
| Auth middleware | 83% |
| PostgreSQL setup | 84% |
| Average | ~65% |

### Usage Examples
```
/caveman                    # Activate full mode
/caveman lite               # Activate lite mode
/caveman ultra              # Activate ultra mode
talk like caveman           # Natural language trigger
less tokens please          # Natural language trigger
```

---

## 2. UI/UX Pro Max — Design Intelligence

**Source:** nextlevelbuilder/ui-ux-pro-max-skill
**Version:** 2.5.0 | **License:** MIT | **Homepage:** https://uupm.cc
**Purpose:** Generate complete UI/UX design systems across 15+ tech stacks using AI-powered design intelligence.

### Design Database
| Resource | Count |
|----------|-------|
| UI styles (general + landing + BI/analytics) | 67 |
| Color palettes (product-category aligned) | 161 |
| Font pairings (Google Fonts) | 57 |
| Industry-specific reasoning rules | 161 |
| UX guidelines (incl. accessibility) | 99 |
| Chart types | 25 |

### Supported Stacks
React, Next.js, Vue, Svelte, SwiftUI, React Native, Flutter, Tailwind, and 7+ more (15 total)

### Supported AI Platforms
Claude, Cursor, Windsurf, GitHub Copilot, Gemini, and 13+ more (18 total)

### Search Interface
```bash
python3 src/ui-ux-pro-max/scripts/search.py "<query>" --domain <domain>
```

**Domains:**
| Domain | Description |
|--------|-------------|
| `product` | Product category reasoning rules |
| `style` | UI style definitions |
| `typography` | Font pairing recommendations |
| `color` | Color palette selection |
| `landing` | Landing page specific styles |
| `chart` | Chart type selection |
| `ux` | UX guidelines and accessibility |

### Core Approach
- **Retrieval:** BM25 ranking + regex matching (hybrid search)
- **Architecture:** Template-based generation; source of truth in `src/ui-ux-pro-max/`
- **Propagation:** Symlinks push changes to platform-specific directories
- **Rule:** No direct commits to main — all changes via pull requests

### Install
```bash
npx uipro-cli init --ai claude
```

---

## 3. Awesome Claude Skills — Curated Skill Catalog

**Source:** ComposioHQ/awesome-claude-skills
**Purpose:** A curated collection of 38+ ready-to-use Claude skills and automation integrations.

### Skill Categories

#### Content & Marketing
| Skill | Description |
|-------|-------------|
| `brand-guidelines` | Enforce brand voice and style consistency |
| `content-research-writer` | Research-backed content generation |
| `changelog-generator` | Auto-generate changelogs from git history |
| `twitter-algorithm-optimizer` | Optimize posts for Twitter/X algorithm |
| `internal-comms` | Internal communications drafting |

#### Developer Tools
| Skill | Description |
|-------|-------------|
| `mcp-builder` | Build MCP (Model Context Protocol) servers |
| `webapp-testing` | Web application test generation and execution |
| `skill-creator` | Create new Claude skills from scratch |
| `skill-share` | Share and distribute skills |
| `langsmith-fetch` | Fetch and analyze LangSmith traces |

#### Document Handling
| Skill | Description |
|-------|-------------|
| `document-skills` | General document processing |
| `file-organizer` | Organize and rename files intelligently |
| `invoice-organizer` | Parse and organize invoice files |
| `tailored-resume-generator` | Generate job-tailored resumes |
| `artifacts-builder` | Build Claude artifacts |

#### Research & Leads
| Skill | Description |
|-------|-------------|
| `lead-research-assistant` | Research and qualify sales leads |
| `competitive-ads-extractor` | Extract competitor advertising data |
| `developer-growth-analysis` | Analyze developer community growth |
| `meeting-insights-analyzer` | Extract insights from meeting transcripts |

#### Creative & Media
| Skill | Description |
|-------|-------------|
| `canvas-design` | Design on canvas-based tools |
| `image-enhancer` | Enhance and transform images |
| `slack-gif-creator` | Create GIFs for Slack |
| `video-downloader` | Download and process videos |
| `theme-factory` | Generate color themes and design tokens |

#### Automation (Composio Integrations)
Hundreds of third-party service integrations including:
- **Communication:** Slack, Gmail, Outlook, Teams, Discord
- **Project Mgmt:** Asana, Jira (Atlassian), Linear, Trello
- **Dev Tools:** GitHub, GitLab, Bitbucket, Vercel
- **CRM/Sales:** Salesforce, HubSpot, Apollo
- **Marketing:** Ahrefs, Active Campaign, Adobe
- **Cloud:** AWS (Amazon), Google Cloud, Azure (Auth0)
- **Analytics:** Alpha Vantage, and many more

#### Other
| Skill | Description |
|-------|-------------|
| `raffle-winner-picker` | Randomly select raffle winners |
| `domain-name-brainstormer` | Generate domain name ideas |
| `connect-apps` | Connect and integrate applications |

### Creating a New Skill (Template)
Skills follow the `template-skill` scaffold — create a `SKILL.md` with:
```markdown
# Skill Name
**Trigger:** when to activate this skill
**Purpose:** what this skill does

## Instructions
[Step-by-step instructions for Claude to follow]
```

### Install Any Skill
```bash
npx skills add <org>/<skill-name>
```

---

## 4. Superpowers — Software Development Workflow

**Source:** obra/superpowers
**Authors:** Jesse Vincent & Prime Radiant
**License:** MIT
**Purpose:** A structured, multi-stage software development workflow for coding agents.

### Meta Rule
> Before ANY response, check if any superpowers skill applies. Invoke it if there is even a 1% chance it is relevant. (`using-superpowers` skill)

---

### Skill: Brainstorming (`superpowers:brainstorming`)
**Trigger:** Before starting any non-trivial implementation

**9-Step Design-First Process:**
1. Restate the problem in your own words
2. Identify constraints and requirements
3. List assumptions being made
4. Generate 3+ solution approaches
5. Evaluate trade-offs for each approach
6. Recommend an approach with justification
7. Identify risks and unknowns
8. Write a spec document
9. **Block all implementation until user approves the written spec**

---

### Skill: Writing Plans (`superpowers:writing-plans`)
**Trigger:** After brainstorming is approved, before coding

**Rules:**
- Create detailed TDD-structured implementation plans
- Save plans to `docs/superpowers/plans/`
- Plans must include: file changes, test strategy, acceptance criteria
- Plans reference the approved spec from brainstorming

---

### Skill: Test-Driven Development (`superpowers:test-driven-development`)
**Iron Law:** No production code without a prior failing test.

**Red-Green-Refactor Cycle:**
1. **Red:** Write a failing test that describes desired behavior
2. **Green:** Write the minimum code to make the test pass
3. **Refactor:** Clean up while keeping tests green

**Rules:**
- Tests must be written FIRST, always
- Commit the failing test before writing implementation
- Never skip the red phase — if a test passes immediately, it may be testing the wrong thing

---

### Skill: Systematic Debugging (`superpowers:systematic-debugging`)
**Trigger:** When encountering bugs, unexpected behavior, or errors

**4-Phase Methodology:**

| Phase | Action |
|-------|--------|
| 1. Root Cause Investigation | Gather all evidence; read error messages fully; check logs |
| 2. Pattern Analysis | Identify what changed; find similar past incidents |
| 3. Hypothesis Testing | Form hypotheses; test one at a time; don't shotgun |
| 4. Implementation | Fix the root cause, not the symptom |

**Rules:**
- Never guess — form and test hypotheses
- Never apply multiple fixes simultaneously
- Document what you tried and why

---

### Skill: Subagent-Driven Development (`superpowers:subagent-driven-development`)
**Trigger:** For large tasks that can be parallelized

**Process:**
1. Break task into discrete, independent subtasks
2. Dispatch fresh subagents per task (no shared state)
3. **Stage 1 Review:** Spec compliance check
4. **Stage 2 Review:** Code quality check
5. Integrate results after both review stages pass

---

### Skill: Receiving Code Review (`superpowers:receiving-code-review`)
**Rules:**
- Technical verification over performative agreement
- Prohibited responses: "great point!", "you're absolutely right!", "I'll fix that right away!"
- Instead: verify the claim technically, then respond with findings
- If the reviewer is wrong, say so with evidence
- If the reviewer is right, explain why and fix it

---

### Skill: Verification Before Completion (`superpowers:verification-before-completion`)
**Iron Law:** Never claim work is complete without running and showing actual command output.

**Required before marking any task done:**
```bash
# Show actual output — never fabricate
<run the relevant test/build/lint command>
<paste the actual terminal output>
```

---

### Skill: Git Worktrees (`superpowers:using-git-worktrees`)
**Purpose:** Isolated development branches for parallel work

```bash
# Create worktree for a feature
git worktree add ../feature-branch -b feature/my-feature

# List worktrees
git worktree list

# Remove when done
git worktree remove ../feature-branch
```

---

### Skill: Writing Skills (`superpowers:writing-skills`)
**Structure for a new skill:**
```markdown
# Skill Name

**Trigger:** [exact conditions to invoke this skill]
**Purpose:** [one sentence on what it does]

## Rules
[Numbered list of enforceable rules]

## Process
[Step-by-step workflow]

## Examples
[Concrete examples]
```

**Quality Bar:**
- Skill must have measurable, testable outcomes
- No third-party dependencies
- No domain-specific assumptions
- Must include before/after eval results for submission

---

### Skill: Dispatching Parallel Agents (`superpowers:dispatching-parallel-agents`)
**When to use:** Tasks with 2+ independent workstreams

**Rules:**
- Each agent gets a clean context — no shared state
- Define clear input/output contracts per agent
- Use a coordinator agent to integrate results
- Never dispatch agents for tasks with sequential dependencies

---

### Skill: Finishing a Development Branch (`superpowers:finishing-a-development-branch`)
**Checklist before merge:**
- [ ] All tests pass (show output)
- [ ] No TODOs left unaddressed
- [ ] Plan in `docs/superpowers/plans/` marked complete
- [ ] PR description explains the *why*, not just the *what*
- [ ] Reviewers assigned

---

## Quick Reference: When to Use Which Skill

| Situation | Skill to Apply |
|-----------|---------------|
| Want shorter responses | Caveman Mode |
| Building UI/frontend | UI/UX Pro Max |
| Need a 3rd-party integration | Awesome Claude Skills / Composio |
| Starting a non-trivial feature | Superpowers: Brainstorming → Writing Plans |
| Writing any code | Superpowers: TDD |
| Something is broken | Superpowers: Systematic Debugging |
| Large parallelizable task | Superpowers: Subagent-Driven Development |
| Finishing work | Superpowers: Verification Before Completion |
| Merging a branch | Superpowers: Finishing a Development Branch |
| Creating a new skill | Awesome Claude Skills template + Superpowers: Writing Skills |

---

*Last updated: 2026-04-05*
