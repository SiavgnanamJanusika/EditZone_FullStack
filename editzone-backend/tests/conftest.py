import os

# Unit tests must never resolve or connect to a developer/production Atlas URI
# merely while importing application modules.
os.environ["ENV"] = "test"
os.environ["MONGO_URI"] = "mongodb://127.0.0.1:27017"
os.environ["MONGO_DB_NAME"] = "editzone_test"
os.environ["JWT_SECRET_KEY"] = "test-only-secret-that-is-longer-than-thirty-two-characters"
