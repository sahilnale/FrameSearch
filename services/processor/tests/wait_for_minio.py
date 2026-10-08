"""Wait a bounded time for the isolated test server before running the suite."""

import os
import time
import urllib.error
import urllib.request

endpoint = os.environ["S3_ENDPOINT_INTERNAL"].rstrip("/")
for _attempt in range(30):
    try:
        with urllib.request.urlopen(f"{endpoint}/minio/health/ready", timeout=2) as response:
            if response.status == 200:
                print("Local MinIO test server is ready", flush=True)
                break
    except (urllib.error.URLError, TimeoutError):
        pass
    time.sleep(1)
else:
    raise SystemExit("MinIO did not become ready within the test startup limit")
