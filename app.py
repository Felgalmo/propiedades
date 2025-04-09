from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
import CoolProp.CoolProp as CP

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

@app.route('/refrigerants', methods=['GET'])
def get_refrigerants():
    return jsonify({'status': 'success', 'refrigerants': CP.FluidsList()})

@app.route('/')
def serve_index():
    return send_from_directory('.', 'index.html')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
