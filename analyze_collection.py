import json, re, sys
sys.stdout.reconfigure(encoding="utf-8")

with open("Brighter_API_Cabang_Puri_Complete.postman_collection.json", encoding="utf-8") as f:
    data = json.load(f)

endpoints = []

def walk_items(items, parent=""):
    for item in items:
        if "request" in item:
            name = item["name"]
            method = item["request"]["method"]
            url = item["request"]["url"]["raw"] if isinstance(item["request"]["url"], dict) else ""
            has_ts = "timestamp_data=true" in url
            has_page = "page=" in url
            path = re.sub(r"\?.*$", "", url).replace("{{base_url}}", "")
            is_get = method == "GET"
            endpoints.append({
                "parent": parent.encode("ascii", "ignore").decode(), "name": name.encode("ascii", "ignore").decode(), "method": method,
                "path": path, "has_timestamp": has_ts, "has_page": has_page, "is_get": is_get
            })
        elif "item" in item:
            walk_items(item["item"], item["name"])

walk_items(data["item"])

print(f"Total endpoints: {len(endpoints)}")
print(f"GET: {sum(1 for e in endpoints if e['is_get'])}")
non_get = [e for e in endpoints if not e["is_get"]]
print(f"Non-GET (POST/PUT/PATCH): {len(non_get)}")
print()

ts_paged = [e for e in endpoints if e["is_get"] and e["has_timestamp"] and e["has_page"]]
ts_no_page = [e for e in endpoints if e["is_get"] and e["has_timestamp"] and not e["has_page"]]
no_ts_paged = [e for e in endpoints if e["is_get"] and not e["has_timestamp"] and e["has_page"]]
no_ts_no_page = [e for e in endpoints if e["is_get"] and not e["has_timestamp"] and not e["has_page"]]

print(f"GET + timestamp + paging: {len(ts_paged)}")
print(f"GET + timestamp - paging: {len(ts_no_page)}")
print(f"GET - timestamp + paging: {len(no_ts_paged)}")
print(f"GET - timestamp - paging: {len(no_ts_no_page)}")
print()

print("=== Non-GET endpoints ===")
for e in non_get:
    print(f"  {e['method']} {e['path']}  ({e['parent']})")
print()

print("=== GET - timestamp - paging (full-replace) ===")
for e in no_ts_no_page:
    print(f"  {e['path']}  ({e['parent']})")
print()

print("=== GET - timestamp + paging ===")
for e in no_ts_paged:
    print(f"  {e['path']}  ({e['parent']})")
print()

print("=== GET + timestamp - paging ===")
for e in ts_no_page:
    print(f"  {e['path']}  ({e['parent']})")

# Group by parent folder
from collections import Counter
folder_counts = Counter()
for e in endpoints:
    if e["is_get"]:
        key = f"{'TS' if e['has_timestamp'] else 'NO_TS'}_{'PG' if e['has_page'] else 'NO_PG'}"
        folder_counts[(e["parent"], key)] += 1

print("\n=== Endpoint distribution by folder ===")
for (folder, cat), count in folder_counts.most_common():
    print(f"  {cat:10s} {folder:35s} x{count}")
