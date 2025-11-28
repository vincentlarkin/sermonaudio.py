import requests
from bs4 import BeautifulSoup
import os
import urllib.parse
import re
from tqdm import tqdm


def sanitize_filename(filename):
    # Remove or replace characters that are invalid in filenames
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    filename = filename.strip()
    # Limit filename length (max 255 characters including extension)
    return filename[:251] + '.mp3'  # 251 + 4 (.mp3) = 255

def fetch_sermon_data(url):
    if not url.startswith('https://'):
        url = 'https://' + url

    response = requests.get(url)
    soup = BeautifulSoup(response.content, 'html.parser')

    # Try multiple methods to find the title
    title = None
    title_element = soup.find(attrs={"data-v-29c0d6dd": True, "class": "title"})
    if title_element:
        title = title_element.text.strip()
    
    if not title:
        meta_title = soup.find('meta', property='og:title')
        if meta_title:
            title = meta_title['content']
    
    if not title:
        title_tag = soup.find('title')
        if title_tag:
            title = title_tag.text.strip()
    
    if not title:
        h1_tag = soup.find('h1')
        if h1_tag:
            title = h1_tag.text.strip()

    if not title:
        title_element = soup.find(class_=re.compile('title', re.I))
        if title_element:
            title = title_element.text.strip()

    title = title if title else "Untitled Sermon"

    speaker_element = soup.find(attrs={"data-v-29c0d6dd": True, "class": "speaker"})
    date_element = soup.find(attrs={"data-v-29c0d6dd": True, "class": "date"})

    speaker = speaker_element.text.strip() if speaker_element else "Unknown Speaker"
    date = date_element.text.strip() if date_element else "Unknown Date"

    audio_element = soup.find('audio')
    mp3_url = audio_element.get('src') if audio_element else None

    return {
        "title": title,
        "speaker": speaker,
        "date": date,
        "mp3_url": mp3_url
    }

def download_sermon(url, output_directory):
    sermon_data = fetch_sermon_data(url)
    print("Sermon Title:", sermon_data['title'])
    print("Speaker:", sermon_data['speaker'])
    print("Date:", sermon_data['date'])
    print("MP3 URL:", sermon_data['mp3_url'])

    if sermon_data['mp3_url']:
        try:
            encoded_url = urllib.parse.quote(sermon_data['mp3_url'], safe=':/?&=')
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            
            head_response = requests.head(encoded_url, headers=headers)
            head_response.raise_for_status()
            
            content_type = head_response.headers.get('Content-Type', '')
            content_length = int(head_response.headers.get('Content-Length', 0))
            
            if 'audio' not in content_type.lower():
                print(f"Warning: Content type is {content_type}, not audio as expected.")
            
            print(f"File size: {content_length / (1024*1024):.2f} MB")
            
            mp3_response = requests.get(encoded_url, headers=headers, stream=True)
            mp3_response.raise_for_status()

            filename = sanitize_filename(sermon_data['title'])
            filepath = os.path.join(output_directory, filename)
            
            with open(filepath, 'wb') as f, tqdm(
                desc=filename,
                total=content_length,
                unit='iB',
                unit_scale=True,
                unit_divisor=1024,
            ) as progress_bar:
                for data in mp3_response.iter_content(chunk_size=8192):
                    size = f.write(data)
                    progress_bar.update(size)
            print(f"Downloaded: {filename}")
            return True
        except requests.exceptions.RequestException as e:
            print(f"Error during download: {e}")
        except IOError as e:
            print(f"Error writing file: {e}")
    else:
        print("MP3 URL not found in sermon data")
    return False


# Example usage
url = "beta.sermonaudio.com/sermons/714242349564949/"
output_directory = "downloaded_sermons"

os.makedirs(output_directory, exist_ok=True)

download_sermon(url, output_directory)