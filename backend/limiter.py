from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


def client_ip(request: Request) -> str:
    # Behind Render's proxy, X-Forwarded-For is "<whatever the client sent>, <real ip>":
    # the proxy APPENDS the real address, so only the LAST entry is trustworthy.
    # (uvicorn's --proxy-headers uses the first entry, which a client can forge to
    # get a fresh rate-limit bucket per request.) Without the header — local dev,
    # the check script — fall back to the socket address.
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        last = forwarded.rsplit(",", 1)[-1].strip()
        if last:
            return last
    return get_remote_address(request)


limiter = Limiter(key_func=client_ip)
