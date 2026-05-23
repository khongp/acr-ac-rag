import os
import requests
from bs4 import BeautifulSoup
import time
import logging
import re

def download_pdfs(output_dir="data/pdf_narratives", max_topic_id=2000):
    os.makedirs(output_dir, exist_ok=True)
    
    # Setup simple logging
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    
    records_downloaded = 0
    consecutive_errors = 0
    
    # We'll use a session to reuse connections and be slightly faster/kinder to the server
    session = requests.Session()
    
    # Adding a user-agent to play nice
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36"
    })
    
    logging.info(f"Starting to download PDFs to {os.path.abspath(output_dir)}")
    
    for topic_id in range(1, max_topic_id + 1):
        # We can break if we hit a huge number of consecutive misses
        if consecutive_errors > 100:
            logging.info("Stopping after 100 consecutive empty topics. Assuming end of records.")
            break
            
        narrative_url = f"https://gravitas.acr.org/ACPortal/TopicNarrative?topicId={topic_id}"
        pdf_url = f"https://gravitas.acr.org/ACPortal/TopicNarrativePdf?topicId={topic_id}"
        
        try:
            # First, fetch the narrative page to get the title
            resp = session.get(narrative_url, timeout=10)
            
            # If the response is a 500 error or similar, just skip
            if resp.status_code != 200:
                consecutive_errors += 1
                time.sleep(0.1)
                continue
                
            # Parse HTML to find the h1 tag for the title
            soup = BeautifulSoup(resp.text, 'html.parser')
            h1 = soup.find('h1')
            if not h1 or not h1.text.strip():
                # Some IDs might return a valid page but not an actual narrative
                consecutive_errors += 1
                time.sleep(0.1)
                continue
                
            # Clean the title for use as a filename
            title = h1.text.strip()
            title = re.sub(r'<[^>]+>', '', title)
            # Replace invalid filename characters with underscore
            safe_title = re.sub(r'[\\/*?:"<>|]', '_', title)
            safe_title = re.sub(r'_+', '_', safe_title).strip('_ ')
            
            filepath = os.path.join(output_dir, f"{safe_title}_{topic_id}.pdf")
            
            # Skip if already downloaded
            if os.path.exists(filepath):
                logging.info(f"Topic {topic_id}: Already downloaded ({safe_title})")
                consecutive_errors = 0
                continue
            
            # Fetch the actual PDF
            pdf_resp = session.get(pdf_url, stream=True, timeout=30)
            if pdf_resp.status_code != 200:
                logging.info(f"Topic {topic_id}: '{title}' PDF not found (Status {pdf_resp.status_code})")
                consecutive_errors += 1
                continue
                
            content_type = pdf_resp.headers.get('Content-Type', '')
            if 'application/pdf' not in content_type:
                logging.info(f"Topic {topic_id}: '{title}' did not return a PDF (Content-Type: {content_type})")
                consecutive_errors += 1
                continue
            
            # Write to disk
            with open(filepath, 'wb') as f:
                for chunk in pdf_resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            logging.info(f"Downloaded Topic {topic_id}: {safe_title}")
            records_downloaded += 1
            consecutive_errors = 0
            
            # Polite delay after a successful download
            time.sleep(1)
            
        except Exception as e:
            logging.error(f"Topic {topic_id}: Error - {str(e)}")
            consecutive_errors += 1
            time.sleep(0.5)
        
    logging.info(f"Finished. Downloaded {records_downloaded} new PDFs.")

if __name__ == "__main__":
    download_pdfs()
