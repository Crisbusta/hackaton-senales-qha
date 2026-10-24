"""Manda un mensaje al webhook local firmado como lo haria Twilio."""
import re
import sys
import time
import urllib.parse
import urllib.request

from twilio.request_validator import RequestValidator

from app.core.config import get_settings

s = get_settings()
body = sys.argv[1]
numero = sys.argv[2] if len(sys.argv) > 2 else "+56995433931"
params = {"MessageSid": f"SM{int(time.time()*1000)}", "From": f"whatsapp:{numero}",
          "To": "whatsapp:+14155238886", "Body": body, "NumMedia": "0"}
url_firmada = s.public_base_url.rstrip("/") + "/webhooks/whatsapp"
firma = RequestValidator(s.twilio_auth_token).compute_signature(url_firmada, params)
req = urllib.request.Request(
    "http://127.0.0.1:8000/webhooks/whatsapp",
    data=urllib.parse.urlencode(params).encode(),
    headers={"Content-Type": "application/x-www-form-urlencoded", "X-Twilio-Signature": firma})
t0 = time.monotonic()
with urllib.request.urlopen(req, timeout=30) as r:
    xml = r.read().decode()
ms = int((time.monotonic()-t0)*1000)
m = re.search(r"<Message>(.*)</Message>", xml, re.S)
print(f'ella     : "{body}"')
print(f'Contigo  : "{m.group(1) if m else "(TwiML vacio)"}"')
print(f"           [{r.status} · {ms} ms]")
