#!/usr/bin/env python3
import os
import sys
import sqlite3
import argparse
import calendar
import datetime
import requests
import html2text
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

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


def extract_from_sqlite(db_path: str, username: str, all_history: bool = False):
    """Read native Markdown and media attachments directly from GoToSocial's SQLite database."""
    if not os.path.exists(db_path):
        return None

    print(f"Reading natively from SQLite database: {db_path}")
    conn = sqlite3.connect(f"file:{os.path.abspath(db_path)}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Locate account ID
    cursor.execute("SELECT id FROM accounts WHERE username = ? AND (domain IS NULL OR domain = '')", (username,))
    account = cursor.fetchone()
    if not account:
        conn.close()
        raise ValueError(f"Account '{username}' not found in {db_path}")

    account_id = account["id"]

    # Match posts (0, 1, 2 for public/unlisted enums)
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
        placeholders = ",".join("?" for _ in status_ids)
        # Select available columns
        select_cols = ["id", "status_id"]
        for opt in ["description", "remote_url", "file_path"]:
            if opt in media_columns:
                select_cols.append(opt)

        media_sql = f"SELECT {', '.join(select_cols)} FROM media_attachments WHERE status_id IN ({placeholders})"
        cursor.execute(media_sql, status_ids)
        media_rows = cursor.fetchall()

        instance_base = CONFIG["instance_url"]

        for m in media_rows:
            sid = m["status_id"]
            if sid not in attachments_by_status:
                attachments_by_status[sid] = []

            # Determine public hotlink URL
            remote_url = m["remote_url"] if "remote_url" in media_columns else None
            file_path = m["file_path"] if "file_path" in media_columns else None

            if remote_url and remote_url.strip():
                image_url = remote_url.strip()
            elif file_path and file_path.strip():
                clean_path = file_path.lstrip("/")
                image_url = f"{instance_base}/fileserver/{clean_path}"
            else:
                image_url = f"{instance_base}/fileserver/{account_id}/attachment/original/{m['id']}"

            raw_desc = (m["description"] if "description" in media_columns else None) or "Attached image"
            alt_text = raw_desc.replace("\n", " ").replace('"', '&quot;').strip()

            attachments_by_status[sid].append({"url": image_url, "alt": alt_text})

    h = html2text.HTML2Text()
    h.ignore_links = False
    h.body_width = 0

    posts = []
    for r in status_rows:
        content = r["text"] if r["text"] and r["text"].strip() else h.handle(r["content"] or "").strip()
        
        # Attach images directly to markdown
        media_items = attachments_by_status.get(r["id"], [])
        if media_items:
            img_markdown = "\n\n" + "\n\n".join(f"![{m['alt']}]({m['url']})" for m in media_items)
            content += img_markdown

        posts.append({
            "id": r["id"],
            "created_at": r["created_at"],
            "content": content,
            "url": r["url"] or f"{CONFIG['instance_url']}/@{username}/statuses/{r['id']}"
        })

    conn.close()
    return posts


def process_and_write_digests(statuses):
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

        # Avoid future dates on active current month
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate 11ty Monthly Digests with hotlinked images")
    parser.add_argument("--backfill", action="store_true", help="Fetch full history instead of current month")
    parser.add_argument("--db", type=str, default=CONFIG["db_path"], help="Path to local SQLite gts.db")
    args = parser.parse_args()

    target_posts = extract_from_sqlite(args.db, CONFIG["username"], all_history=args.backfill)

    if target_posts:
        process_and_write_digests(target_posts)
        print("\nSuccess! Sync execution completed cleanly.")
    else:
        print("\nNo matching posts resolved during this window context.")