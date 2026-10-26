#!/usr/bin/env python3
"""
sync_fediverse.py - Extract GoToSocial posts natively from SQLite
(with fallback to REST API) and generate 11ty Markdown monthly digests.
Supports raw Markdown text, hotlinked images, and native HTML5 video tags.
"""

import os
import sys
import sqlite3
import argparse
import calendar
import datetime
import requests
import html2text
from zoneinfo import ZoneInfo
from pathlib import Path
from dotenv import load_dotenv

# Load local .env file variables
load_dotenv()

# Global configuration with environment fallbacks
CONFIG = {
    "db_path": os.getenv("GTS_DB_PATH", "gts.db"),
    "instance_url": os.getenv("GOTOSOCIAL_INSTANCE_URL", "https://cuthrell.com").rstrip("/"),
    "access_token": os.getenv("GOTOSOCIAL_ACCESS_TOKEN"),
    "account_id": os.getenv("GOTOSOCIAL_ACCOUNT_ID"),
    "username": os.getenv("GTS_ACCOUNT_USERNAME", "jay"),
    "output_dir": os.getenv("DIGEST_OUTPUT_DIR", "./src/posts/fediverse"),
    "timezone": os.getenv("BLOG_TIMEZONE", "US/Eastern")
}

TARGET_TZ = ZoneInfo(CONFIG["timezone"])


def format_media_html(media_list: list) -> str:
    """
    Render media attachments into HTML.
    - Videos render as <video> players.
    - Images render as <img> with eleventy:ignore so Sharp/11ty-img won't fail during build.
    """
    if not media_list:
        return ""

    rendered_items = []
    for item in media_list:
        url = item.get("url", "")
        if not url:
            continue

        raw_desc = item.get("alt") or "Attached media"
        alt = raw_desc.replace("\n", " ").replace('"', '&quot;').strip()
        media_type = (item.get("type") or "").lower()

        # Detect video by mime/type or file extension
        is_video = media_type in ["video", "gifv"] or any(
            url.lower().endswith(ext) for ext in [".mp4", ".m4v", ".webm", ".mov"]
        )

        if is_video:
            rendered_items.append(
                f'<video controls preload="metadata" style="max-width: 100%; height: auto;" class="feed-media">\n'
                f'  <source src="{url}">\n'
                f'  Your browser does not support the video tag.\n'
                f'</video>'
            )
        else:
            # eleventy:ignore tells @11ty/eleventy/html-transformer to bypass Sharp optimization
            rendered_items.append(
                f'<img src="{url}" alt="{alt}" loading="lazy" decoding="async" eleventy:ignore style="max-width: 100%; height: auto;" class="feed-image" />'
            )

    return "\n\n" + "\n\n".join(rendered_items)


def extract_from_sqlite(db_path: str, username: str, all_history: bool = False):
    """
    Extract statuses and media natively from GoToSocial's SQLite database.
    Preserves authoring Markdown from the `text` column and constructs hotlinks
    for items in `media_attachments`.
    """
    if not os.path.exists(db_path):
        return None

    print(f"Reading natively from SQLite database: {db_path}")
    conn = sqlite3.connect(f"file:{os.path.abspath(db_path)}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Find the local account ID
    cursor.execute("SELECT id FROM accounts WHERE username = ? AND (domain IS NULL OR domain = '')", (username,))
    account = cursor.fetchone()
    if not account:
        conn.close()
        raise ValueError(f"Account '{username}' not found in {db_path}")

    account_id = account["id"]
    instance_base = CONFIG["instance_url"]

    # Match original public/unlisted statuses (GoToSocial stores visibility as integer enum: 0, 1, 2)
    query = """
        SELECT id, created_at, text, content, url
        FROM statuses
        WHERE account_id = ?
          AND (boost_of_id IS NULL OR boost_of_id = '')
          AND (in_reply_to_id IS NULL OR in_reply_to_id = '')
          AND visibility IN (0, 1, 2, 'public', 'unlisted')
    """
    params = [account_id]

    now_local = datetime.datetime.now(TARGET_TZ)
    if not all_history:
        start_month_utc = datetime.datetime(now_local.year, now_local.month, 1, tzinfo=TARGET_TZ).astimezone(datetime.timezone.utc)
        query += " AND created_at >= ?"
        params.append(start_month_utc.strftime("%Y-%m-%d %H:%M:%S"))

    query += " ORDER BY created_at ASC"
    cursor.execute(query, params)
    status_rows = cursor.fetchall()

    if not status_rows:
        conn.close()
        return []

    status_ids = [row["id"] for row in status_rows]

    # Inspect media_attachments schema dynamically
    cursor.execute("PRAGMA table_info(media_attachments);")
    media_columns = {col["name"] for col in cursor.fetchall()}

    attachments_by_status = {}
    if "status_id" in media_columns:
        select_cols = ["id", "status_id"]
        for opt in ["description", "remote_url", "file_path", "type"]:
            if opt in media_columns:
                select_cols.append(opt)

        # Batch query in chunks to stay well under SQLite parameter limits
        chunk_size = 500
        for i in range(0, len(status_ids), chunk_size):
            chunk = status_ids[i:i + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            media_sql = f"SELECT {', '.join(select_cols)} FROM media_attachments WHERE status_id IN ({placeholders})"
            cursor.execute(media_sql, chunk)
            media_rows = cursor.fetchall()

            for m in media_rows:
                sid = m["status_id"]
                if sid not in attachments_by_status:
                    attachments_by_status[sid] = []

                remote_url = m["remote_url"] if "remote_url" in media_columns else None
                file_path = m["file_path"] if "file_path" in media_columns else None
                m_type = m["type"] if "type" in media_columns else ""

                if remote_url and remote_url.strip():
                    media_url = remote_url.strip()
                elif file_path and file_path.strip():
                    clean_path = file_path.lstrip("/")
                    media_url = f"{instance_base}/fileserver/{clean_path}"
                else:
                    media_url = f"{instance_base}/fileserver/{account_id}/attachment/original/{m['id']}"

                raw_desc = (m["description"] if "description" in media_columns else None) or "Attached media"
                attachments_by_status[sid].append({
                    "url": media_url,
                    "alt": raw_desc,
                    "type": m_type
                })

    h = html2text.HTML2Text()
    h.ignore_links = False
    h.body_width = 0

    posts = []
    for r in status_rows:
        raw_text = r["text"]
        content = raw_text.strip() if raw_text and raw_text.strip() else h.handle(r["content"] or "").strip()

        # Append hotlinked media tags
        media_list = attachments_by_status.get(r["id"], [])
        content += format_media_html(media_list)

        posts.append({
            "id": r["id"],
            "created_at": r["created_at"],
            "content": content,
            "url": r["url"] or f"{instance_base}/@{username}/statuses/{r['id']}"
        })

    conn.close()
    return posts


def fetch_from_api(all_history: bool = False):
    """Fallback fetcher using the GoToSocial / Mastodon REST API."""
    if not CONFIG["access_token"]:
        print("CRITICAL ERROR: No local database found and GOTOSOCIAL_ACCESS_TOKEN is missing.")
        sys.exit(1)

    headers = {"Authorization": f"Bearer {CONFIG['access_token']}"}

    if not CONFIG["account_id"]:
        print("Auto-detecting account ID via verify_credentials...")
        resp = requests.get(f"{CONFIG['instance_url']}/api/v1/accounts/verify_credentials", headers=headers)
        resp.raise_for_status()
        CONFIG["account_id"] = resp.json()["id"]

    url = f"{CONFIG['instance_url']}/api/v1/accounts/{CONFIG['account_id']}/statuses"
    params = {"limit": 40, "exclude_replies": True, "exclude_reblogs": True}

    now_local = datetime.datetime.now(TARGET_TZ)
    start_of_current_month_local = datetime.datetime(now_local.year, now_local.month, 1, tzinfo=TARGET_TZ)

    h = html2text.HTML2Text()
    h.ignore_links = False
    h.body_width = 0

    all_statuses = []
    while url:
        resp = requests.get(url, headers=headers, params=params)
        if resp.status_code != 200:
            print(f"API Error {resp.status_code}: {resp.text}")
            break

        statuses = resp.json()
        if not statuses:
            break

        for s in statuses:
            utc_dt = datetime.datetime.fromisoformat(s["created_at"].replace("Z", "+00:00"))
            local_dt = utc_dt.astimezone(TARGET_TZ)

            if all_history or local_dt >= start_of_current_month_local:
                media_list = [
                    {
                        "url": m.get("url"),
                        "alt": m.get("description") or "Attached media",
                        "type": m.get("type", "")
                    }
                    for m in s.get("media_attachments", [])
                ]

                content = h.handle(s.get("content") or "").strip()
                content += format_media_html(media_list)

                all_statuses.append({
                    "id": s["id"],
                    "created_at": s["created_at"],
                    "content": content,
                    "url": s["url"]
                })
            elif not all_history:
                url = None
                break

        if url and "next" in resp.links:
            url = resp.links["next"]["url"]
            params = {}
        else:
            url = None

    all_statuses.sort(key=lambda x: x["created_at"])
    return all_statuses


def process_and_write_digests(statuses: list):
    """Group posts by month in local timezone and generate 11ty markdown files."""
    os.makedirs(CONFIG["output_dir"], exist_ok=True)
    monthly_buckets = {}

    for s in statuses:
        utc_dt = datetime.datetime.fromisoformat(s["created_at"].replace("Z", "+00:00"))
        local_dt = utc_dt.astimezone(TARGET_TZ)
        month_key = local_dt.strftime("%Y-%m")

        if month_key not in monthly_buckets:
            monthly_buckets[month_key] = []
        monthly_buckets[month_key].append((local_dt, s))

    today = datetime.date.today()

    for month_key, month_entries in monthly_buckets.items():
        dt_sample = datetime.datetime.strptime(month_key, "%Y-%m")
        year, month_num = dt_sample.year, dt_sample.month
        month_name = dt_sample.strftime("%B")

        last_day = calendar.monthrange(year, month_num)[1]
        calendar_end_date = datetime.date(year, month_num, last_day)

        # Avoid future dates on active month so Eleventy doesn't skip publishing
        post_date = min(calendar_end_date, today) if (year == today.year and month_num == today.month) else calendar_end_date
        post_date_str = post_date.strftime("%Y-%m-%d")

        file_path = os.path.join(CONFIG["output_dir"], f"digest-{calendar_end_date.strftime('%Y-%m-%d')}.md")

        markdown_output = f"""---
title: "My Fediverse Digest for {month_name} {year}"
description: "A monthly digest of my thoughts, links, and updates from the Fediverse for {month_name} {year}."
date: {post_date_str}
permalink: /archive/fediverse-digest-{month_key}/
tags: ["Fediverse"]
---
"""
        last_day_str = ""
        for local_dt, s in month_entries:
            current_day_str = local_dt.strftime("%b %d")
            if current_day_str != last_day_str:
                markdown_output += f"\n## {current_day_str}\n\n"
                last_day_str = current_day_str

            time_stamp = local_dt.strftime("%I:%M %p")
            escaped_content = f"{{% raw %}}\n{s['content']}\n{{% endraw %}}"
            markdown_output += f"### {time_stamp}\n\n{escaped_content}\n\n[Source]({s['url']})\n\n---\n\n"

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(markdown_output)

        print(f"Generated Layout File: {file_path} ({len(month_entries)} entries synced)")


def main():
    parser = argparse.ArgumentParser(description="Generate 11ty Monthly Fediverse Digests natively or via API")
    parser.add_argument("--backfill", action="store_true", help="Fetch full history instead of current month")
    parser.add_argument("--db", type=str, default=CONFIG["db_path"], help="Path to local SQLite gts.db")
    args = parser.parse_args()

    # Step 1: Attempt native SQLite extraction from local DB
    target_posts = extract_from_sqlite(args.db, CONFIG["username"], all_history=args.backfill)

    # Step 2: Fall back to REST API if SQLite file is absent
    if target_posts is None:
        print(f"Database not found at '{args.db}'. Falling back to REST API...")
        target_posts = fetch_from_api(all_history=args.backfill)

    if target_posts:
        process_and_write_digests(target_posts)
        print("\nSuccess! Sync execution completed cleanly.")
    else:
        print("\nNo matching posts resolved during this window context.")


if __name__ == "__main__":
    main()