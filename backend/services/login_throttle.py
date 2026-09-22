"""Atomic failed-login cooldowns; Redis in normal use, memory in explicit dev mode."""
import hashlib
import math
from threading import Lock
import time

from fastapi import HTTPException
from redis.exceptions import RedisError

from backend.config import ProtectionSettings
from backend.services.session_store import RedisSessionStore

# Check and update in one operation: concurrent failures cannot lose increments.
LOGIN_ATTEMPT = """
local count = tonumber(redis.call('GET', KEYS[1]) or '0')
if count >= tonumber(ARGV[1]) then
    return math.max(1, redis.call('TTL', KEYS[1]))
end
if ARGV[3] == '1' then
    redis.call('DEL', KEYS[1])
    return 0
end
count = redis.call('INCR', KEYS[1])
if count == 1 or count == tonumber(ARGV[1]) then
    redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""


class LoginThrottle:
    def __init__(self, store, settings=None, clock=time.monotonic):
        self.settings = settings or ProtectionSettings.from_environment()
        self.store = store
        self.clock = clock
        self.lock = Lock()
        self.failures = {}

    def attempt(self, client, valid):
        identity = hashlib.sha256(client.encode()).hexdigest()
        limit = self.settings.admin_login_attempts
        cooldown = self.settings.admin_login_cooldown_seconds
        if isinstance(self.store, RedisSessionStore):
            # Separate namespace: deleting a conversation cannot reset throttling.
            key = f"{self.store.settings.key_prefix}:admin-login:{identity}"
            try:
                retry = int(self.store.client.eval(LOGIN_ATTEMPT, 1, key, limit, cooldown, int(valid)))
            except RedisError:
                raise HTTPException(503, "Admin login temporarily unavailable.") from None
        else:
            # The configured in-memory store is explicitly local/dev only.
            with self.lock:
                now = self.clock()
                self.failures = {k: v for k, v in self.failures.items() if v[1] > now}
                count, expires = self.failures.get(identity, (0, now + cooldown))
                retry = max(1, math.ceil(expires - now)) if count >= limit else 0
                if not retry:
                    if valid:
                        self.failures.pop(identity, None)
                    else:
                        # Bound memory without dropping active lockouts.
                        if identity not in self.failures and len(self.failures) >= 10000:
                            raise HTTPException(429, "Please try again later.", headers={"Retry-After": str(cooldown)})
                        self.failures[identity] = (count + 1, now + cooldown if count + 1 == limit else expires)
        if retry:
            raise HTTPException(429, "Too many login attempts. Please try again later.",
                                headers={"Retry-After": str(retry)})
