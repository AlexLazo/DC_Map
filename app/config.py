import os

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./dc_map.db")
SESSION_SECRET = os.getenv("SESSION_SECRET", "change-me-in-production-please")
