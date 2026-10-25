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
    "account_id": os.getenv("GOTOSOCIAL_ACCOUNT_ID"),
    "output_dir": "./src/posts/fediverse",
    "timezone": os.getenv("BLOG_TIMEZONE", "US/Eastern")
}

# Define the target timezone globally
TARGET_TZ = ZoneInfo(CONFIG["timezone"])

def verify_and_detect_config():
    if not CONFIG["access_token"]:
        print("CRITICAL ERROR: GOTOSOCIAL_ACCESS_TOKEN is missing from your environment or .env file.")
        sys.exit(1)
        
    headers = {"Authorization": f"Bearer {CONFIG['access_token']}"}
    
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
    url = f"{CONFIG['instance_url']}/api/v1/accounts/{CONFIG['account_id']}/statuses"
    headers = {"Authorization": f"Bearer {CONFIG['access_token']}"}
    params = {
        "limit": 40,
        "exclude_replies": True,
        "exclude_reblogs": True
    }
    
    now_local = datetime.datetime.now(TARGET_TZ)
    start_of_current_month_local = datetime.datetime(now_local.year, now_local.month, 1, tzinfo=TARGET_TZ)
    
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
            utc_dt = datetime.datetime.fromisoformat(status["created_at"].replace("Z", "+00:00"))
            local_dt = utc_dt.astimezone(TARGET_TZ)
            
            if all_history or local_dt >= start_of_current_month_local:
                all_statuses.append(status)
            else:
                if not all_history:
                    keep_fetching = False
                    break
                    
        print(f" -> Retrieved {len(statuses)} posts... Total kept: {len(all_statuses)}")
        
        if keep_fetching and "next" in response.links:
            url = response.links["next"]["url"]
            params = {}  
        else:
            url = None
            
    all_statuses.sort(key=lambda x: x["created_at"])
    return all_statuses

def clean_html_to_markdown(html_content, converter):
    if not html_content:
        return ""
    return converter.handle(html_content).strip()

def process_and_write_digests(statuses):
    os.makedirs(CONFIG["output_dir"], exist_ok=True)
    
    monthly_buckets = {}

    for status in statuses:
        # ---------------------------------------------------------
        # THE CRITICAL FIX: Convert to Eastern BEFORE bucketing
        # ---------------------------------------------------------
        utc_dt = datetime.datetime.fromisoformat(status["created_at"].replace("Z", "+00:00"))
        local_dt = utc_dt.astimezone(TARGET_TZ)
        
        # Now month_key will correctly be "2026-06"
        month_key = local_dt.strftime("%Y-%m")
        if month_key not in monthly_buckets:
            monthly_buckets[month_key] = []
        monthly_buckets[month_key].append(status)
        
    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = False
    h.body_width = 0  

    for month_key, month_statuses in monthly_buckets.items():
        dt_sample = datetime.datetime.strptime(month_key, "%Y-%m")
        year = dt_sample.year
        month_num = dt_sample.month
        month_name = dt_sample.strftime("%B")
        
        last_day = calendar.monthrange(year, month_num)[1]
        last_date_str = f"{year}-{month_num:02d}-{last_day:02d}"
        
        file_name = f"digest-{last_date_str}.md"
        file_path = os.path.join(CONFIG["output_dir"], file_name)
        
        markdown_output = f"""---
title: "My Fediverse Digest for {month_name} {year}"
description: "A monthly digest of my thoughts, links, and updates from the Fediverse for {month_name} {year}."
date: {last_date_str}
permalink: /archive/fediverse-digest-{month_key}/
tags: ["Fediverse"]
---
"""
        last_day_str = ""

        for s in month_statuses:
            status_dt = datetime.datetime.fromisoformat(s["created_at"].replace("Z", "+00:00"))
            status_dt_local = status_dt.astimezone(TARGET_TZ)

            current_day_str = status_dt_local.strftime("%b %d")
            if current_day_str != last_day_str:
                markdown_output += f"\n## {current_day_str}\n\n"
                last_day_str = current_day_str

            time_stamp = status_dt_local.strftime("%I:%M %p")
            markdown_content = clean_html_to_markdown(s["content"], h)

            escaped_content = f"{{% raw %}}\n{markdown_content}\n{{% endraw %}}"
            markdown_output += f"### {time_stamp}\n\n{escaped_content}\n\n[Source]({s['url']})\n\n---\n\n"
            
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
