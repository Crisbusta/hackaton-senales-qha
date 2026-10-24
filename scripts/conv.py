"""Una conversacion completa contra el webhook local, firmada como Twilio."""
import re
import sys
import time
import urllib.parse
import urllib.request

from twilio.request_validator import RequestValidator

from app.core.config import get_settings

s = get_settings()
numero = "+56995433931"
val = RequestValidator(s.twilio_auth_token)

def turno(body):
    params = {"MessageSid": f"SM{int(time.time()*1000000)}", "From": f"whatsapp:{numero}",
              "To": "whatsapp:+14155238886", "Body": body, "NumMedia": "0"}
    url = s.public_base_url.rstrip("/") + "/webhooks/whatsapp"
    req = urllib.request.Request(
        "http://127.0.0.1:8000/webhooks/whatsapp",
        data=urllib.parse.urlencode(params).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "X-Twilio-Signature": val.compute_signature(url, params)})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=40) as r:
        xml = r.read().decode()
    ms = int((time.monotonic()-t0)*1000)
    m = re.search(r"<Message>(.*)</Message>", xml, re.S)
    print(f'  ella    : {body}')
    print(f'  Contigo : {m.group(1) if m else "(TwiML vacio — sin respuesta)"}')
    print(f'            [{ms} ms]\n')

for m in sys.argv[1:]:
    turno(m)
