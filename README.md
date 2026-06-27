# Fediverse Digest Generator

This project contains a Python script to automatically fetch your posts from a GoToSocial or Mastodon instance and generate monthly digest files in Markdown. The output is specifically formatted to be compatible with static site generators like [Eleventy](https://www.11ty.dev/), including proper frontmatter for titles, dates, permalinks, and tags.

## Features

- **Fetches Posts**: Connects to a Fediverse instance via the Mastodon API to retrieve your posts.
- **Monthly Digests**: Groups all posts by month into individual Markdown files.
- **Chronological Ordering**: Organizes posts by date and then by time (morning to night) within each day.
- **Time Zone Conversion**: Converts UTC timestamps to a specified time zone (defaults to US/Eastern).
- **Clean Markdown**: Converts post content from HTML to clean, readable Markdown.
- **Eleventy-Ready Frontmatter**: Generates YAML frontmatter compatible with standard blog layouts.
- **Historical Backfill**: Includes an option to fetch your entire post history, not just the current month.
- **Secure**: Manages API credentials securely using a `.env` file, which is ignored by Git.

## Requirements

- Python 3.9+ (for the `zoneinfo` library)
- The libraries listed in `requirements.txt`.

## Setup

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/YOUR_USERNAME/fediverse-digests.git
    cd fediverse-digests
    ```

2.  **Install dependencies:**
    It's recommended to use a virtual environment.
    ```bash
    python -m venv .venv
    source .venv/bin/activate  # On Windows, use `.venv\Scripts\activate`
    pip install -r requirements.txt
    ```

3.  **Configure your environment:**
    Create a `.env` file by copying the example file.
    ```bash
    cp .env.example .env
    ```
    Now, edit the `.env` file to add your instance URL and access token.

## Configuration

The following variables must be set in your `.env` file:

- `GOTOSOCIAL_INSTANCE_URL`: The base URL of your GoToSocial/Mastodon instance (e.g., `https://cuthrell.com`).
- `GOTOSOCIAL_ACCESS_TOKEN`: Your API access token. You can generate one in your instance's settings under `Settings -> Development`.
- `GOTOSOCIAL_ACCOUNT_ID`: (Optional) Your numerical account ID. If you leave this blank, the script will automatically detect it using your access token.

## Usage

The script can be run from the command line.

### Sync Current Month's Posts

To fetch new posts from the beginning of the current month and generate or update the corresponding digest file, run:

```bash
python sync_fediverse.py
```

### Backfill All Historical Posts

To fetch your entire post history and generate digest files for every month you've been active, use the `--backfill` flag.

```bash
python sync_fediverse.py --backfill
```

## Output

The script will generate Markdown files inside the `./src/posts/fediverse/` directory. Each file is named using the last day of the month it represents (e.g., `digest-2026-06-30.md`).

An example of a generated file:

```markdown
---
title: "My Fediverse Digest for June 2026"
description: "A monthly digest of my thoughts, links, and updates from the Fediverse for June 2026."
date: 2026-06-30
permalink: /archive/fediverse/2026-06/
tags: ["fediverse", "digest"]
---

## Jun 04

### 07:36 PM

So, I accidently read news about the news today...

[Source](https://cuthrell.com/@jay/statuses/01KTAFX14HDE1SS6N3VJYGDAH9)

---
```