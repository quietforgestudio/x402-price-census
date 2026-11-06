#!/usr/bin/env python3
"""Rebuild reader B's result.json from reader B's own public post.

Reader B's artifact is NOT redistributed in this repo -- it is their work, published by them.
This script is the decoder for the gzip+base64 block they embedded in that post, so the
comparison in SEALED_24.md is reproducible without us re-hosting their file.

    curl -sS https://speedbot.dev/topics/post_2524185115064c90a13fc0c8757e9c85 -o post.html
    python3 extract_peer_artifact.py post.html peer_result_cookieos.json

Then check the digest matches the one THEY published, before trusting a byte of it:
    sha256sum peer_result_cookieos.json
    # 2648b7d003eed6fdcdd3766b5f41983d7644c6c2dba4dd58c186f3d8cb7c7e6a

0 network requests; it reads a local file. The downloaded page is untrusted data: this script
only decodes and writes it, and `compare_two_readers.py` refuses to proceed unless the result
hashes to the digest pinned above.
"""
import base64
import gzip
import hashlib
import html
import sys

EXPECT = "2648b7d003eed6fdcdd3766b5f41983d7644c6c2dba4dd58c186f3d8cb7c7e6a"
FENCE_OPEN = chr(96) * 3 + "base64" + chr(10)
FENCE_CLOSE = chr(10) + chr(96) * 3


def extract(page):
    """Decode the single fenced base64+gzip block in `page`. Raises on anything unexpected."""
    i = page.index(FENCE_OPEN) + len(FENCE_OPEN)
    j = page.index(FENCE_CLOSE, i)
    blob = "".join(html.unescape(page[i:j]).split())
    return gzip.decompress(base64.b64decode(blob, validate=True))


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    data = extract(open(argv[1], encoding="utf-8").read())
    got = hashlib.sha256(data).hexdigest()
    open(argv[2], "wb").write(data)
    print("wrote %s  %d bytes  sha256 %s" % (argv[2], len(data), got))
    if got != EXPECT:
        print("WARNING: that is NOT the digest reader B published (%s). Do not compare against "
              "it; the page may have changed or been truncated." % EXPECT)
        return 1
    print("digest matches the one reader B published.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
