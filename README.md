# kycscreen

Customer onboarding screening for a made-up fintech. It checks people against a sanctions and PEP watchlist with fuzzy name matching, works out who really owns a business customer by following ownership chains, and gives every customer a low, medium, or high risk rating with the reasons listed. The matcher is pure Python: normalization, transliteration tables, blocking with phonetic keys and trigrams, and an explainable score built from Jaro-Winkler, token sets, date of birth, and nationality. An evaluation over 20,000 synthetic customers measures the tradeoff between missed hits and analyst workload, and CI fails the build if recall at the configured threshold drops below 99%.

All names, companies, and lists are fictional and generated in code. Nothing here is a real sanctioned person or entity.

## Results

20,000 synthetic customers, 400 true matches hidden among them with realistic spelling changes, 160 namesakes (same name as a listed person, different person), 3,000 watchlist entries with 2,359 aliases. Output of `uv run kycscreen eval`:

| Method | Threshold | Recall | Precision | Alerted customers per 1,000 |
|---|---:|---:|---:|---:|
| Exact match on the lowercased name | n/a | 22.8% | 22.4% | 20.3 |
| Levenshtein ratio, lowest threshold tried (40) | 40 | 88.8% | 0.3% | 872.6 |
| Levenshtein ratio at the same workload as this engine | 88 | 47.8% | 25.5% | 37.0 |
| **This engine, recommended threshold** | **71.5** | **99.0%** | **49.9%** | **37.2** |

At the recommended threshold the engine alerts on 745 of 20,000 customers. 396 of those are true matches; 349 are false positives an analyst clears. The plain Levenshtein baseline never reaches 99% recall at any threshold, and at the same workload it misses more than half of the true matches.

The tradeoff is steep near the top. Moving the threshold from 80 to 71.5 raises recall from 91.0% to 99.0% and almost doubles the work (401 to 745 alerted customers):

| Threshold | Recall | Precision | Alerted per 1,000 | False alerts per 1,000 |
|---:|---:|---:|---:|---:|
| 60 | 99.8% | 14.9% | 106.8 | 86.9 |
| 70 | 99.0% | 46.8% | 39.4 | 19.7 |
| **71.5** | **99.0%** | **49.9%** | **37.2** | **17.4** |
| 75 | 97.2% | 53.4% | 34.8 | 15.4 |
| 80 | 91.0% | 90.3% | 20.0 | 1.9 |
| 90 | 65.0% | 97.7% | 13.3 | 0.3 |

The full chart is in [`reports/tradeoff.html`](reports/tradeoff.html) (static HTML and SVG, also served at `/report`).

Scale and speed, single process, pure Python:

| Measure | Value |
|---|---|
| Throughput | about 700 names per second (20,000 customers in 28 s) |
| Blocking recall | 99.75% (the true entry is in the candidate set for 399 of 400 matches) |
| Candidates per customer | 31.5 out of 5,359 indexed names (99.4% of comparisons skipped) |
| Delta rescreen after a list update (40 added, 25 amended, 15 removed) | 0.64 s versus 18.7 s for a full rescreen, 29x faster, identical alerts |

## Why

Name screening is where onboarding teams spend most of their review hours. Exact matching misses "Mohamed" when the list says "Muhammad", so nobody uses it. Loose fuzzy matching catches the variants but alerts on a large share of customers, and every alert is a case an analyst has to open, read, and close with a written reason. The threshold is a staffing decision as much as a risk decision, and it is usually set by feel. This project makes it a measured choice: label the data, sweep the threshold, pick the point that meets the recall target, and show the workload that comes with it. Then make the recall target a test, so a change to normalization or scoring that drops recall fails CI instead of reaching production.

Business customers add a second problem. The name on the account is rarely the person who controls it. Beneficial ownership rules (25% in most regimes) require following holdings through intermediate companies, multiplying along each chain and adding across chains, and noticing structures built to hide that: circular holdings, nominee directors, and stakes split just under the threshold.

## Architecture

```
 customer name, DOB, nationality
        |
        v
 normalize       unicode fold, strip honorifics and particles (Dr., Sheikh, Al-, bin, de la),
                 legal forms for companies (LLC, L.L.C., GmbH), transliteration table
                 (Mohamed -> muhammad, Chang -> zhang, Kovalevskiy -> kovalevsky)
        |
        v
 block           each token -> similar watchlist tokens (same phonetic key, or trigram
                 Jaccard >= 0.4); keep names whose matched tokens carry >= 40% of the
                 query's weight, where common tokens weigh less
        |
        v
 score           name: token-set Jaro-Winkler with phonetic and initial rules, up to 85
                 DOB: +10 exact, +4 same year, -25 mismatch    nationality: +5 / -8
                 person vs company: -40      every point is returned with its reason
        |
        v
 threshold 71.5  -> review queue (SQLite) -> analyst disposition -> hash-chained audit log
                 -> risk scorecard (YAML): customer type, geography, product, screening,
                    ownership red flags -> low / medium / high with reasons
```

For a business, `/screen/business` also walks the ownership graph upward from the company, computes each person's effective share over every simple path, flags cycles, opaque holders, nominee patterns, and threshold splitting, then screens the business name, every UBO, and every director.

| File | What it does |
|---|---|
| `normalize.py` | Unicode folding, honorific, particle, and legal-form stripping, transliteration table and ending rules for Arabic, Cyrillic, Chinese, and Hispanic names. |
| `similarity.py` | Jaro, Jaro-Winkler, Levenshtein with early exit, trigrams, and a simplified Double Metaphone style phonetic key. All implemented here. |
| `blocking.py` | Token index with phonetic and trigram lookup. The similarity relation is symmetric, which delta screening relies on. |
| `scoring.py` | Explainable score with per-feature points and token alignment. Name frequencies from the customer base down-weight common names. |
| `screening.py` | The engine: index, candidate generation, best score per watchlist entry across primary name and aliases. |
| `data.py` | Synthetic watchlist and customers, perturbation functions, watchlist update simulation. |
| `evaluate.py`, `report.py` | Threshold sweep, baselines, recommendation, recall by perturbation type, HTML and SVG report. |
| `kyb.py`, `graph.py` | Company registry, UBO math with path explanations, red flags, ownership graph as SVG. |
| `risk.py`, `config/risk.yaml` | Points-based scorecard with overrides (a possible sanctions match is always high). |
| `review.py` | Case queue with dispositions and an append-only audit log where each row hashes the previous one. |
| `delta.py` | Delta rescreening: reverse blocking from changed entries to the customers that could match them. |
| `api.py`, `static/index.html` | FastAPI service and the web UI. |

### Synthetic data

Watchlist names are random combinations of name parts across five conventions: Arabic transliterated (given, father's name, family name with optional Al-), Cyrillic transliterated (given, patronymic, family name with gendered endings), Chinese pinyin (family name first, one or two syllable given name), Hispanic (two given names, paternal and maternal surnames), and Western European. 30% of the people are PEPs and 27% of entries are companies. Entries carry aliases, dates of birth (15% year only), and nationalities.

True matches are made from an entry by applying 1 to 3 of: transliteration variants (some in the normalizer's table, some deliberately not, like "Mouhamed" or "Chih"), typos (keyboard substitution, deletion, transposition, duplication), dropped or reordered name parts, initials, honorifics, diacritics added or removed, uppercase and spacing changes, compound surname changes (hyphen, "y", dropped maternal name), split or merged Chinese given names, and legal form changes for companies. Dates of birth are kept, dropped, reduced to a year, or off by one year. Recall per perturbation type is in the report; the hardest cases are initials (95.7%) and a year-off DOB (90.9%).

### Delta screening

When the list changes, the service does not rescreen everyone. It runs the blocking rule in reverse: for each new or amended entry it finds customer tokens that are similar to the entry's tokens and keeps customers where those tokens carry enough of the customer's name weight. That is exactly the set the forward screener would produce, so the result equals a full rescreen. `kycscreen rescreen` checks this on every run and exits 1 if they differ. Name frequencies come from the customer base, not the list, so existing scores do not move when the list changes.

### Ultimate beneficial owners

Effective ownership is the sum over paths of the product of percentages along each path. A path stops when it would revisit a company, so a circular holding is counted once and reported as a red flag rather than expanded into an infinite series. Every UBO comes with its arithmetic, for example:

```
Anna Richard Falkenquit owns 70% of Maris Holdings Ltd; Maris Holdings Ltd owns 80% of
Tellar Investments B.V.; Tellar Investments B.V. owns 60% of Kestrel Bay Commodities FZE.
70% x 80% x 60% = 33.60%
```

If no person reaches 25%, the directors are listed as senior managing officials. The demo registry has five hand-built cases (simple, three-layer chain with a listed indirect owner, circular holding, family stakes of 24% each with a nominee holder and a nominee director at a mass registration address, and a foundation that discloses no owners) plus 160 random structures.

## How to run

Needs [uv](https://docs.astral.sh/uv/). No API keys or external services.

```bash
cd kyc-screening
uv sync --python 3.12
uv run kycscreen eval                 # sweep, baselines, writes reports/eval.json and reports/tradeoff.html (about 2 min)
uv run kycscreen eval --gate          # engine only, exit 1 if recall at the configured threshold is below 99%
uv run kycscreen bench                # throughput and blocking recall
uv run kycscreen rescreen             # watchlist update: delta versus full rescreen
uv run kycscreen screen "Sheikh Mohamed Haddad" --nationality JO
uv run kycscreen business B-0002      # UBO paths and red flags for a registry company
uv run kycscreen gen --out data       # write the synthetic data to JSON
uv run kycscreen serve --port 8000    # then open http://127.0.0.1:8000
```

The web UI has three tabs: screen a name and see each candidate's score breakdown and token alignment, pick a business and see its ownership graph with UBO paths highlighted, and the threshold tradeoff chart.

Call the API:

```bash
curl -s -X POST localhost:8000/screen/person -H 'content-type: application/json' \
  -d '{"name":"Dr. Mohamed Haddad","dob":"1950-09-26","nationality":"SA","customer_id":"C-1"}'
curl -s -X POST localhost:8000/screen/business -H 'content-type: application/json' -d '{"registry_id":"B-0005"}'
curl -s localhost:8000/review/queue
curl -s -X POST localhost:8000/review/1/disposition -H 'content-type: application/json' \
  -d '{"disposition":"false_positive","analyst":"ana","note":"DOB and nationality differ"}'
curl -s localhost:8000/audit
curl -s localhost:8000/metrics | grep kyc_
```

The threshold and recall target live in `src/kycscreen/config/screening.yaml`; the risk scorecard is `src/kycscreen/config/risk.yaml`. Set `KYC_REVIEW_DB=review.db` to keep the review queue across restarts (the default is in memory). Logs are one JSON object per line on stderr.

Docker files are built and smoke tested in CI. Docker is not needed to run the app.

```bash
make install test lint gate run
```

## Tests

74 tests, run with `uv run pytest -q` (about 10 seconds on a tenth-scale dataset):

- **Normalization (15):** diacritics and special letters, honorific chains, particles, six Muhammad spellings, Cyrillic ending rules, Wade-Giles surnames, legal forms and dotted acronyms, Spanish compound surnames.
- **Similarity (10):** Jaro and Jaro-Winkler against published values (MARTHA/MARHTA 0.961, DWAYNE/DUANE 0.840, DIXON/DICKSONX 0.813), symmetry and bounds, Levenshtein and its early exit, phonetic keys.
- **Blocking (4):** typo and phonetic lookup, the similarity relation is symmetric on real data, initials and joined tokens, blocking recall at least 97% with fewer than 10% of names compared.
- **Scoring (8):** score rises monotonically with name closeness, DOB and nationality evidence are ordered, record type penalty, contributions add up to the total, common tokens count less, initials and reordering.
- **Screening (4):** perturbed true matches rank first, one candidate per entry, alias hits, score floor.
- **KYB (9):** direct holdings, chains multiply, paths sum, exactly 25% counts, cycles detected and counted once (hand-checked 39% and 49%), self-holding terminates, senior managing official fallback, opaque holder and threshold splitting flags, the generated demo structures.
- **Risk (5), review (3), delta (3):** scorecard arithmetic, overrides, configurable bands; idempotent cases, disposition rules, audit chain tamper detection; delta equals full rescreen, removed entries clear alerts, a new entry is found.
- **Eval (4) and API (9):** the engine beats both baselines, the recommendation rule, recall and workload fall with the threshold, the CLI gate passes and fails; health, metrics, UI, person hit opens a case and rates high, validation, business UBOs and paths, graph SVG, review flow with 404 and 409, audit chain.

## Production notes

- **Real lists:** load OFAC, UN, EU, and UK lists from their published XML with list version tracking, and run the delta rescreen on every publication. The blocking index and customer index would move to a search engine (OpenSearch with custom analyzers) or a dedicated screening service, but the reverse-blocking idea stays the same.
- **Names in original script:** index Arabic, Cyrillic, and Chinese names in their original script plus several romanizations, and use a maintained transliteration library rather than a hand-written table. Add a learned name-similarity model as one more scored feature, not a replacement for the explainable ones.
- **Threshold governance:** the threshold is a documented model risk decision. Re-run the eval on a labeled sample of real dispositions every quarter, keep the report with the model inventory, and require sign-off when it changes. Separate thresholds for sanctions and PEP lists are common.
- **Ownership data:** pull from company registries and a commercial ownership graph, store it in a graph database, and solve circular holdings with the linear system (I - A)^-1 when the rule set calls for full look-through instead of simple paths.
- **Review operations:** suppress repeat alerts on the same customer and entry once cleared (a good-guy list with expiry), add four-eyes approval for true match dispositions, and ship the audit chain to write-once storage.
- **Throughput:** pure Python does about 700 names per second per core. A full rescreen of 10 million customers would shard by customer id across workers, or move scoring to a compiled library; delta screening keeps the daily load small either way.

## License

MIT. See [LICENSE](LICENSE).
