import os
import requests
from bs4 import BeautifulSoup
import time
import logging
import re

def get_pdf_text(filepath):
    from pypdf import PdfReader
    try:
        reader = PdfReader(filepath)
        text = ""
        for page in reader.pages:
            t = page.extract_text()
            if t:
                text += t + "\n"
        return text.strip()
    except Exception:
        return ""

def download_pdfs(output_dir="data/pdf_narratives", max_topic_id=2500):
    import glob
    os.makedirs(output_dir, exist_ok=True)
    
    # Setup simple logging
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    
    records_downloaded = 0
    consecutive_errors = 0
    new_articles = []
    
    # We'll use a session to reuse connections and be slightly faster/kinder to the server
    session = requests.Session()
    
    # Adding a user-agent to play nice
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36"
    })
    
    logging.info(f"Starting to scan and download PDFs to {os.path.abspath(output_dir)} (up to ID {max_topic_id})")
    
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
            
            target_filename = f"{safe_title}_{topic_id}.pdf"
            filepath = os.path.join(output_dir, target_filename)
            
            # Check if any file ending with _{topic_id}.pdf already exists to prevent duplicate titles
            pattern = os.path.join(output_dir, f"*_{topic_id}.pdf")
            existing_files = glob.glob(pattern)
            
            existing_file = None
            if existing_files:
                existing_file = existing_files[0]
                existing_filename = os.path.basename(existing_file)
                if existing_filename != target_filename:
                    # Title has changed: delete old version to avoid duplicate files
                    logging.info(f"Topic {topic_id}: Title updated from '{existing_filename}' to '{target_filename}'. Deleting old file.")
                    try:
                        os.remove(existing_file)
                        existing_file = None
                    except Exception as e:
                        logging.error(f"Failed to delete duplicate/old file {existing_file}: {e}")
            
            # If the file already exists (same name and ID), verify if it has updates
            if existing_file and os.path.exists(existing_file):
                local_size = os.path.getsize(existing_file)
                
                # Check remote Content-Length with a fast HEAD request
                remote_size = None
                try:
                    head_resp = session.head(pdf_url, timeout=10)
                    if head_resp.status_code == 200:
                        size_header = head_resp.headers.get('Content-Length')
                        if size_header:
                            remote_size = int(size_header)
                except Exception as e:
                    logging.debug(f"HEAD request failed for Topic {topic_id}: {e}")
                    
                if remote_size is not None:
                    if local_size == remote_size:
                        # Sizes match exactly; assume no content update and skip
                        consecutive_errors = 0
                        continue
                    else:
                        logging.info(f"Topic {topic_id}: Size mismatch detected (Local: {local_size} bytes, Remote: {remote_size} bytes). Fetching update.")
                else:
                    # Content-Length is missing or HEAD failed; fallback to check via downloading
                    logging.info(f"Topic {topic_id}: Size header not found. Fetching to verify content.")
            
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
            
            # Download stream to temporary file first (for checksum matching)
            temp_filepath = filepath + ".tmp"
            with open(temp_filepath, 'wb') as f:
                for chunk in pdf_resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            is_updated = True
            if existing_file and os.path.exists(existing_file):
                local_text = get_pdf_text(existing_file)
                temp_text = get_pdf_text(temp_filepath)
                if local_text and temp_text and local_text == temp_text:
                    is_updated = False
                    os.remove(temp_filepath)
                    
            if is_updated:
                if os.path.exists(filepath):
                    os.remove(filepath)
                os.rename(temp_filepath, filepath)
                logging.info(f"Downloaded/Updated Topic {topic_id}: {safe_title}")
                new_articles.append(f"Topic {topic_id}: {title} ({target_filename})")
                records_downloaded += 1
                consecutive_errors = 0
                time.sleep(1) # Polite delay
            else:
                consecutive_errors = 0
            
        except Exception as e:
            logging.error(f"Topic {topic_id}: Error - {str(e)}")
            consecutive_errors += 1
            time.sleep(0.5)
            
    logging.info(f"Finished. Downloaded {records_downloaded} new or updated PDFs.")
    if new_articles:
        logging.info("\n=== NEW OR UPDATED GUIDELINES FOUND ===")
        for art in new_articles:
            logging.info(f" - {art}")
        logging.info("========================================\n")
    else:
        logging.info("\n=== NO NEW GUIDELINES DISCOVERED ===\n")

if __name__ == "__main__":
    download_pdfs()
