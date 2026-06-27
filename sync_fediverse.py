import os
import sys
import argparse
import requests
import datetime
import calendar
import html2text
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# Load local .env file variables
load_dotenv()

# --- Fallback Configuration from Environment Variables ---
CONFIG = {
    "instance_url": os.getenv("GOTOSOCIAL_INSTANCE_URL", "https://cuthrell.com").rstrip("/"),
    "access_token": os.getenv("GOTOSOCIAL_ACCESS_TOKEN"),
    "account_id": os.getenv("GOTOSOCIAL_ACCOUNT_ID"), # Optional: if omitted, script auto-detects via token lookup
    "output_dir": "./src/posts/fediverse"
}

def verify_and_detect_config():
    """Validates loaded .env values and discovers the account ID if missing."""
    if not CONFIG["access_token"]:
        print("CRITICAL ERROR: GOTOSOCIAL_ACCESS_TOKEN is missing from your environment or .env file.")
        sys.exit(1)
        
    headers = {"Authorization": f"Bearer {CONFIG['access_token']}"}
    
    # Auto-discover internal account ID if not provided explicitly in .env
    if not CONFIG["account_id"]:
        print("Discovering Account ID via credentials verification...")
        try:
            response = requests.get(f"{CONFIG['instance_url']}/api/v1/accounts/verify_credentials", headers=headers)
            response.raise_for_status()
            CONFIG["account_id"] = response.json()["id"]
            print(f" -> Auto-detected Account ID: {CONFIG['account_id']}")
        except Exception as e:
            print(f"CRITICAL ERROR: Failed to reach GoToSocial or verify token: {e}")
            sys.exit(1)

def fetch_posts(all_history=False):
    """
    Fetches posts from GoToSocial via standard Mastodon API pagination.
    If all_history is False, stops paginating when posts older than the current month are found.
    """
    url = f"{CONFIG['instance_url']}/api/v1/accounts/{CONFIG['account_id']}/statuses"
    headers = {"Authorization": f"Bearer {CONFIG['access_token']}"}
    params = {
        "limit": 40,
        "exclude_replies": True,
        "exclude_reblogs": True
    }
    
    now = datetime.datetime.now(datetime.timezone.utc)
    start_of_current_month = datetime.datetime(now.year, now.month, 1, tzinfo=datetime.timezone.utc)
    
    all_statuses = []
    keep_fetching = True
    
    print(f"Fetching posts from {CONFIG['instance_url']}...")
    
    while url and keep_fetching:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code != 200:
            print(f"API Error: {response.status_code} - {response.text}")
            break
            
        statuses = response.json()
        if not statuses:
            break
            
        for status in statuses:
            # Parse timeline ISO timestamp safely handling Z offset
            created_at_dt = datetime.datetime.fromisoformat(status["created_at"].replace("Z", "+00:00"))
            
            if all_history or created_at_dt >= start_of_current_month:
                all_statuses.append(status)
            else:
                # Post is older than the current month window
                if not all_history:
                    keep_fetching = False
                    break
                    
        print(f" -> Retrieved {len(statuses)} posts... Total kept: {len(all_statuses)}")
        
        if keep_fetching and "next" in response.links:
            url = response.links["next"]["url"]
            params = {}  # Link header URL already embeds next pagination markers (max_id)
        else:
            url = None
            
    # Return chronologically sorted items
    all_statuses.sort(key=lambda x: x["created_at"])
    return all_statuses

def clean_html_to_markdown(html_content):
    """Converts GoToSocial HTML safely into clean, layout-friendly Markdown."""
    if not html_content:
        return ""
    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = False
    h.body_width = 0  # Disable line wrapping artifacts inside codeblocks/paragraphs
    return h.handle(html_content).strip()

def process_and_write_digests(statuses):
    """Groups statuses by calendar month and outputs pure Markdown matching layout conventions."""
    os.makedirs(CONFIG["output_dir"], exist_ok=True)
    
    monthly_buckets = {}
    # Define the target timezone
    eastern_tz = ZoneInfo("US/Eastern")

    for status in statuses:
        dt = datetime.datetime.fromisoformat(status["created_at"].replace("Z", "+00:00"))
        month_key = dt.strftime("%Y-%m")
        if month_key not in monthly_buckets:
            monthly_buckets[month_key] = []
        monthly_buckets[month_key].append(status)
        
    last_day_str = ""

    for month_key, month_statuses in monthly_buckets.items():
        dt_sample = datetime.datetime.strptime(month_key, "%Y-%m")
        year = dt_sample.year
        month_num = dt_sample.month
        month_name = dt_sample.strftime("%B")
        
        # Calculate the exact last day of that month for standard index date formatting
        last_day = calendar.monthrange(year, month_num)[1]
        last_date_str = f"{year}-{month_num:02d}-{last_day:02d}"
        
        file_name = f"digest-{last_date_str}.md"
        file_path = os.path.join(CONFIG["output_dir"], file_name)
        
        # Generate perfect front-matter matching post.njk expectations
        markdown_output = f"""---
title: "My Fediverse Digest for {month_name} {year}"
description: "A monthly digest of my thoughts, links, and updates from the Fediverse for {month_name} {year}."
date: {last_date_str}
permalink: /archive/fediverse/{month_key}/
tags: ["fediverse", "digest"]
---
"""
        for s in month_statuses:
            status_dt = datetime.datetime.fromisoformat(s["created_at"].replace("Z", "+00:00"))
            # Convert to the desired timezone
            status_dt_eastern = status_dt.astimezone(eastern_tz)

            current_day_str = status_dt_eastern.strftime("%b %d")
            if current_day_str != last_day_str:
                markdown_output += f"\n## {current_day_str}\n\n"
                last_day_str = current_day_str

            time_stamp = status_dt_eastern.strftime("%I:%M %p")
            markdown_content = clean_html_to_markdown(s["content"])
            
            markdown_output += f"### {time_stamp}\n\n{markdown_content}\n\n[Source]({s['url']})\n\n---\n\n"
            
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(markdown_output)
            
        print(f"Generated Layout File: {file_path} ({len(month_statuses)} entries synced)")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GoToSocial to Eleventy Monthly Digest Automation Script")
    parser.add_argument("--backfill", action="store_true", help="Fetch complete account historical profile instead of just the current month")
    args = parser.parse_args()
    
    verify_and_detect_config()
    target_posts = fetch_posts(all_history=args.backfill)
    
    if target_posts:
        process_and_write_digests(target_posts)
        print("\nSuccess! Sync execution completed cleanly.")
    else:
        print("\nNo matching posts resolved during this window context.")