import sys
sys.path.insert(0, r"C:\Yeeps-2.32.0-yeeper920\server")
import pymongo
from config import MONGO_URI, DB_NAME
db = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=8000)[DB_NAME]
print("extra_unknown (recent):")
for d in db.extra_unknown.find({}, {"_id": 0}).sort("lastAt", -1).limit(10):
    print(" ", d.get("count"), d.get("fn"), d.get("lastAt", "")[:19])
print("purchase_calls customized (recent):")
cnt = 0
for d in db.purchase_calls.find({}, {"_id": 0}).sort("_id", -1).limit(5):
    if "customized" in str(d.get("fn", "")).lower():
        cnt += 1
        if cnt <= 2:
            print("  resp keys:", list(d.get("resp", {}).keys()))
print("total customized:", cnt)