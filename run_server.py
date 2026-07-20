#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
import logging
logging.basicConfig(level=logging.DEBUG)

from app.main import app
import uvicorn

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8766, log_level="debug")
