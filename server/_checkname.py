import sys
sys.path.insert(0, r"C:\Yeeps-2.32.0-yeeper920\server")
import pymongo
from config import MONGO_URI, DB_NAME
db = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=8000)[DB_NAME]
doc = db.accounts.find_one({"displayName": "yeeper920"}, {"_id": 0, "accountID": 1, "displayName": 1})
print("by displayName:", doc)
for a in db.accounts.find({}, {"_id": 0, "accountID": 1, "displayName": 1}):
    print("  account:", a.get("accountID"), "->", repr(a.get("displayName")))