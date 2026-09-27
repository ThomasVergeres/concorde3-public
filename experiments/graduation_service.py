"""Editable inherited product, not a hidden evaluator or Concorde instruction."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def result(domain, data):
    if domain == 'operations':
        records = data.get('records')
        if not isinstance(records, list) or not records:
            raise ValueError('records must be a nonempty list')
        if any(not isinstance(r, dict) or type(r.get('amount')) is not int for r in records):
            raise ValueError('Every record needs a confirmed integer amount')
        return {'records': records, 'total_amount': sum(r['amount'] for r in records)}
    if domain == 'developer':
        events = data.get('events')
        if not isinstance(events, list) or not events:
            raise ValueError('events must be a nonempty list')
        out = []
        seen = set()
        for e in events:
            if not isinstance(e, dict) or not isinstance(e.get('id'), str) or not e['id']:
                raise ValueError('Each event needs a nonempty string id')
            if e['id'] not in seen:
                out.append(e); seen.add(e['id'])
        return {'events': out, 'duplicates': len(events)-len(out)}
    minutes = data.get('minutes', 5)
    if type(minutes) is not int or minutes < 1:
        raise ValueError('minutes must be a positive integer')
    # A small service with honest limits; owners may replace it completely.
    activities = [
        ('A different use', 'Pick one nearby everyday object. Invent three harmless imaginary uses for it. Choose the strangest and write one sentence explaining how it works. Stop whenever you like; no materials or sharing needed.'),
        ('Two details', 'Choose a familiar place from memory. Write two concrete details, then change one to something impossible. Describe what happens next in three sentences. This is an open-ended writing prompt, not a scored task.'),
        ('Tiny dialogue', 'Imagine a key and a door disagree about where they belong. Give each two lines of dialogue. Choose whether they reach an agreement. You can pause at any point; nothing needs to be posted or uploaded.'),
    ]
    index = data.get('visit', 0)
    if type(index) is not int or index < 0:
        raise ValueError('visit must be a nonnegative integer')
    title, text = activities[index % len(activities)]
    return {'title': title, 'text': text, 'format': 'text', 'suggested_minutes': min(minutes, 5),
            'catalog_size': len(activities), 'privacy': 'No user submissions or account data stored'}


def handler(domain):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, data):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers(); self.wfile.write(body)

        def do_GET(self):
            if self.path == '/health':
                self.reply(200, {'up': True, 'domain': domain})
            elif self.path == '/':
                self.reply(200, {'domain': domain, 'method': 'POST', 'path': '/v1/process',
                                'limits': 'Small inherited service; support through the ordinary world mailbox'})
            else:
                self.reply(404, {'error': 'Unknown route'})

        def do_POST(self):
            if self.path != '/v1/process':
                return self.reply(404, {'error': 'Unknown route'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 65536:
                    raise ValueError('Request length must be 1..65536 bytes')
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError('JSON object required')
                self.reply(200, result(domain, data))
            except (ValueError, TypeError) as e:
                self.reply(422, {'error': str(e)})
    return Handler


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('domain', choices=['operations','developer','consumer'])
    p.add_argument('--port', type=int, default=8000)
    a = p.parse_args()
    ThreadingHTTPServer(('0.0.0.0', a.port), handler(a.domain)).serve_forever()
