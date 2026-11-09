# Where fund-house holdings and mentions are disclosed, by market

Holdings filings give "Held" evidence, which podcasts cannot. Each market discloses differently, so use the right source and record its as-of date. Check each host is reachable first; if it is blocked, name the host and use the environment tools to request it, rather than substituting remembered holdings.

## Contents
- US / Canada
- UK
- Europe
- Japan
- Korea
- India
- Australia
- Other markets
- How to read the results

## US / Canada
- **13F-HR** on SEC EDGAR (hosts: www.sec.gov, data.sec.gov): quarterly, US-listed long equity positions of managers above $100M, filed within 45 days of quarter end. Use `scripts/pull_13f.py <out.json> <CIK...>`; it needs a descriptive User-Agent with contact details (SEC fair-access rule). Find a CIK via EDGAR company search (`type=13F-HR`, `output=atom`) and confirm the entity name; holding companies often file no 13F.
- Non-US managers also file 13F for their US-listed holdings (e.g. Baillie Gifford, Schroders, Comgest), but this shows nothing about their home-market positions.
- Canada: SEDAR+ fund filings and fund-company monthly top-10 reports.

## UK
- Fund factsheets (monthly top-10 holdings) and annual/half-year reports from the fund house site.
- Substantial-holding notices (TR-1, from 3%) via the London Stock Exchange RNS feed: shows a fund house building or cutting a stake in a named company.
- Investment trust monthly reports and AIC data: often disclose the full top-10.

## Europe
- National registers of major holdings under the Transparency Directive, for example the Dutch AFM substantial-holdings register, Germany's BaFin voting-rights notifications, France's AMF declarations, and the Nordic regulators.
- UCITS factsheets and KIIDs/semi-annual reports: top-10 or full holdings with a date.
- No pan-European 13F equivalent exists: be explicit that coverage is partial.

## Japan
- **EDINET** (disclosure.edinet-fsa.go.jp): large-shareholding reports (大量保有報告書, above 5%) show which asset managers own named listed companies.
- Asset managers' monthly fund reports (月次運用レポート) and investment-trust reports (運用報告書) list holdings.
- Many documents are Japanese: summarize in English and flag translation.

## Korea
- **DART** (dart.fss.or.kr): large-holding reports (주식등의 대량보유상황보고서, above 5%), plus company filings.
- KOFIA fund disclosure and asset-manager monthly reports for fund holdings.
- National Pension Service holdings are published periodically. Documents are Korean: summarize in English and flag translation.

## India
- **AMFI / AMC sites:** every mutual fund publishes monthly portfolio holdings. This is the richest source in the set.
- NSE/BSE quarterly shareholding patterns: name holders above 1%.
- SEBI FPI and fund disclosures for foreign holders.

## Australia
- **ASX substantial-holder notices** (Forms 603/604/605, from 5%): which fund house crossed a threshold in a named company.
- Manager monthly reports and LIC/LIT monthly NTA releases, which usually list the top-10 holdings.

## Other markets
- Hong Kong: HKEXnews disclosure of interests (from 5%). Singapore: SGX substantial-shareholder notices. Taiwan, Brazil, China: local exchange disclosures plus manager factsheets.
- Where nothing structured exists, fall back to fund-house factsheets and tag the evidence with the factsheet date.

## How to read the results
- A 13F or major-holding notice shows ownership only, not the manager's thesis. Pair it with podcast or letter commentary tagged separately.
- Report cross-house overlap, but separate index-like mega-cap overlap from genuine conviction positions. Compare weight and position count: a 55-stock fund's top holding says more than a 3,900-stock fund's.
- Always record as-of date, filing date and entity name used.
