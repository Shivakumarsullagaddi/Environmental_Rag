#!/usr/bin/env python3
import os
from google.cloud import storage

client = storage.Client()
bucket = client.bucket('agriculture_rag_data')
scratch_dir = os.path.join(os.path.dirname(__file__), '..', 'scratch')
os.makedirs(scratch_dir, exist_ok=True)

files = [
    ('books/environmental_science/Introduction to Environmental Science 2025 edition.pdf', 'Introduction to Environmental Science 2025 edition.pdf'),
    ('books/soil/introduction-to-soil-science.pdf', 'introduction-to-soil-science.pdf'),
]

for blob_path, filename in files:
    dest = os.path.join(scratch_dir, filename)
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        print(f"Already exists: {dest} ({os.path.getsize(dest)} bytes)")
        continue
    print(f"Downloading {blob_path} to {dest}...")
    blob = bucket.blob(blob_path)
    blob.download_to_filename(dest)
    print(f"Finished {filename}: {os.path.getsize(dest)} bytes")
