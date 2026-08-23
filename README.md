# API Reliability Kit

Credential-free proof for the failure paths that make small API automations unreliable: pagination, transient retries, malformed responses, duplicates, conflicting identifiers, normalization, and resumable state.

The engine receives a transport callable, so every included test runs against deterministic scripted responses. It makes no network requests and contains no credentials.

## Install

```bash
python -m pip install api-reliability-kit
api-reliability-kit
```

The command prints a deterministic JSON run showing a synthetic HTTP 429 retry, two cursor pages, exact-duplicate suppression, normalized records, and resumable state.

## Run

```powershell
python -m unittest discover -s tests -v
python -m api_reliability.cli
```

The CLI executes two synthetic cursor pages, retries a synthetic rate limit, suppresses one exact duplicate, and prints a JSON snapshot.

## Verified boundaries

This proves only the included provider-neutral engine and scripted scenarios. It is not a live API connector, scheduler, database, proxy, scraper, OAuth client, hosted service, security audit, compliance assessment, throughput benchmark, or production deployment. A real adapter must follow the selected provider's current official documentation, terms, authentication flow, limits, and data rules.

## Hosted webhook decisions

For language-neutral idempotency keys, bounded retry decisions, and webhook contract checks over HTTPS, use the [Blaz Webhook Reliability API on RapidAPI](https://rapidapi.com/jblaz6335/api/blaz-webhook-reliability-api). A free plan is available for evaluation.

For a downloadable walkthrough, use the [free browser demo](https://blazalgosystems.itch.io/python-api-reliability-demo). For a scoped reliability diagnosis or implementation, use the [paid support listing](https://ko-fi.com/s/fd75e9be8a).
