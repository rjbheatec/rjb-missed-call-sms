import os, json, logging, requests
from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

WHATSAPP_NUMBER = '447440416675'

NUMBERS = {
    '+441216306543': {'name': 'Birmingham', 'display': '0121 630 6543', 'brand': 'RJB Heating & Plumbing', 'website': 'rjbheating.com'},
    '+442080982247': {'name': 'London', 'display': '020 8098 2247', 'brand': 'RJB Heating & Plumbing', 'website': 'rjbheating.com'},
    '+441604969077': {'name': 'Northampton', 'display': '01604 969077', 'brand': 'Northampton Boiler Service', 'website': 'northamptonboilerservice.co.uk'},
    '+442475906543': {'name': 'Coventry', 'display': '024 7590 6543', 'brand': 'Coventry Boiler Service', 'website': 'coventryboilerservice.co.uk'},
}

def get_sms(n):
    return (
        'Hi, thanks for calling ' + n['brand'] + '! We are on hand to help. '
        'Message us anytime on WhatsApp wa.me/' + WHATSAPP_NUMBER +
        ', visit ' + n['website'] + ' to book online, '
        'or call us back on ' + n['display']
    )

def normalise(raw):
    if not raw:
        return None
    n = raw.strip().replace(' ', '').replace('-', '')
    if n.startswith('+44'):
        return n
    if n.startswith('44'):
        return '+' + n
    if n.startswith('0'):
        return '+44' + n[1:]
    return n

def send_sms(from_num, to_num, message):
    user = os.environ.get('YAY_USERNAME', '')
    pwd = os.environ.get('YAY_PASSWORD', '')
    if not user or not pwd:
        logger.error('Yay.com credentials missing')
        return False
    try:
        r = requests.post(
            'https://api.yay.com/voip/text-message/campaign',
            auth=(user, pwd),
            json={'from': from_num, 'messages': [{'to': to_num, 'body': message}]},
            timeout=10
        )
        if r.status_code in (200, 201):
            logger.info('SMS sent to ' + to_num)
            return True
        logger.error('SMS failed ' + str(r.status_code) + ': ' + r.text)
    except Exception as e:
        logger.error('SMS error: ' + str(e))
    return False

@app.route('/webhook/call-ended', methods=['POST'])
def call_ended():
    expected = os.environ.get('YAY_AUTH_TOKEN', '')
    if expected and request.headers.get('X-Auth-Token', '') != expected:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json(silent=True) or {}
    logger.info('Webhook: ' + json.dumps(data))
    call_type = data.get('call_type', '')
    from_type = data.get('from_type', '')
    caller_raw = data.get('from', '')
    called_raw = data.get('to', '')
    if call_type != 'inbound':
        return jsonify({'status': 'ignored', 'reason': 'outbound'}), 200
    if from_type == 'sipuser':
        return jsonify({'status': 'ignored', 'reason': 'internal sip user'}), 200
    caller = normalise(caller_raw)
    called = normalise(called_raw)
    if not caller or 'withheld' in caller_raw.lower() or caller.startswith('+44800'):
        return jsonify({'status': 'ignored', 'reason': 'no caller id'}), 200
    num = NUMBERS.get(called)
    if not num:
        logger.warning('Unknown number: ' + str(called))
        return jsonify({'status': 'ignored', 'reason': 'unknown number'}), 200
    logger.info('Inbound: ' + str(caller) + ' to ' + str(called))
    message = get_sms(num)
    ok = send_sms(called, caller, message)
    return jsonify({'status': 'sent' if ok else 'failed', 'branch': num['name'], 'message': message}), 200 if ok else 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'time': datetime.utcnow().isoformat()}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))