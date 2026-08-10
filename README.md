# Weekend Getaway (周末去哪玩)

**[English](README.md)** | [中文](README.zh-CN.md)

![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)
![Streamlit](https://img.shields.io/badge/UI-Streamlit-FF4B4B?logo=streamlit&logoColor=white)
![Tests](https://img.shields.io/badge/tests-39%20passing-2ea44f)
![License](https://img.shields.io/badge/license-All%20Rights%20Reserved-lightgrey)

An LLM-powered short-trip planner with live web search grounding. Give it your
departure city, time/budget constraints, and personal weights (scenery vs.
food, how much walking you can tolerate), and it generates a ranked list of
candidate destinations. Pick one and it builds a full itinerary — trains,
hotel, a day-by-day schedule, locally-recommended restaurants, and an honest
verdict on whether the trip is actually worth it. Export to HTML or PDF.

There's no integration with an official train/hotel booking API (neither
12306 nor Ctrip exposes one to individual developers), so all factual
information comes from the model's live web search. Treat it as a starting
point, not ground truth — always double-check train times and prices on
12306/Ctrip before you actually travel.

## Preview

<p align="center">
  <img src="docs/preview.png" alt="Sample generated itinerary card" width="640">
</p>

<p align="center"><em>A generated itinerary card (sample output with placeholder data, for illustration only — not a real recommendation).</em></p>

## Quick start

```bash
cd weekend-getaway
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Configure your API key — see "Configure the API" below

streamlit run app.py
```

Your browser will open at http://localhost:8501.

PDF export uses [WeasyPrint](https://weasyprint.org/), which depends on the
system's pango/cairo libraries:

```bash
brew install pango
```

(`pip install -r requirements.txt` installs the Python packages; `pango` is a
system library that needs a separate Homebrew install. Skipping it doesn't
break anything else — only the "Download PDF" button will fail, and you can
fall back to downloading HTML instead.)

## Run the tests

```bash
pytest
```

39 test cases, under a second, all running fully offline against a fake LLM
client and fake search results — no real API quota is consumed. They cover
real bugs found while actually using the app, not generic smoke tests: JSON
self-repair retries, cache hit/miss behavior and forced refresh, candidate
generation (concurrent verification, duration filtering, backfilling short
counts, deduplicating repeated cities), hotel price validation (including the
"budget number got echoed back as a real price" incident), train number
grounding, cross-city contamination in food/hotel recommendations, search
retry-on-failure, HTML rendering (source attribution, staleness warnings),
and the CJK font bug in PDF export. Run this after any code change instead of
hand-rolling a one-off verification script.

## Configure the API

Two ways to provide credentials, toggled in the sidebar under "Model
Settings":

**Option 1: local `.env` file (recommended — no need to retype it each time)**

Copy `.env.example` to `.env` and fill in your key:

```
LLM_API_KEY=your-api-key-here
LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
LLM_MODEL=glm-4-air
```

`.env` is already in `.gitignore` and will never be committed — don't add it
to version control manually.

**Option 2: type it directly into the Streamlit page**

Uncheck "Use .env config" and enter API Key / Base URL / model name directly
in the sidebar. This value only lives in the current browser session's
memory, disappears on refresh, and is never written to disk.

The default is Zhipu GLM-4 (`https://open.bigmodel.cn/api/paas/v4`), but the
client is a plain OpenAI-protocol wrapper — pointing it at any other
OpenAI-compatible endpoint (Moonshot, DeepSeek, or OpenAI itself) only needs a
different Base URL / Key / model name, no code changes.

## Architecture

```
User preferences (departure city / time window / budget / weights)
   → candidate_gen    — LLM brainstorms candidate cities, verifies travel time for
                         each concurrently, scores on 4 dimensions
                         (backfills if short on count, deduplicates by city,
                         results cached locally)
   → scoring          — linear weighted ranking by user weights (explainable, not a black box)
   → [user picks a candidate and clicks "Generate detailed itinerary";
      progress streams live via st.status]
   → itinerary_builder — hotel / outbound train / return train / "schedule + food"
                         verified concurrently (4-way), each cross-checked against
                         its search results before being trusted (results cached locally)
   → html_renderer / pdf_renderer — rendered into a styled page, previewable in-app,
                                     downloadable as HTML or PDF
```

```
weekend-getaway/
├── app.py                      # Streamlit entry point
├── pytest.ini                  # pytest config
├── .streamlit/config.toml      # theme colors (shares design tokens with the itinerary page)
├── tests/                      # offline tests against fake LLM/search, covering real bugs found
└── weekendgo/
    ├── config.py                # LLM settings + user preference data structures
    ├── llm/client.py            # OpenAI-compatible client; chat_structured() does
    │                             # JSON self-repair retries
    ├── search/duckduckgo.py     # key-free web search, with retry-on-failure backoff
    ├── domain/models.py         # Pydantic models (train/hotel/food/itinerary/candidate)
    ├── cache/store.py           # local file-based result cache with TTL + forced refresh
    ├── pipeline/
    │   ├── candidate_gen.py     # candidate destinations: brainstorm + concurrent
    │   │                         # verification + backfill + dedupe + cache
    │   ├── scoring.py           # weighted ranking
    │   ├── itinerary_builder.py # full itinerary: concurrent fact search + structured
    │   │                         # generation + cache
    │   └── orchestrator.py      # get_candidates() / get_itinerary() two-stage entry point
    └── render/
        ├── html_renderer.py     # renders the styled itinerary page
        └── pdf_renderer.py      # HTML → PDF (weasyprint)
```

## How the personalization weights work

Four sliders, each 0–1, independent of each other:

- **Scenery weight**: higher favors destinations with distinctive
  scenery/culture.
- **Food weight**: higher favors destinations with strong food appeal.
- **Walking/climbing tolerance**: lower means the ranking favors
  lower-intensity destinations (e.g. avoids places needing lots of stair
  climbing).
- **Uniqueness / novelty weight**: higher favors "things you can't get in
  your home city" — limited-time events, unique festivals, rare
  landscapes/architecture. If a place is "just good food" that your home city
  already has an equivalent of, the model is instructed to score this
  dimension low.

The scoring formula lives in `weekendgo/pipeline/scoring.py` — it's an
explainable linear weighted sum, not a black-box model.

## Why the candidate count sometimes "backfills"

A real bug hit during testing: the user asked for 4 candidates and only 3
showed up on screen, silently, with no error. Root cause: three separate
places could quietly shrink the final count below what was requested — the
brainstorm step not returning enough cities, an empty city name getting
skipped, or a verified candidate exceeding the duration limit and getting
filtered out with no replacement.

`weekendgo/pipeline/candidate_gen.py` now runs a backfill loop: after each
round of brainstorm + verify, if the count is still short, it brainstorms
another round (explicitly excluding cities already tried, so no repeats),
up to 2 extra rounds. Under normal conditions you get exactly as many
candidates as you asked for. There's also a final dedup pass — even if a
duplicate slips in through some other path (a real bug also seen in
practice, where the same city was returned twice with slightly different
brainstorm wording), it gets caught before being shown. If constraints are
genuinely too strict (e.g. a very short duration cap with few real options
nearby) and two backfill rounds still can't fill the count, it honestly shows
however many verified candidates it actually has, rather than looping forever
or silently returning a short list that looks like a bug.

## How hotel prices are kept honest

A concrete bug hit during testing: search engine snippets often don't
contain a real hotel price at all, but the model would "make up something
plausible" — sometimes wildly outside the requested budget, sometimes with
zero grounding. In one case the model even echoed the prompt's own budget
example text back as if it were a "found" price. The current flow
(`weekendgo/pipeline/itinerary_builder.py`):

1. **Primary verification** (`_verify_hotel_primary`) and **experience-based
   fallback recommendation** (`_recommend_fallback_hotel`) run concurrently
   from the start, not "verify first, fall back only on failure" — free web
   search has weak recall for real-time hotel prices, so the verification
   path fails often enough that it's faster to always run both and pick
   whichever succeeds (trading one extra API call for a shorter worst-case
   wait, a deliberate tradeoff).
2. If verification succeeds and a code-level check (`_validate_hotel_price`,
   with a 50% tolerance band) confirms the price isn't wildly off from the
   budget, that result is used.
3. If verification fails (the model admits it couldn't find a price, or the
   validator catches an implausible number), the fallback recommendation is
   used instead — with its price field hard-overwritten to a fixed
   "price not found" string in code, regardless of what the model actually
   output. There is no code path left where a fabricated number can be shown
   as fact, not even a hedged "approximately ¥X" guess.
4. Unverified prices render in orange (not gold) with an "unverified" badge,
   so it's visually obvious at a glance which numbers can't be fully trusted.

## How train info is kept honest

Same philosophy as hotels, different validation mechanism — a train number
is a concrete string (like `G921`), so it can be checked directly against
whether it actually appears in the search results:

1. Outbound and return trains are each verified in their own isolated call
   (`_verify_train`), not bundled into the big itinerary-generation call,
   with the prompt treating "no explicit train number means leave it blank,
   never guess" as the highest-priority rule.
2. A code-level backstop (`_validate_train_grounding`) double-checks: if the
   model's train number doesn't literally appear in the search result text,
   it's almost certainly fabricated — it gets cleared, keeping only the
   rough duration if one was found, with a note explaining the number
   couldn't be verified and to check 12306 directly.
3. Hotel, outbound train, return train, and the "schedule + food" call all
   run concurrently (`_build_itinerary_uncached`), so total latency is close
   to the slowest single step, not the sum of all four.

## How cross-city contamination is prevented

A real bug: a Changsha itinerary once recommended a restaurant that was
actually in Tianjin — the free search API's recall for Chinese travel
queries is mediocre, and occasionally returns results with nothing to do
with the actual destination. The fix, applied to both restaurant and hotel
search:

- Before any search result is handed to the model, it's filtered to keep
  only results whose title/snippet actually mention the destination city
  name (`_filter_relevant_to_destination`). Anything that doesn't is
  dropped rather than risk being mistaken for local information.
- If filtering a restaurant search leaves nothing relevant, the code retries
  once with a differently-worded query before giving up — reducing empty
  recommendations without reintroducing the contamination risk.
- If everything is filtered out even after the retry, the model is
  explicitly told the search turned up nothing relevant and to say so
  honestly, rather than being left to improvise from irrelevant results.

## How caching and forced refresh work

Results are cached locally (`.cache/`, 24h TTL, keyed by a hash of the
request parameters) so repeating the same planning request doesn't burn
another round of API calls and searches. The sidebar and each candidate card
show a hint when a result would come from cache and how old it is, plus a
"bypass cache" checkbox — useful when you suspect the cached information
(e.g. a hotel that's since closed) is stale and want to force a fresh lookup.

## Search resilience

The free DuckDuckGo search backend (`ddgs`) is prone to transient rate
limiting, and this app fires off a lot of concurrent searches in a short
window (candidate verification, two hotel search variants, outbound/return
trains, restaurants). `search_web` now retries with a short backoff on
failure before giving up, so a transient rate limit doesn't get
misinterpreted downstream as "this information genuinely doesn't exist."

## What using it feels like

1. Fill in preferences on the left, click "Start planning" — one model call
   brainstorms candidate cities, then each candidate's travel time is
   verified concurrently; rankings typically appear within seconds to a
   couple minutes (an identical repeat request hits cache and is instant).
   Progress streams live via `st.status` ("Verified «X»" as each one
   finishes), not a generic spinner.
2. Candidates render as cards: city, four dimension scores, overall score.
   Click "Generate detailed itinerary" on any of them — you can open several
   candidates side by side, not just the top-ranked one. This step also
   streams live progress (hotel / outbound train / return train / schedule
   & food, whichever finishes first shows first).
3. The detailed itinerary renders in the same styled card layout (where to
   stay / walking intensity / where to eat / day-by-day schedule / whether
   it's actually worth it), viewable in-page, downloadable as HTML or PDF.
4. If any step fails (network issue, unexpected model response), the page
   shows one plain-language sentence ("this failed, clicking again usually
   fixes it") with the raw error tucked into a collapsed expander — never a
   raw stack trace dumped on screen, and never a crashed app.

## Known limitations (stated honestly, not glossed over)

- No integration with an official train/hotel API — all train and price
  information comes from the model's live search results, which can be
  inaccurate or stale. **Always verify before you actually travel.**
- **Model choice materially affects reliability**, learned the hard way:
  `glm-4-flash` is fast but would sometimes "look like" it searched without
  actually grounding its answer in the results, producing fabricated travel
  times. Switching the default to `glm-4-air` mostly resolved this, at an
  acceptable speed cost, hence the default. An earlier version let the model
  decide for itself whether to search again (an open tool-calling loop);
  stronger models would sometimes loop indefinitely without converging —
  that mechanism was removed entirely in favor of code deciding what to
  search, with the model only responsible for turning results into
  structured output.
- Search runs on the key-free DuckDuckGo backend (`ddgs`), whose recall for
  Chinese travel queries is noticeably weaker than a dedicated maps/POI API
  (e.g. Amap), especially for precise hotel/restaurant addresses — this is
  why hotel prices and train numbers are validated in code rather than
  trusted outright, and why restaurant/hotel search results are filtered for
  destination relevance before being used.
- Cache is a local file store with a 24h TTL and no cleanup UI beyond the
  per-request "bypass cache" checkbox; to wipe everything at once, call
  `weekendgo.cache.store.clear_all()` or delete the `.cache/` directory.
- Progress indicators show "whichever subtask finished first" rather than a
  true percentage; generating one detailed itinerary typically takes tens of
  seconds, depending on model latency and network conditions.

## Security notes

- Never commit `.env` to git, and never hardcode an API key into any file
  that could end up committed. `.env` is already listed in `.gitignore`.
- The manually-entered API key field (Option 2 above) lives only in the
  browser session's memory for that run — it's never written to disk or
  logged.
- This project is intended to run locally. It has no authentication, no rate
  limiting, and no input sanitization hardened for multi-tenant or public
  deployment — don't expose it directly on the public internet without
  adding those yourself.

## License

All rights reserved — see [LICENSE](LICENSE). This repository is public for
portfolio/demonstration purposes only; it is not open source, and no
permission is granted to reuse, copy, or redistribute the code.
