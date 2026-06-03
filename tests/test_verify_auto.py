import requests

BASE_URL = "http://127.0.0.1:5000"

# 1) Fetch a real embedding from Firebase via your debug endpoint
r1 = requests.get(f"{BASE_URL}/api/debug/sample_embedding")
print("Sample status:", r1.status_code)
print("Sample raw:", r1.text[:200])

sample = r1.json()
embedding = sample["data"]["embedding"]
student_id = sample["data"]["student_id"]
name = sample["data"]["name"]

print(f"Fetched embedding for student_id={student_id}, name={name}, len={len(embedding)}")

# 2) Send that embedding to your verify endpoint
r2 = requests.post(f"{BASE_URL}/api/face/verify", json={"embedding": embedding})
print("Verify status:", r2.status_code)
print("Verify JSON:", r2.json())