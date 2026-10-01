# Codex models and effort: evidence reference

Researched 2026-09-28; prices, allowances and availability age. Monitor approved the guidance on 2026-09-28. The five working bullets live in [GUIDE.md](../GUIDE.md#codex). Recommendations remain trial settings, not measured colony results. No paid comparison runs or images were generated.

## What is available, and what effort means

OpenAI currently recommends **GPT-6 Astra** for its most demanding work, **GPT-6 Sol** for coding and other substantial work, and **GPT-6 Luna** for focused volume. Its suggested starting efforts are Astra low (called Light in some clients), Sol medium and Luna high. The CLI selects models with `-m` or `/model`; `-c model_reasoning_effort=high` sets effort. Higher effort spends more time and tokens. Max increases depth; Ultra uses subagents. Availability varies by account and client. [Official Codex models](https://developers.openai.com/codex/models)

The API model pages list Astra efforts `low`, `medium`, `high`, `xhigh`, `max`; Sol and Luna additionally allow `none`. Ultra is a Codex workflow option, not an Astra API `reasoning.effort` value. API support does not mean every CLI picker exposes the same values. [Astra](https://developers.openai.com/api/docs/models/gpt-6-astra), [Sol](https://developers.openai.com/api/docs/models/gpt-6-sol), [Luna](https://developers.openai.com/api/docs/models/gpt-6-luna)

Local evidence: `codex --version` is **0.154.0**. This machine's `~/.codex/models_cache.json`, fetched at **2026-09-28T12:17:31Z**, advertises these visible models:

| Model ID | Catalog default effort | Advertised efforts |
| --- | --- | --- |
| `gpt-6-astra` | medium | low, medium, high, xhigh, max, ultra |
| `gpt-5.6-sol` | low | low, medium, high, xhigh, max, ultra |
| `gpt-5.6-terra` | medium | low, medium, high, xhigh, max, ultra |
| `gpt-5.6-luna` | medium | low, medium, high, xhigh, max |
| `gpt-5.5` | medium | low, medium, high, xhigh |

GPT-6 Sol and Luna are absent from that local catalog. This is an observed availability difference, not evidence that the public docs are wrong or that an API key lacks them. Catalog defaults also differ from OpenAI's workload recommendations; neither establishes the person's configured choice. Do not silently migrate a project. GPT-5.5 retires from Codex with ChatGPT sign-in on October 14, 2026; its API availability is unaffected. [Official retirement notice](https://developers.openai.com/codex/models#gpt-55-retirement)

## Money, allowance, and speed

Standard API dollars per million tokens, short context:

| Model | Input | Cached input | Cache write | Output |
| --- | ---: | ---: | ---: | ---: |
| GPT-6 Astra | $10 | $1 | $12.50 | $50 |
| GPT-6 Sol | $2 | $0.20 | $2.50 | $10 |
| GPT-6 Luna | $0.10 | $0.01 | $0.125 | $0.50 |
| GPT-5.6 Sol | $4 | $0.40 | $5 | $20 |
| GPT-5.6 Terra | $2 | $0.20 | $2.50 | $12 |
| GPT-5.6 Luna | $0.20 | $0.02 | $0.25 | $1.20 |

GPT-6 requests above 272K input tokens reprice the entire request: input/cache rates double, output rises 50%. Batch/Flex rates are half Standard; Fast doubles these GPT-6 rates. Those are billing options, not reasoning levels. GPT-5.6 Sol's listed promotion lasts at least through November 21. [Official API pricing](https://developers.openai.com/api/docs/pricing)

For illustration, 100K uncached input and 10K billable output tokens cost Astra $1.50, 6 Sol $0.30, 6 Luna $0.015, or 5.6 Sol $0.60 at those rates, excluding tool fees and other adjustments. This is arithmetic at equal usage, not a task benchmark: actual reasoning, retries and cache reuse change the bill.

ChatGPT subscriptions and API billing are separate. The published monthly starting prices are Plus $20 and Pro $100 (5x or 20x usage tiers). Business is $20/seat annually or $25 monthly, with two seats minimum. The official approximate **local messages per five hours** are:

| Model | Plus | Pro 5x | Pro 20x |
| --- | ---: | ---: | ---: |
| Astra | 5–45 | 25–225 | 100–900 |
| 6 Sol | 15–150 | 70–700 | 300–3,000 |
| 6 Luna | 350–3,000 | 1,750–14,000 | 7,000–56,000 |
| 5.6 Sol | 10–100 | 50–500 | 200–2,000 |

These are estimates, not guaranteed counts. Work and Codex share allowance; weekly limits can apply. Inspect `/status` or the usage dashboard for this account. Extra credits can extend eligible subscriptions; an API key bills separately. [Official Codex pricing and limits](https://learn.chatgpt.com/docs/pricing)

## External evidence: useful claims, not colony results

**Artificial Analysis, supplied by the person.** Its Astra release page reports this Intelligence Index comparison:

| Astra effort | Index | Dollars per index task |
| --- | ---: | ---: |
| low | 46 | $0.82 |
| medium | 50 | $1.54 |
| high | 51 | $1.73 |
| xhigh | 52 | $2.31 |
| max | 53 | $3.26 |

Calculated from those figures, medium costs about 1.88x low; max about 3.98x low. The index is not a ratio scale of intelligence. Higher scores and throughput do not tell us end-to-end build time or accepted changes per dollar. This supports trying low/medium before max, but is not a measured colony recommendation. We have not reproduced these runs. [Artificial Analysis Astra release](https://artificialanalysis.ai/models/releases/gpt-6-astra)

**AlphaCorp comparison, supplied by the person.** The September 22 article argues for Opus 5.5 on coding value and Astra on frontier reasoning. It cites vendor-run Terminal-Bench figures (66.4% versus 57.9%) while acknowledging different setups; these do not establish a controlled coding winner. Its $10/$50 Astra rate matches the official page. Its blanket statement that Astra is 2.5x the price in every row conflicts with its own cached-read numbers ($1 versus $0.20, which is 5x). Its claim that Astra's cutoff is undisclosed also differs from today's official model page. Treat its conclusion as an untested hypothesis, not grounds to replace GUIDE.md. [AlphaCorp article](https://alphacorp.ai/blog/claude-opus-5-5-vs-gpt-6-astra-benchmarks-pricing-and-which-is-better)

**CodeRabbit, practitioner evaluation.** Its September 4 first-party report measures actionable bug coverage at 61.3% for Astra, 59.0% for 5.6 Sol and 50.2% for Opus 5. The cross-file subset is 57.1%, 47.6%, 42.9%. The often-repeated “20% improvement” over Sol is relative; the displayed cross-file difference is 9.5 percentage points. This is early evidence for trying Astra on distributed defects, not a comparison with Opus 5.5 or 6 Sol, and not evidence of optimal reasoning effort. We have not reproduced the dataset or harness. [CodeRabbit's evaluation](https://www.coderabbit.ai/blog/gpt-6-astra-code-review-evaluation)

**Practitioner reports are mixed.** In one discussion, a business user reports better instruction-following with Astra low but worries about credits; other users describe Astra planning with cheaper workers and warn about repeated checks in long contexts. In another, the author reports a 20-minute Sol Max audit of about 100 lines and heavy Astra allowance consumption. Replies recommend Sol medium/high, while others report poor Sol results. These are self-reported experiences without comparable prompts, traces or controlled budgets. They suggest watching latency, repeated work and quota; they do not establish universal rankings or unlimited plan capacity. [Astra low versus 5.6 Sol discussion](https://www.reddit.com/r/codex/comments/1wmh2h1/gpt_6_astra_low_vs_gpt_56_sol_high/), [Sol Max versus Astra Light discussion](https://www.reddit.com/r/codex/comments/1wowjk9/gpt6_sol_max_or_astra_light/)

## Painter seat

Codex's built-in image generation uses **gpt-image-2**, and OpenAI estimates image turns use included allowance 3–5x faster on average, varying with image size and quality. CLI users can attach references with `-i` and explicitly invoke `$imagegen`. API generation is separately billed. The directing text model and the image generator are separate choices. [Official image-generation documentation](https://learn.chatgpt.com/docs/image-generation)

My proposed painter default is the same Sol medium controller as building, with explicit visual references and a small number of requested variants. Astra medium is an escalation for difficult interpretation or critique, not a claimed way to obtain sharper pixels. Compare finished images against the person's examples; no fetched source measures this colony's taste.

## Colony suggestions and inheritance

For a new ordinary Codex project, the form recommends **GPT-5.6 Sol at medium**, reserving Astra for hard decisions and failures. This is explanatory text, not a preselected model or a persisted default. Blank settings continue through colony's existing inheritance to the person's Codex configuration. `own_defaults()` reports configured values, never this recommendation.

The provider suggests Astra, 5.6 Sol, 5.6 Terra and 5.6 Luna. GPT-6 Sol and Luna appear only when the local Codex model catalog advertises them as visible. The catalog and configuration are read from `CODEX_HOME`, falling back to `~/.codex`. A missing or unreadable catalog leaves the four baseline suggestions available; arbitrary IDs remain typeable.

Effort suggestions run from low through max, plus Ultra labeled as delegating. Luna excludes Ultra; GPT-5.5 excludes both max and Ultra, matching the observed catalog. Model and provider changes update the suggestions without replacing what the person typed. No existing project is migrated.

Suggested future trial, once available: GPT-6 Sol medium for ordinary building and GPT-6 Luna high for focused work. Availability and the lower token rate alone do not prove a better result on colony's projects.


## Catalog freshness and concurrent clients (2026-10-01)

An older running Codex client can overwrite a shared `models_cache.json` with a
smaller catalog. Owned remote homes now have independent writable cache files;
credentials remain shared. Discovery considers same-account catalogs, preferring
the newer client and then its freshest snapshot, and never unions removed models.
An older writer cannot replace a previously discovered newer catalog. A genuine
identity change permits replacement and excludes an old-identity host cache.

Settings shows Codex's catalog client version, fetch time and identity, flagging a
client older than the installed program. Claude's section shows fetchedAt/staleAt,
flags expiry and a fetch predating the installed executable update. These are
native catalog timestamps, separate from Artificial Analysis benchmark freshness.
Session-start hooks re-read only the starting provider's on-disk catalog, without
model calls; compaction hooks do not repeat discovery.

Remote integration is checked with both Codex 0.159.3 and 0.160.0. Native fixtures
cover same-conversation transfer, hooks, compaction restoration, cold resume,
active app turns and shared credential refresh. Unsupported releases retain a
visible local-mode reason rather than claiming app connectivity.
