import requests

BASE_URL = "http://127.0.0.1:5000"

# Change this to your real image path (front-facing, clear face)
IMAGE_PATH = "static/images/0.png"   # <-- edit this

data = {
    "name": "Test Student",
    "email": "test@student.com",
    "userType": "student",
    "classes": ["CSIT101", "CSIT213"],   # can be 1 or more
    "password": "password123"
}

with open(IMAGE_PATH, "rb") as f:
    files = {"image": ("face.png", f, "image/png")}
    r = requests.post(f"{BASE_URL}/api/face/enrol", data=data, files=files)

print("Status code:", r.status_code)
print(r.json())
