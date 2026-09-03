Put credential files here. This directory is git-ignored in full.

Firebase: save the service account JSON as firebase-service-account.json
(Firebase Console > Project Settings > Service accounts > Generate new
private key), then set in .env:

  FIREBASE_CREDENTIALS_FILE=secrets/firebase-service-account.json

Relative paths resolve against the directory holding manage.py.
