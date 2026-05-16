"""
RJB Heating & Plumbing — Auto SMS Responder
=============================================
Sends a tailored SMS to every inbound caller across all 4 Yay.com numbers.
Deploy free on Render.com.

Environment variables required:
    YAY_USERNAME       — your Yay.com login email
    YAY_PASSWORD       — your Yay.com password
    YAY_AUTH_TOKEN     — webhook auth token (from Yay.com webhook page)
    SERVICEM8_API_KEY  — from ServiceM8 → Settings → API

Yay.com setup:
    Voice → Web Hooks → Call Ended → paste your Render URL:
    https://your-app.onrender.com/webhook/call-ended
"""

import os, json, logging, requests
from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

WHATSAPP_NUMBER = '447440416675'

NUMBERS = {
    '+441216306543': {'name': 'Birmingham', 'display': '0121 630 6543', 'brand': 'RJB Heating & Plumbing', 'area': 'Birmingham & West Midlands'},
    '+442080982247': {'name': 'London', 'display': '020 8098 2247', 'brand': 'RJB Heating & Plumbing', 'area': 'London'},
    '+441604969077': {'name': 'Northampton', 'display': '01604 969077', 'brand': 'Northampton Boiler Service', 'area': 'Northampton & Northamptonshire'},
    '+442475906543': {'name': 'Coventry', 'display': '024 7590 6543', 'brand': 'Coventry Boiler Service', 'area': 'Coventry & Warwickshire'},
}

def sms_missed_new(n):
    return (f"Hi, sorry we missed your call! This is {n['brand']}, Gas Safe registered "
            f"engineers covering {n['area']}. Boiler service from £115, CP12 from £95 — "
            f"all-in, no hidden extras. Call {n['display']} or WhatsApp us: "
            f"https://wa.me/{WHATSAPP_NUMBER}")

def sms_missed_existing(n):
    return (f"Hi, sorry we missed your call! This is {n['brand']}. "
            f"We'll call you back shortly. Or reach us on {n['display']} "
            f"or WhatsApp: https://wa.me/{WHATSAPP_NUMBER}")

def sms_answered_new(n):
    return (f"Thanks for calling {n['brand']}! If you need anything else — "
            f"boiler service, CP12 or repairs — call {n['display']} "
            f"or WhatsApp: https://wa.me/{WHATSAPP_NUMBER}")

def sms_answered_existing(n):
    return (f"Thanks for calling {n['brand']}! Great speaking with you. "
            f"Need anything else? Call {n['display']} or WhatsApp: "
            f"https://wa.me/{WHATSAPP_NUMBER}")

def normalise(raw):
    if not raw: return None
    n = raw.strip().replace(' ','').replace('-','')
    if n.startswith('+44'): return n
    if n.startswith('44'): return '+' + n
    if n.startswith('0'): return '+44' + n[1:]
    return n

def is_existing(phone):
    key = os.environ.get('SERVICEM8_API_KEY','')
    if not key: return False
    try:
        r = requests.get('https://api.servicem8.com/api_1.0/client.json',
                         auth=(key,''), params={'%24filter': f"phone eq '{phone}'"}, timeout=5)
        if r.status_code == 200:
            found = len(r.json()) > 0
            logger.info(f'ServiceM8 {phone}: {"EXISTS" if found else "NEW"}')
            return found
    except Exception as e:
        logger.error(f'ServiceM8 error: {e}')
    return False

def send_sms(from_num, to_num, message):
    user = os.environ.get('YAY_USERNAME','')
    pwd = os.environ.get('YAY_PASSWORD','')
    if not user or not pwd:
        logger.error('Yay.com credentials missing')
        return False
    try:
        r = requests.post('https://api.yay.com/voip/text-message/campaign',
                          auth=(user, pwd),
                          json={'from': from_num, 'messages': [{'to': to_num, 'body': message}]},
                          timeout=10)
        if r.status_code in (200, 201):
            logger.info(f'SMS sent {from_num} to {to_num}')
            return True
        logger.error(f'SMS failed {r.status_code}: {r.text}')
    except Exception as e:
        logger.error(f'SMS error: {e}')
    return False

@app.route('/webhook/call-ended', methods=['POST'])
def call_ended():
    expected = os.environ.get('YAY_AUTH_TOKEN','')
    if expected and request.headers.get('X-Auth-Token','') != expected:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json(silent=True) or {}
    logger.info(f'Webhook: {json.dumps(data)}')
    call_type = data.get('call_type','')
    caller_raw = data.get('from','')
    called_raw = data.get('to','')
    answered_by = data.get('answered_by')
    if call_type != 'inbound':
        return jsonify({'status': 'ignored', 'reason': 'outbound'}), 200
    caller = normalise(caller_raw)
    called = normalise(called_raw)
    if not caller or 'withheld' in caller_raw.lower() or caller.startswith('+44800'):
        return jsonify({'status': 'ignored', 'reason': 'no caller id'}), 200
    num = NUMBERS.get(called)
    if not num:
        logger.warning(f'Unknown number: {called}')
        return jsonify({'status': 'ignored', 'reason': 'unknown number'}), 200
    logger.info(f'{caller} to {called} ({num["name"]}) | {"ANSWERED" if answered_by else "MISSED"}')
    existing = is_existing(caller)
    if answered_by and existing: msg = sms_answered_existing(num)
    elif answered_by and not existing: msg = sms_answered_new(num)
    elif not answered_by and existing: msg = sms_missed_existing(num)
    else: msg = sms_missed_new(num)
    ok = send_sms(called, caller, msg)
    return jsonify({'status': 'sent' if ok else 'failed', 'branch': num['name'],
                    'caller': caller, 'answered': bool(answered_by), 'existing': existing,
                    'message': msg}), 200 if ok else 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'time': datetime.utcnow().isoformat()}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
