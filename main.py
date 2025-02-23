# main.py
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer
from bot import app
from config import PORT

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'OK')

def run_health_server():
    server = HTTPServer(('0.0.0.0', PORT), HealthHandler)
    server.serve_forever()

if __name__ == "__main__":
    health_thread = Thread(target=run_health_server, daemon=True)
    health_thread.start()
    app.run()
