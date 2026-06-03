import requests

BASE_URL = "http://127.0.0.1:5000"

# Change this to an image you want to verify
IMAGE_PATH = "static/images/0.png"   # <-- edit this

with open(IMAGE_PATH, "rb") as f:
    files = {"image": ("face.png", f, "image/png")}
    r = requests.post(f"{BASE_URL}/api/face/verify", files=files)

print("Status code:", r.status_code)
print(r.json())
