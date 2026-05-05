# Web Crawler

A high-throughput async web crawler with LLM-based content classification and structured data extraction.

## Features

- **Async-first** — AsyncIO event loop with a configurable worker pool (default 50 workers) and aiohttp connection pooling
- **Adaptive rate limiting** — per-domain token-bucket that slows on 429/503, speeds up on consecutive successes
- **LLM extraction** — LangChain + OpenAI classify every page (article, product, docs, …) and extract structured fields; falls back gracefully when no API key is set
- **Redis backend** — Redis Streams task queue (consumer groups, exactly-once delivery), Redis SET URL deduplication, Redis Hash checkpointing for crash recovery
- **Robots.txt compliance** — per-domain cache with 1-hour TTL
- **Resilient retries** — exponential back-off with full jitter, respects `Retry-After` headers
- **Idle auto-stop** — crawl ends automatically when the queue drains
- **Stale-task reclamation** — crashed workers' in-flight tasks are automatically reclaimed and re-queued

---

## Architecture

```
main.py (Typer CLI)
└── CrawlEngine
    ├── TaskQueue        ← Redis Streams (XADD / XREADGROUP / XACK)
    ├── URLDeduplicator  ← Redis SET, SHA-1 fingerprints
    ├── CheckpointManager← Redis Hash
    ├── AdaptiveRateLimiter (per-domain token bucket, in-process)
    ├── RobotsCache      ← robots.txt TTL cache
    ├── AsyncHTTPClient  ← aiohttp + RetryPolicy
    ├── HTMLParser       ← BeautifulSoup in ThreadPoolExecutor
    └── LLMExtractor
        ├── ClassifyChain  ← ChatPromptTemplate | ChatOpenAI | JsonOutputParser
        └── ExtractChain   ← same pattern, deeper structured output
```

Output is written as JSONL files partitioned by domain and date:

```
output/
└── example.com/
    └── 2025-05-05.jsonl
```

Each line is a JSON object with: `url`, `title`, `summary`, `classification`, `key_entities`, `structured_fields`, `outbound_links`, and a `raw_text_hash`.

---

## Prerequisites

| Requirement | Version |
|---|---|
| Python | 3.11 or later |
| Redis | 7.x |
| OpenAI API key | Optional — LLM extraction is skipped if absent |

---

## Local Setup — macOS

### 1. Install system dependencies

```bash
# Install Homebrew if not already installed
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install Python 3.11+ and Redis
brew install python@3.11 redis

# Start Redis as a background service
brew services start redis

# Verify Redis is running
redis-cli ping   # should print: PONG
```

### 2. Clone the repo and create a virtual environment

```bash
git clone https://github.com/nikhilpyreddy/web-crawler.git
cd web-crawler

python3.11 -m venv .venv
source .venv/bin/activate
```

### 3. Install Python dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in at minimum:

```dotenv
# Required for LLM extraction (optional — crawler works without it)
CRAWLER_OPENAI_API_KEY=sk-...

# Redis defaults to localhost:6379 — change only if needed
CRAWLER_REDIS_URL=redis://localhost:6379/0
```

### 5. Run your first crawl

```bash
# Basic crawl of a single site
python main.py crawl https://example.com

# Limit depth and concurrency
python main.py crawl --depth 2 --concurrency 20 https://news.ycombinator.com

# Crawl multiple seeds, write output to ./data
python main.py crawl --output ./data https://example.com https://example.org

# Resume an interrupted crawl
python main.py crawl --resume https://example.com

# Clear all Redis state and start fresh
python main.py reset
```

### Stop Redis when done

```bash
brew services stop redis
```

---

## Local Setup — Windows

### Option A — Docker Desktop (recommended, least friction)

If you have [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed, spin up Redis with one command:

```powershell
docker compose up -d redis
```

Then skip to [Install Python dependencies](#install-python-dependencies-windows).

### Option B — Native Redis via Chocolatey

```powershell
# Install Chocolatey if not already installed (run in an elevated PowerShell)
Set-ExecutionPolicy Bypass -Scope Process -Force
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072
iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))

# Install Redis
choco install redis-64

# Start Redis (runs in the current terminal; open a new one for next steps)
redis-server
```

### Option C — Native Redis via winget

```powershell
winget install Redis.Redis
redis-server
```

### Install Python dependencies (Windows)

```powershell
# Install Python 3.11 via winget if needed
winget install Python.Python.3.11

# Clone and enter the repo
git clone https://github.com/nikhilpyreddy/web-crawler.git
cd web-crawler

# Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1      # PowerShell
# — or —
.venv\Scripts\activate.bat      # Command Prompt

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

### Configure and run (Windows)

```powershell
# Copy the example env file
Copy-Item .env.example .env
notepad .env   # add your CRAWLER_OPENAI_API_KEY

# Run a crawl
python main.py crawl https://example.com

# Resume after Ctrl-C
python main.py crawl --resume https://example.com

# Reset Redis state
python main.py reset
```

> **Windows note:** `SIGTERM` is not supported on Windows. Use `Ctrl+C` to stop the crawler; it will flush a final checkpoint before exiting.

---

## Alternative: Docker Compose for Redis only

If you prefer not to install Redis natively, use the included `docker-compose.yml` regardless of OS:

```bash
# Start Redis in the background (persists data in a named volume)
docker compose up -d redis

# Verify
docker compose exec redis redis-cli ping   # PONG

# Stop and remove the container (data is preserved in the volume)
docker compose down
```

---

## CLI Reference

```
Usage: python main.py [COMMAND] [OPTIONS] [ARGS]

Commands:
  crawl   Crawl seed URLs and extract structured data
  reset   Clear all Redis state for a fresh start

Crawl options:
  URLS                   One or more seed URLs (required)
  -d, --depth INT        Max link depth to follow        [default: 3]
  -c, --concurrency INT  Number of async workers         [default: 50]
  -n, --max-pages INT    Stop after N pages crawled      [default: 100000]
  -o, --output PATH      Output directory                [default: ./output]
  -r, --resume           Resume from last checkpoint
  -l, --log-level TEXT   DEBUG / INFO / WARNING / ERROR  [default: INFO]
      --redis TEXT        Redis URL override
```

---

## Configuration Reference

All settings can be set in `.env` (prefixed `CRAWLER_`) or as environment variables:

| Variable | Default | Description |
|---|---|---|
| `CRAWLER_OPENAI_API_KEY` | — | OpenAI API key (optional) |
| `CRAWLER_OPENAI_MODEL` | `gpt-4o-mini` | Model used for classification and extraction |
| `CRAWLER_REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `CRAWLER_MAX_DEPTH` | `3` | Maximum link depth |
| `CRAWLER_MAX_PAGES` | `100000` | Hard stop after N pages |
| `CRAWLER_CONCURRENCY` | `50` | Number of async worker coroutines |
| `CRAWLER_THREAD_POOL_SIZE` | `8` | Threads for CPU-bound HTML parsing |
| `CRAWLER_REQUEST_TIMEOUT_SECONDS` | `30.0` | Per-request HTTP timeout |
| `CRAWLER_MAX_RETRIES` | `3` | Retry attempts before giving up |
| `CRAWLER_DEFAULT_RATE_LIMIT_RPS` | `2.0` | Starting requests/sec per domain |
| `CRAWLER_MIN_RATE_LIMIT_RPS` | `0.5` | Floor for adaptive rate |
| `CRAWLER_MAX_RATE_LIMIT_RPS` | `10.0` | Ceiling for adaptive rate |
| `CRAWLER_OUTPUT_DIR` | `./output` | JSONL output directory |
| `CRAWLER_LOG_LEVEL` | `INFO` | Log verbosity |

---

## Output Format

Each page is written as a single JSON line (JSONL):

```json
{
  "task_id": "3f2a1c...",
  "url": "https://example.com/article/foo",
  "crawled_at": "2025-05-05T14:32:01Z",
  "classification": {
    "category": "article",
    "confidence": 0.95,
    "language": "en",
    "should_extract": true
  },
  "title": "Foo Article Title",
  "summary": "This article discusses foo in three concise sentences...",
  "key_entities": ["Foo Corp", "Jane Smith"],
  "structured_fields": {
    "author": "Jane Smith",
    "published_date": "2025-05-01",
    "tags": ["foo", "bar"]
  },
  "outbound_links": ["https://example.com/related"],
  "raw_text_hash": "a3f1..."
}
```

---

## Project Layout

```
web-crawler/
├── main.py                    # CLI entry point (Typer)
├── requirements.txt
├── docker-compose.yml         # Redis service
├── .env.example               # Config template
└── crawler/
    ├── config.py              # Pydantic Settings
    ├── models/
    │   ├── task.py            # CrawlTask, TaskStatus
    │   ├── page.py            # FetchResult, ParsedPage
    │   └── extracted.py       # ExtractedData, PageClassification
    ├── redis_backend/
    │   ├── task_queue.py      # Redis Streams queue
    │   ├── deduplicator.py    # Redis SET dedup
    │   └── checkpoint.py      # Redis Hash checkpoint
    ├── fetcher/
    │   ├── http_client.py     # aiohttp session
    │   ├── retry.py           # Exponential back-off
    │   └── robots.py          # robots.txt cache
    ├── parser/
    │   ├── html_parser.py     # BeautifulSoup + link extraction
    │   └── cleaner.py         # Boilerplate stripping for LLM
    ├── extraction/
    │   ├── prompts.py         # ChatPromptTemplate definitions
    │   ├── chains.py          # ClassifyChain + ExtractChain
    │   └── extractor.py       # Semaphore-gated async orchestrator
    └── core/
        ├── rate_limiter.py    # Adaptive per-domain token bucket
        ├── scheduler.py       # URL frontier + retry logic
        ├── worker.py          # Per-worker fetch→parse→extract→write loop
        └── engine.py          # Top-level orchestrator
```
