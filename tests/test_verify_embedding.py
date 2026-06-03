import requests

BASE_URL = "http://127.0.0.1:5000"

# Put a REAL embedding here (128 floats)
embedding = [0.1] * 128

r = requests.post(
    f"{BASE_URL}/api/face/verify",
    json={"embedding": embedding},
    headers={"Content-Type": "application/json"},
)

print("Status code:", r.status_code)
print("Content-Type:", r.headers.get("Content-Type"))
print("Raw response:\n", r.text[:500])

# Only try JSON if server says it's JSON
if "application/json" in (r.headers.get("Content-Type") or ""):
    print("JSON:", r.json())