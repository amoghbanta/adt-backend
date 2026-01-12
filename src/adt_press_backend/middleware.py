
import time
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from collections import defaultdict, deque
from typing import Dict, Tuple

class RateLimiter:
    """
    Simple in-memory rate limiter using a fixed window algorithm.
    Tracks requests per IP/Key within a time window.
    """
    def __init__(self, requests_per_minute: int = 60):
        self.rpm = requests_per_minute
        # Map of identifier -> valid_until_timestamp
        # Or better: Map of identifier -> list of timestamps (sliding window)
        # For simplicity/performance, let's use a Token Bucket approximation or just fixed window.
        
        # Using a simple Fixed Window for MVP:
        # identifier -> (count, window_start_time)
        self.requests: Dict[str, Tuple[int, float]] = {}

    def is_allowed(self, identifier: str) -> bool:
        now = time.time()
        count, start_time = self.requests.get(identifier, (0, now))
        
        if now - start_time > 60:
            # New window
            self.requests[identifier] = (1, now)
            return True
        
        if count >= self.rpm:
            return False
            
        self.requests[identifier] = (count + 1, start_time)
        return True

    def cleanup(self):
        """Cleanup old entries to prevent memory leak"""
        now = time.time()
        keys_to_delete = [k for k, v in self.requests.items() if now - v[1] > 60]
        for k in keys_to_delete:
            del self.requests[k]

class APIKeyMiddleware(BaseHTTPMiddleware):
    """
    Middleware to extract API Key but NOT enforce it globally yet.
    Enforcement is better handled via specific Dependencies on routes,
    but this middleware could handle global rate limiting based on key or IP.
    """
    pass 
    # Actually, for FastAPI it's often better to use Dependencies for Auth.
    # But for Rate Limiting, Middleware is good.
    
    # We will implement Rate Limiting Logic here? 
    # Or just keep RateLimiter as a utility class used by Dependencies?
    
    # Plan says: "Create middleware to extract and validate X-API-Key"
    # Validating in middleware for ALL routes is strict. 
    # Let's stick to using Dependencies for validation to allow public endpoints if needed (like healthz).
    
    # However, implementing Global Rate Limiting here.
