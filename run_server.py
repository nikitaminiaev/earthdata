#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from app.main import app
import uvicorn

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8766, log_level="info")
