import os, json, logging, requests, time
from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

# =============================================================================
# Config
# =============================================================================
WHATSAPP_NUMBER = '447440416675'
THIRTY_DAYS = 30 * 24 * 60 * 60
sms_sent = {}  # in-memory dedupe: { from_number: last_sent_epoch }

# Inbound RJB numbers → outbound brand details
# 'caller_id_uuid' is filled in by lookup_caller_ids() at first use
NUMBERS = {
    '+441216306543': {
        'name': 'Birmingham',
        'display': '0121 630 6543',
        'brand': 'RJB Heating & Plumbing',
        'website': 'rjbheating.com',
        'caller_id_uuid': None,
    },
    '+442080982247': {
        'name': 'London',
        'display': '020 8098 2247',
        'brand': 'RJB Heating & Plumbing',
        'website': 'rjbheating.com',
        'caller_id_uuid': None,
    },
    '+441604969077': {
        'name': 'Northampton',
        'display': '01604 969077',
        'brand': 'Northampton Boiler Service',
        'website': 'northamptonboilerservice.co.uk',
        'caller_id_uuid': None,
        'short_sms': True,
    },
    '+442475906543': {
        'name': 'Coventry',
        'display': '024 7590 6543',
        'brand': 'Coventry Boiler Service',
        'website': 'coventryboilerservice.co.uk',
        'caller_id_uuid': None,
        'short_sms': True,
    },
}

# =============================================================================
# Yay.com API
# =============================================================================
YAY_BASE = 'https://api.yay.com'
YAY_HEADERS = {
    'X-Auth-Reseller': os.environ.get('YAY_RESELLER', ''),
    'X-Auth-User': os.environ.get('YAY_USERNAME', ''),
    'X-Auth-Password': os.environ.get('YAY_PASSWORD', ''),
    'User-Agent': 'RJB-MissedCallSMS/1.0',
    'Content-Type': 'application/json',
}

_caller_ids_loaded = False

def lookup_caller_ids():
    """Fetch all caller IDs from Yay and map them onto our NUMBERS dict."""
    global _caller_ids_loaded
    try:
        r = requests.get(f'{YAY_BASE}/voip/caller-id', headers=YAY_HEADERS, timeout=10)
        logger.info(f'caller-id lookup status={r.status_code}')
        if r.status_code != 200:
            logger.error(f'caller-id lookup failed: {r.text[:300]}')
            return False
        data = r.json()
        # response shape: { "result": [ { "uuid": "...", "phone_number": "+44..." }, ... ] }
        items = data.get('result', data) if isinstance(data, dict) else data
        if isinstance(items, dict) and 'result' in items:
            items = items['result']
        for item in items:
            num = item.get('cli_display') or item.get('cli_name') or item.get('phone_number') or item.get('number')
            uuid = item.get('uuid') or item.get('id')
            if not num or not uuid:
                continue
            # normalise: match on last 10 digits (covers +44/0044/0 formats)
            digits_only = ''.join(c for c in num if c.isdigit())
            last10 = digits_only[-10:] if len(digits_only) >= 10 else digits_only
            logger.info(f"yay returned num={num} -> digits={digits_only} last10={last10} uuid={uuid}")
            for key in NUMBERS:
                key_digits = ''.join(c for c in key if c.isdigit())
                key_last10 = key_digits[-10:] if len(key_digits) >= 10 else key_digits
                if key_last10 == last10:
                    NUMBERS[key]['caller_id_uuid'] = uuid
                    logger.info(f"matched {key} -> {uuid}")
                    break
        _caller_ids_loaded = True
        return True
    except Exception as e:
        logger.exception(f'lookup_caller_ids error: {e}')
        return False


def send_sms_via_yay(caller_id_uuid, to_number, message):
    """Two-step send: create campaign (is_draft=false) then /confirm."""
    # Step 1: create the campaign
    payload = {
        'is_draft': False,
        'campaign_name': f'Missed call reply {datetime.utcnow().strftime("%Y%m%d-%H%M%S")}',
        'message_content': message,
        'caller_id_uuid': caller_id_uuid,
        'recipients': [{'phone_number': to_number}],
    }
    try:
        r = requests.post(
            f'{YAY_BASE}/voip/text-message/campaign',
            headers=YAY_HEADERS,
            json=payload,
            timeout=15,
        )
        logger.info(f'campaign create status={r.status_code}')
        if r.status_code not in (200, 201):
            logger.error(f'campaign create failed: {r.text[:500]}')
            return False, f'create {r.status_code}: {r.text[:200]}'
        body = r.json()
        result = body.get('result', body)
        campaign_uuid = result.get('uuid')
        if not campaign_uuid:
            logger.error(f'no uuid in campaign response: {body}')
            return False, 'no uuid in response'

        # Step 2: confirm to actually queue for sending
        c = requests.post(
            f'{YAY_BASE}/voip/text-message/campaign/{campaign_uuid}/confirm',
            headers=YAY_HEADERS,
            timeout=15,
        )
        logger.info(f'campaign confirm status={c.status_code}')
        if c.status_code not in (200, 201, 204):
            logger.error(f'confirm failed: {c.text[:500]}')
            return False, f'confirm {c.status_code}: {c.text[:200]}'

        return True, campaign_uuid
    except Exception as e:
        logger.exception(f'send_sms_via_yay error: {e}')
        return False, str(e)


# =============================================================================
# Routes
# =============================================================================
@app.route('/', methods=['GET'])
def health():
    return jsonify({
        'status': 'ok',
        'caller_ids_loaded': _caller_ids_loaded,
        'numbers': {k: v['caller_id_uuid'] for k, v in NUMBERS.items()},
    })


@app.route('/refresh', methods=['POST', 'GET'])
def refresh():
    ok = lookup_caller_ids()
    return jsonify({'refreshed': ok, 'numbers': {k: v['caller_id_uuid'] for k, v in NUMBERS.items()}})


@app.route('/debug', methods=['GET'])
def debug():
    try:
        r = requests.get(f'{YAY_BASE}/voip/caller-id', headers=YAY_HEADERS, timeout=10)
        return jsonify({
            'status': r.status_code,
            'raw': r.text[:3000],
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/test_send', methods=['GET'])
def test_send():
    """Manual test: try sending an SMS using a specific caller_id_uuid.
    Usage: /test_send?uuid=<caller_id_uuid>&to=<phone_number>
    Returns the raw request + response so we can see exactly what Yay says.
    """
    uuid = request.args.get('uuid', '').strip()
    to = request.args.get('to', '').strip()
    if not uuid or not to:
        return jsonify({
            'error': 'usage',
            'example': '/test_send?uuid=85bfe409-a21e-4233-a9f3-661c07cbfabb&to=+447xxxxxxxxx',
            'available_uuids': {k: v['caller_id_uuid'] for k, v in NUMBERS.items()},
        }), 400
    to_clean = '+' + ''.join(c for c in to if c.isdigit())
    payload = {
        'is_draft': False,
        'campaign_name': f'Test send {datetime.utcnow().strftime("%Y%m%d-%H%M%S")}',
        'message_content': 'API test message — please ignore',
        'caller_id_uuid': uuid,
        'recipients': [{'phone_number': to_clean}],
    }
    try:
        r = requests.post(
            f'{YAY_BASE}/voip/text-message/campaign',
            headers=YAY_HEADERS,
            json=payload,
            timeout=15,
        )
        return jsonify({
            'request': {
                'url': f'{YAY_BASE}/voip/text-message/campaign',
                'method': 'POST',
                'headers_used': {
                    'X-Auth-Reseller': YAY_HEADERS.get('X-Auth-Reseller', '')[:3] + '***',
                    'X-Auth-User': YAY_HEADERS.get('X-Auth-User', '')[:4] + '***',
                    'X-Auth-Password': '***',
                    'User-Agent': YAY_HEADERS.get('User-Agent'),
                    'Content-Type': YAY_HEADERS.get('Content-Type'),
                },
                'payload': payload,
            },
            'response': {
                'status': r.status_code,
                'body': r.text[:3000],
            },
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/webhook', methods=['POST'])
def webhook():
    """ServiceM8 / Kira webhook on inbound/missed call.
    Expected JSON: { "from": "+447...", "to": "+44121..." }
    Both can also be passed as form fields.
    """
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    from_number = data.get('from') or data.get('caller') or data.get('caller_number')
    to_number = data.get('to') or data.get('called') or data.get('called_number')

    logger.info(f'webhook from={from_number} to={to_number}')

    if not from_number or not to_number:
        return jsonify({'error': 'missing from/to'}), 400

    # Skip outbound calls — only SMS inbound customer calls
    from_type = data.get('from_type', '')
    call_type = data.get('call_type', '')
    if from_type == 'sipuser' or call_type == 'outbound':
        logger.info(f'skip outbound: from_type={from_type} call_type={call_type}')
        return jsonify({'status': 'skipped', 'reason': 'outbound call'}), 200

    # normalise
    from_clean = '+' + ''.join(c for c in from_number if c.isdigit())
    to_clean = '+' + ''.join(c for c in to_number if c.isdigit())

    # dedupe — don't text same caller for same brand twice within 30 days
    now = time.time()
    dedupe_key = (from_clean, to_clean)
    last = sms_sent.get(dedupe_key, 0)
    if now - last < THIRTY_DAYS:
        logger.info(f'skip {from_clean} for {to_clean} — texted {int((now-last)/3600)}h ago')
        return jsonify({'status': 'skipped', 'reason': 'recently texted for this brand'}), 200

    # find which brand the customer called
    if to_clean not in NUMBERS:
        logger.warning(f'unknown inbound {to_clean}')
        return jsonify({'error': f'unknown inbound number {to_clean}'}), 400

    brand = NUMBERS[to_clean]

    # lazy-load caller IDs once
    if not brand.get('caller_id_uuid'):
        lookup_caller_ids()
    caller_id_uuid = brand.get('caller_id_uuid')
    if not caller_id_uuid:
        logger.error(f'no caller_id_uuid for {to_clean} after lookup')
        return jsonify({'error': 'caller_id lookup failed'}), 500

    # compose message — shorter version for brands with longer names/URLs
    if brand.get('short_sms'):
        message = (
            f"Thanks for contacting {brand['brand']}. "
            f"WhatsApp us: https://wa.me/{WHATSAPP_NUMBER} "
            f"or visit {brand['website']}"
        )
    else:
        message = (
            f"Hi, thanks for contacting {brand['brand']}. "
            f"We'll be in touch shortly. Want to chat? "
            f"WhatsApp: https://wa.me/{WHATSAPP_NUMBER} "
            f"or see pricing at {brand['website']}"
        )

    ok, info = send_sms_via_yay(caller_id_uuid, from_clean, message)
    if ok:
        sms_sent[dedupe_key] = now
        return jsonify({'status': 'sent', 'campaign_uuid': info}), 200
    return jsonify({'status': 'failed', 'error': info}), 500


if __name__ == '__main__':
    # try caller ID lookup at startup but don't crash if it fails
    lookup_caller_ids()
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
