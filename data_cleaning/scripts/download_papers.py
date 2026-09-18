#!/usr/bin/env python3
import os
from google.cloud import storage

client = storage.Client()
bucket = client.bucket('agriculture_rag_data')
scratch_dir = os.path.join(os.path.dirname(__file__), '..', 'scratch')
os.makedirs(scratch_dir, exist_ok=True)

papers = [
    ('papers/2.pdf', '2.pdf'),
    ('papers/5.pdf', '5.pdf'),
    ('papers/7.pdf', '7.pdf'),
    ('papers/10.pdf', '10.pdf'),
    ('papers/11.pdf', '11.pdf'),
]

for blob_path, fname in papers:
    dest = os.path.join(scratch_dir, fname)
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        print(f"Already exists: {dest} ({os.path.getsize(dest)} bytes)")
        continue
    print(f"Downloading {blob_path} to {dest}...")
    blob = bucket.blob(blob_path)
    blob.download_to_filename(dest)
    print(f"Downloaded {dest}: {os.path.getsize(dest)} bytes")
