---
name: "fund-ideas-podcast-screen"
description: "Harvest stock ideas from fund managers' holdings filings, podcasts and interviews across US/Canada, UK/Europe, Japan/Korea, India/Australia, then screen them against the user's saved value thesis with live research and a Buy/Watch/Pass call. Use whenever the user asks for fund-manager picks, underappreciated stocks from podcasts or interviews, 13F or factsheet ideas, or 'what are smart investors talking about', even if they do not say 'skill'."
---

# Fund-manager ideas screen

Goal: find underappreciated companies that serious investors hold or argue for, verify what is actually evidenced, then test each name against the user's current thesis. Sourcing is cheap; the value is in verification and screening.

## 0. Scope and saved rules
- Default regions: US+Canada, UK+Europe, Japan+Korea, India+Australia. Ask only if the user narrows or widens this.
- Read the project's equities-investing and positions-and-pipeline memory files for CURRENT thesis rules, holdings and rejected names. Never hardcode rules; they change.
- Do not invent a ranking of fund houses. Use the user's list, or build one from the podcast pass below and show it for approval.

## 1. Podcast discovery (per region)
- Use finance/investing category charts (Apple, Spotify, YouTube) per country, not overall top-100 charts, which are mostly non-finance. Add fund-house-owned podcasts. Cap about 25-30 shows per country per run; say so and offer to go deeper.
- Charts change daily: record chart name, country and date. Use WebSearch first for any URL, then WebFetch the returned link.
- Keep shows that regularly host fund managers or portfolio managers. Note language (Japanese/Korean shows: summarize in English, flag translation).
- Output a table: country, show, host type (own fund / third party), language, transcript availability (see section 2).

## 2. Transcript sourcing (legitimate text only)
Claude cannot listen to audio, and the network allowlist usually blocks audio downloads. So obtain text this way, in order:
1. Publisher transcript on the show or episode page.
2. YouTube captions or episode page text that is publicly readable.
3. Fund-house write-ups, letters, factsheets, news coverage of the episode.
4. Show notes / descriptions (counts only as "Mentioned").
5. Audio-only with none of the above: list the episode (title, show, date, guest) in a "needs transcript" table and ask the user to paste text or drop a transcript file (they can run Whisper or similar themselves).

Rules: no paywalled or members-only transcripts; do not reproduce transcript text, paraphrase the thesis in one line; never guess what was said.

## 2b. Holdings by market (Held evidence)
Pull dated holdings for the fund houses in play, per market: US/Canada 13F, UK, Europe, Japan (EDINET), Korea (DART), India (AMFI monthly portfolios), Australia (ASX substantial holders), and other markets. Read `references/regional-sources.md` for what each market discloses and where. For US 13F use `scripts/pull_13f.py`. Do not claim "all fund houses": state exactly which houses and filings were covered, and which markets have only partial disclosure.

## 3. Extract ideas and tag evidence
For each name record: fund house/guest, source and date, one-line thesis paraphrase, evidence level.
- **Held** - dated filing or factsheet (13F, quarterly report, factsheet). Record as-of date.
- **Discussed** - readable transcript/episode page.
- **Mentioned** - show notes or news only.
- **Unverified** - heard by user, not found. Keep, flag.
Podcast talk is not a holding: never infer a fund owns a stock from an episode. Where feasible, cross-check discussed names against the guest's latest filing/factsheet to upgrade or contradict.

## 4. Master list
One list per session: fund house, source, as-of date, evidence level, thesis line, US-listed? (Y = NYSE/Nasdaq, OTC = ADR usually not on INDmoney, N = none). Merge duplicates; flag names held or discussed by 2+ houses.

## 5. Thesis fit, then live research
- Apply saved rules (e.g. dividend growth, low volatility, exclusions such as AI exposure, margin of safety, target upside and horizon, barbell). Drop names already held, rejected, or not buyable.
- Test the speaker's claim: is the company actually underappreciated (price vs 52-week range, valuation vs own history, coverage/sentiment) or has the idea already run?
- For every survivor, live research before any view: latest quarter, guidance, price vs 52-week range, valuation vs history, dividend and payout, balance sheet, next dated catalyst, and what would break the thesis. Cite sources with dates.

## 6. Output
- Lead with the answer: ranked survivors, each with Buy / Watch / Pass, evidence level, one-line thesis, key numbers, catalyst, and realistic expected return with horizon. Do not imply returns the numbers do not support. Say plainly when nothing clears the bar.
- Separate sections: ranked shortlist, dropped names and why, "needs transcript" episodes, unverified items.
- Mark the verdict as research, not financial advice. Offer one next step (e.g. deep-dive the top name, or process pasted transcripts).
