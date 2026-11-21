"""Download the real documents for the document search and index them.

Usage: python3 scripts/fetch_documents.py
Needs network access and pypdf (pip install -r requirements-dev.txt).
Writes data/docs/raw/ and data/docs/index/, which are gitignored.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import documents  # noqa: E402

for source in documents.SOURCES:
    done = documents.build_index(source)
    print(f"{source['id']}: {done['passages']} passages written to {documents.INDEX_DIR / (source['id'] + '.json')}; "
          f"{done['pruned']} stale cached vectors removed")
